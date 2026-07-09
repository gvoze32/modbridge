"""End-to-end pipeline scenarios against fake adapters and a real tmp filesystem."""

from __future__ import annotations

from pathlib import Path

from modbridge.adapters.base import PlannedChange, UpdatePlan, UpdateResult
from modbridge.mods.scanner import scan_mods_dir
from modbridge.pipeline.context import RunOptions
from modbridge.pipeline.engine import PipelineEngine
from modbridge.state.store import StateStore
from tests.conftest import make_config, make_context, make_fabric_jar
from tests.fakes import FakeDistributor, FakeNotifier, FakeSupervisor, FakeUpdater

PLAN_ONE = UpdatePlan(changes=(PlannedChange("Sodium", "0.5.0", "0.6.0"),))


def committed_state_for(config, server_dir: Path) -> None:
    """Persist state as if the current mods dir was already published and started."""
    store = StateStore(config.state_dir)
    state = store.load()
    manifest = scan_mods_dir(server_dir / "mods")
    state.last_committed_manifest = manifest
    state.last_committed_version = "2026.07.01"
    state.last_started_manifest_hash = manifest.content_hash()
    store.save(state)


def test_nothing_to_do(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    committed_state_for(config, server_dir)

    updater = FakeUpdater(plan=UpdatePlan())
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    notifier = FakeNotifier()
    ctx = make_context(config, updater, supervisor, distributor, notifier)

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert outcome.committed_version is None
    assert updater.update_called == 0
    assert supervisor.stop_calls == 0
    assert distributor.versions == []


def test_update_and_commit_happy_path(server_dir: Path) -> None:
    jar = make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    committed_state_for(config, server_dir)

    def apply_update() -> None:
        make_fabric_jar(jar, "sodium", "0.6.0")

    updater = FakeUpdater(plan=PLAN_ONE, on_update=apply_update)
    supervisor = FakeSupervisor(running=True, players=2)
    distributor = FakeDistributor()
    notifier = FakeNotifier()
    ctx = make_context(config, updater, supervisor, distributor, notifier)

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert outcome.committed_version == "2026.07.09-1"
    # Full sequence: warned, stopped, updated, restarted, committed.
    assert len(supervisor.said) == 2  # countdown [3, 1]
    assert supervisor.stop_calls == 1
    assert updater.update_called == 1
    assert supervisor.start_calls == 1
    assert distributor.versions == ["2026.07.09-1"]
    # Changelog was rendered with the version diff.
    changelog = distributor.committed_changelogs[0].read_text()
    assert "0.5.0" in changelog and "0.6.0" in changelog
    # State now records the publish; a second run is a no-op.
    ctx2 = make_context(config, FakeUpdater(), supervisor, distributor, notifier)
    outcome2 = PipelineEngine(ctx2).run()
    assert outcome2.success and outcome2.committed_version is None
    assert distributor.versions == ["2026.07.09-1"]
    # Success notification was sent.
    assert any(n.success for n in notifier.sent)


def test_failed_update_never_commits_and_restarts_server(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    committed_state_for(config, server_dir)

    updater = FakeUpdater(
        plan=PLAN_ONE,
        result=UpdateResult(success=False, rolled_back=True, errors=("Download failed",)),
    )
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    notifier = FakeNotifier()
    ctx = make_context(config, updater, supervisor, distributor, notifier)

    outcome = PipelineEngine(ctx).run()
    assert not outcome.success
    assert distributor.versions == []
    # The engine restarted the server it had stopped.
    assert supervisor.start_calls == 1
    assert supervisor.running
    assert any(not n.success for n in notifier.sent)


def test_manual_changes_are_published(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    committed_state_for(config, server_dir)
    # Admin drops in a new mod by hand; no upstream updates available.
    make_fabric_jar(server_dir / "mods" / "lithium.jar", "lithium", "1.0.0")

    updater = FakeUpdater(plan=UpdatePlan())
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    ctx = make_context(config, updater, supervisor, distributor, FakeNotifier())

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert distributor.versions  # committed
    assert updater.update_called == 0
    assert supervisor.stop_calls == 1  # restart still required before publishing
    changelog = distributor.committed_changelogs[0].read_text()
    assert "Lithium" in changelog


def test_schedule_window_blocks_and_force_bypasses(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir, schedule={"window": "10:00-11:00"})  # now is 04:30

    updater = FakeUpdater(plan=PLAN_ONE)
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()

    ctx = make_context(config, updater, supervisor, distributor, FakeNotifier())
    outcome = PipelineEngine(ctx).run()
    assert outcome.success and "window" in outcome.message
    assert updater.update_called == 0

    ctx_force = make_context(
        config, updater, supervisor, distributor, FakeNotifier(),
        options=RunOptions(force=True),
    )
    outcome_force = PipelineEngine(ctx_force).run()
    assert outcome_force.success
    assert updater.update_called == 1


def test_dry_run_changes_nothing(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)

    updater = FakeUpdater(plan=PLAN_ONE)
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    ctx = make_context(
        config, updater, supervisor, distributor, FakeNotifier(),
        options=RunOptions(dry_run=True),
    )
    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert "Sodium" in outcome.message
    assert updater.update_called == 0
    assert supervisor.stop_calls == 0
    assert distributor.versions == []


def test_startup_failure_triggers_rollback_and_no_commit(server_dir: Path) -> None:
    jar = make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)
    committed_state_for(config, server_dir)

    def apply_update() -> None:
        make_fabric_jar(jar, "sodium", "0.6.0-broken")

    def apply_rollback() -> None:
        make_fabric_jar(jar, "sodium", "0.5.0")

    updater = FakeUpdater(plan=PLAN_ONE, on_update=apply_update, on_rollback=apply_rollback)
    supervisor = FakeSupervisor(running=True, ready_ok=False)
    distributor = FakeDistributor()
    notifier = FakeNotifier()
    ctx = make_context(config, updater, supervisor, distributor, notifier)

    # First wait_ready fails -> rollback -> second start; make the retry succeed.
    original_wait = supervisor.wait_ready

    def wait_ready_once_failing(timeout: float) -> bool:
        if updater.rollback_called:
            supervisor.ready_ok = True
        return original_wait(timeout)

    supervisor.wait_ready = wait_ready_once_failing  # type: ignore[method-assign]

    outcome = PipelineEngine(ctx).run()
    assert not outcome.success
    assert updater.rollback_called == 1
    assert distributor.versions == []
    assert supervisor.running  # recovered on the old mod set
    assert any(not n.success for n in notifier.sent)


def test_first_run_commits_initial_version(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)

    updater = FakeUpdater(plan=UpdatePlan())
    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    ctx = make_context(config, updater, supervisor, distributor, FakeNotifier())

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert distributor.versions == ["2026.07.09-1"]


def test_unhealthy_distributor_fails_commit(server_dir: Path) -> None:
    make_fabric_jar(server_dir / "mods" / "sodium.jar", "sodium", "0.5.0")
    config = make_config(server_dir)

    distributor = FakeDistributor(healthy=False)
    supervisor = FakeSupervisor(running=True)
    ctx = make_context(config, FakeUpdater(), supervisor, distributor, FakeNotifier())

    outcome = PipelineEngine(ctx).run()
    assert not outcome.success
    assert "SakuraUpdater" in outcome.message
    assert distributor.versions == []


def test_crash_recovery_skips_restart_when_server_runs_current_mods(server_dir: Path) -> None:
    """Update done + server started with new mods, but commit crashed: re-run must
    commit without another restart."""
    jar = server_dir / "mods" / "sodium.jar"
    config = make_config(server_dir)
    store = StateStore(config.state_dir)
    state = store.load()
    # Players last received 0.5.0 …
    make_fabric_jar(jar, "sodium", "0.5.0")
    state.last_committed_manifest = scan_mods_dir(server_dir / "mods")
    # … but the server is already up and running on 0.6.0 (commit crashed last run).
    make_fabric_jar(jar, "sodium", "0.6.0")
    state.last_started_manifest_hash = scan_mods_dir(server_dir / "mods").content_hash()
    store.save(state)

    supervisor = FakeSupervisor(running=True)
    distributor = FakeDistributor()
    ctx = make_context(config, FakeUpdater(), supervisor, distributor, FakeNotifier())

    outcome = PipelineEngine(ctx).run()
    assert outcome.success
    assert distributor.versions  # committed
    assert supervisor.stop_calls == 0  # and without a restart
