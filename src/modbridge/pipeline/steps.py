"""The pipeline steps, in execution order.

Every step re-checks reality instead of trusting earlier runs, which makes the
whole pipeline idempotent: a crash at any point is healed by simply running again
(the update is a no-op, the server gets started if down, and the commit only
happens if the published manifest differs from disk).
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from modbridge.adapters.sakura_config import sakura_config_synced, write_sakura_config
from modbridge.changelog.renderer import render_changelog
from modbridge.domain.models import ChangeSet, ModsManifest, diff_manifests
from modbridge.mods.scanner import scan_mods_dir
from modbridge.pipeline.context import RunContext, StepResult

log = logging.getLogger(__name__)


def step_preflight(ctx: RunContext) -> StepResult:
    window = ctx.config.schedule.parsed_window()
    gated = window and not ctx.options.force and not ctx.options.dry_run
    if gated and window and not window.contains(ctx.now()):
        return StepResult.done_early(
            f"Outside update window {window} (now {ctx.now():%H:%M}); use --force to override"
        )
    if not ctx.config.mods_dir.is_dir():
        ctx.warn(f"Mods directory missing: {ctx.config.mods_dir}")
    return StepResult.ok()


def step_dependencies(ctx: RunContext) -> StepResult:
    """Install/update the maintainer jar and the SakuraUpdater mod from GitHub.

    Runs before the tooling check so a fresh setup bootstraps itself. A new
    SakuraUpdater jar lands in mods/, so the normal manifest diff publishes it
    to players like any other mod change.
    """
    deps_cfg = ctx.config.dependencies
    if ctx.deps is None or not (deps_cfg.auto_install or deps_cfg.auto_update):
        return StepResult.skipped("dependency management disabled")
    if ctx.options.dry_run:
        return StepResult.skipped("dry run: not touching dependencies")
    try:
        actions = ctx.deps.ensure_all(update=deps_cfg.auto_update)
    except Exception as exc:
        # Only fatal when the maintainer jar is still missing; otherwise keep
        # going with the currently installed versions.
        if not ctx.config.maintainer_jar.is_file():
            return StepResult.failed(f"Could not install maintainer jar: {exc}")
        ctx.warn(f"Dependency update check failed: {exc}")
        return StepResult.ok("kept currently installed versions")
    return StepResult.ok("; ".join(actions) or "everything up to date")


def step_tooling(ctx: RunContext) -> StepResult:
    problems = ctx.updater.preflight()
    if problems:
        return StepResult.failed("Tooling check failed: " + "; ".join(problems))
    return StepResult.ok()


def step_snapshot(ctx: RunContext) -> StepResult:
    ctx.pre_manifest = scan_mods_dir(ctx.config.mods_dir)
    ctx.mc_version_before = _mc_version(ctx)
    return StepResult.ok(f"{len(ctx.pre_manifest.mods)} mods on disk")


def step_plan(ctx: RunContext) -> StepResult:
    assert ctx.pre_manifest is not None
    ctx.plan = ctx.updater.plan()
    ctx.needs_update = ctx.plan.has_changes

    committed = ctx.state.last_committed_manifest
    manual_changes = (
        committed is not None and committed.content_hash() != ctx.pre_manifest.content_hash()
    )
    never_committed = committed is None
    # SakuraUpdater's own config drifting from modbridge.yaml also forces a
    # maintenance cycle: the file must be rewritten while the server is down.
    config_drift = ctx.config.sakura.manage_config and not sakura_config_synced(ctx.config)

    if ctx.options.dry_run:
        lines = [f"  {c.name}: {c.old_version} -> {c.new_version}" for c in ctx.plan.changes]
        planned = "\n".join(lines) if lines else "  (none)"
        return StepResult.done_early(
            f"Dry run. Planned updates:\n{planned}\n"
            f"Unpublished manual changes: {'yes' if manual_changes else 'no'}\n"
            f"Initial commit pending: {'yes' if never_committed else 'no'}\n"
            f"SakuraUpdater config in sync: {'no (will be rewritten)' if config_drift else 'yes'}"
        )

    if not ctx.needs_update and not manual_changes and not never_committed and not config_drift:
        return StepResult.done_early("No updates available and nothing unpublished")

    # A restart is needed unless the running server already loaded exactly the
    # mod set on disk (typical crash-recovery re-run: update done, commit missing).
    server_runs_current = (
        ctx.supervisor.is_server_running()
        and ctx.state.last_started_manifest_hash == ctx.pre_manifest.content_hash()
    )
    if ctx.needs_update or config_drift:
        ctx.needs_restart = True
    elif manual_changes or never_committed:
        ctx.needs_restart = ctx.config.restart_on_manual_changes and not server_runs_current

    plan_desc = ", ".join(f"{c.name} {c.old_version}->{c.new_version}" for c in ctx.plan.changes)
    return StepResult.ok(plan_desc or "no upstream updates; publishing local state")


def step_countdown(ctx: RunContext) -> StepResult:
    cfg = ctx.config.maintenance
    if not ctx.needs_restart or not ctx.supervisor.is_server_running():
        return StepResult.skipped("no restart needed or server not running")
    if ctx.options.skip_countdown or not cfg.countdown:
        return StepResult.skipped("countdown disabled")
    if cfg.skip_if_empty:
        players = ctx.supervisor.online_players()
        if players == 0:
            return StepResult.skipped("server is empty")
    steps = list(cfg.countdown)
    for current, nxt in zip(steps, [*steps[1:], 0], strict=True):
        ctx.supervisor.say(cfg.message.format(s=current))
        ctx.sleep(current - nxt)
    return StepResult.ok(f"warned players over {steps[0]}s")


def step_stop(ctx: RunContext) -> StepResult:
    if not ctx.needs_restart:
        return StepResult.skipped("no restart needed")
    if not ctx.supervisor.is_server_running():
        return StepResult.skipped("server already stopped")
    if not ctx.supervisor.stop(ctx.config.server.stop_timeout):
        return StepResult.failed("Server did not stop in time; aborting before any file changes")
    ctx.stopped_server = True
    return StepResult.ok()


def step_configure(ctx: RunContext) -> StepResult:
    """Write SakuraUpdater's real config file (sakuraupdater-common.toml) from
    modbridge.yaml. Runs after `stop`, so the mod loads it fresh on startup."""
    if not ctx.config.sakura.manage_config:
        return StepResult.skipped("sakura.manage_config disabled")
    if sakura_config_synced(ctx.config):
        return StepResult.skipped("SakuraUpdater config already in sync")
    path = write_sakura_config(ctx.config)
    return StepResult.ok(f"wrote {path.name}")


def step_update(ctx: RunContext) -> StepResult:
    if not ctx.needs_update:
        return StepResult.skipped("no upstream updates")
    log.info("Running Minecraft Server Maintainer update…")
    result = ctx.updater.update()
    ctx.update_result = result
    if result.success:
        applied = ", ".join(f"{c.name} {c.old_version}->{c.new_version}" for c in result.applied)
        return StepResult.ok(applied or "maintainer reported no applied changes")
    detail = "; ".join(result.errors) or f"maintainer exited {result.exit_code}"
    if result.rolled_back:
        detail = f"update failed and was rolled back by the maintainer: {detail}"
    return StepResult.failed(detail)


def step_rescan(ctx: RunContext) -> StepResult:
    ctx.post_manifest = scan_mods_dir(ctx.config.mods_dir)
    ctx.mc_version_after = _mc_version(ctx)
    return StepResult.ok(f"{len(ctx.post_manifest.mods)} mods on disk")


def step_start(ctx: RunContext) -> StepResult:
    assert ctx.post_manifest is not None
    if ctx.supervisor.is_server_running():
        return StepResult.skipped("server already running")
    ctx.supervisor.start()
    if not ctx.supervisor.wait_ready(ctx.config.server.startup_timeout):
        return _recover_failed_start(ctx)
    ctx.state.last_started_manifest_hash = ctx.post_manifest.content_hash()
    ctx.store.save(ctx.state)
    return StepResult.ok()


def _recover_failed_start(ctx: RunContext) -> StepResult:
    """The server did not come up after an update.

    Try a maintainer rollback, then one more start. The rollback is often
    unavailable by design (the maintainer only creates backups before Minecraft
    version updates, not before mod updates) — in that case a successful retry
    with the updated mods is a *good* outcome and the pipeline continues.
    """
    if ctx.update_result is None or not ctx.update_result.success:
        return StepResult.failed("Server failed to start (no update was applied this run)")
    log.error("Server failed to start after update; attempting maintainer rollback")
    rolled_back = ctx.updater.rollback()
    if not rolled_back:
        log.warning("No rollback available (expected for mod-only updates); retrying start")
    ctx.post_manifest = scan_mods_dir(ctx.config.mods_dir)
    ctx.supervisor.start()
    came_up = ctx.supervisor.wait_ready(ctx.config.server.startup_timeout)
    if came_up:
        ctx.state.last_started_manifest_hash = ctx.post_manifest.content_hash()
        ctx.store.save(ctx.state)
    if rolled_back:
        if came_up:
            return StepResult.failed(
                "Update broke server startup; rolled back to previous state and restarted. "
                "No update was published."
            )
        return StepResult.failed(
            "Server failed to start even after rollback — manual intervention needed"
        )
    if came_up:
        return StepResult.ok(
            "first readiness check failed, but the server started on retry with the "
            "updated mods (no rollback was available)"
        )
    return StepResult.failed(
        "Server failed to start after update and no backup was available to roll back — "
        "manual intervention needed"
    )


def step_changelog(ctx: RunContext) -> StepResult:
    assert ctx.post_manifest is not None
    baseline = ctx.state.last_committed_manifest
    if baseline is not None and baseline.content_hash() == ctx.post_manifest.content_hash():
        return StepResult.done_early("Published state already matches disk; nothing to commit")

    # Diff against what players currently have, so manual changes since the last
    # commit are included; on the very first run, diff this run's update only.
    changeset = diff_manifests(baseline or _require_pre(ctx), ctx.post_manifest)
    changeset = ChangeSet(
        changes=changeset.changes,
        minecraft_old=ctx.mc_version_before,
        minecraft_new=ctx.mc_version_after,
        extra_notes=tuple(ctx.warnings),
    )
    if changeset.is_empty and baseline is not None:
        return StepResult.done_early("No effective changes; nothing to commit")

    if ctx.config.changelog.modrinth_enrichment:
        from modbridge.changelog.modrinth import enrich_changeset

        changeset = enrich_changeset(changeset, ctx.config.mods_dir)

    ctx.changeset = changeset
    ctx.version = ctx.distributor.next_version()
    ctx.changelog_text = render_changelog(
        changeset,
        version=ctx.version,
        title_format=ctx.config.changelog.title,
        now=ctx.now(),
        template_path=ctx.config.changelog.template,
    )
    ctx.store.ensure_dirs()
    ctx.changelog_path = ctx.store.changelog_dir / f"{ctx.version}.md"
    ctx.changelog_path.write_text(ctx.changelog_text, encoding="utf-8")
    return StepResult.ok(f"version {ctx.version}: {changeset.summary()}")


def step_commit(ctx: RunContext) -> StepResult:
    assert ctx.version is not None and ctx.changelog_path is not None
    grace = ctx.config.sakura.startup_grace
    if not ctx.distributor.is_healthy(retries=max(1, int(grace / 2)), delay=2.0):
        return StepResult.failed(
            "SakuraUpdater HTTP endpoint is not responding; is the mod installed and the "
            f"port ({ctx.config.sakura.port}) correct? Commit skipped."
        )
    if not ctx.distributor.commit(ctx.version, ctx.changelog_path):
        return StepResult.failed(f"SakuraUpdater commit {ctx.version} could not be verified")

    assert ctx.post_manifest is not None
    ctx.state.last_committed_version = ctx.version
    ctx.state.last_committed_at = ctx.now().isoformat(timespec="seconds")
    ctx.state.last_committed_manifest = ctx.post_manifest
    ctx.store.save(ctx.state)
    return StepResult.ok(f"committed and verified {ctx.version}")


def _mc_version(ctx: RunContext) -> str | None:
    getter = getattr(ctx.updater, "current_minecraft_version", None)
    return getter() if callable(getter) else None


def _require_pre(ctx: RunContext) -> ModsManifest:
    assert ctx.pre_manifest is not None
    return ctx.pre_manifest


PIPELINE: list[tuple[str, Callable[[RunContext], StepResult]]] = [
    ("preflight", step_preflight),
    ("dependencies", step_dependencies),
    ("tooling", step_tooling),
    ("snapshot", step_snapshot),
    ("plan", step_plan),
    ("countdown", step_countdown),
    ("stop", step_stop),
    ("configure", step_configure),
    ("update", step_update),
    ("rescan", step_rescan),
    ("start", step_start),
    ("changelog", step_changelog),
    ("commit", step_commit),
]
