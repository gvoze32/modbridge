"""ModBridge command-line interface.

    modbridge run          # what cron calls; honors the schedule window
    modbridge run --force  # admin: update right now, bypass the window
    modbridge dry-run      # show what would happen, change nothing
    modbridge status       # last run / last published version / pending changes
    modbridge validate     # check the config file
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from modbridge import __version__
from modbridge.adapters.base import NotificationSink
from modbridge.adapters.maintainer import MaintainerAdapter
from modbridge.adapters.notify import DiscordNotifier, LogNotifier
from modbridge.adapters.sakura import SakuraAdapter
from modbridge.adapters.tmux import TmuxSupervisor
from modbridge.config.schema import Config, ConfigError, load_config
from modbridge.deps.github import GitHubError
from modbridge.deps.installer import DependencyError, DependencyManager
from modbridge.logging_setup import setup_logging
from modbridge.mods.scanner import scan_mods_dir
from modbridge.pipeline.context import RunContext, RunOptions
from modbridge.pipeline.engine import PipelineEngine
from modbridge.state.lock import LockError, RunLock
from modbridge.state.store import StateStore

app = typer.Typer(
    name="modbridge",
    help="Automated Minecraft server update pipeline: Server Maintainer -> SakuraUpdater.",
    no_args_is_help=True,
    add_completion=False,
)

log = logging.getLogger(__name__)

ConfigOption = Annotated[
    Path,
    typer.Option("--config", "-c", help="Path to the ModBridge YAML config file."),
]
_DEFAULT_CONFIG = Path("modbridge.yaml")


def _load(config_path: Path) -> Config:
    try:
        return load_config(config_path)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from None


def _build_context(config: Config, options: RunOptions) -> RunContext:
    store = StateStore(config.state_dir)
    supervisor = TmuxSupervisor(config)
    notifiers: list[NotificationSink] = [LogNotifier()]
    if config.notifications.discord_webhook:
        notifiers.append(DiscordNotifier(config.notifications.discord_webhook))
    return RunContext(
        config=config,
        options=options,
        updater=MaintainerAdapter(config),
        supervisor=supervisor,
        distributor=SakuraAdapter(config, supervisor),
        notifiers=notifiers,
        store=store,
        state=store.load(),
        run_id=datetime.now().strftime("%Y%m%d-%H%M%S"),
        deps=DependencyManager(config),
    )


def _execute(config_path: Path, options: RunOptions) -> None:
    config = _load(config_path)
    setup_logging(config.log_level, config.state_dir)
    try:
        with RunLock(config.state_dir / "modbridge.lock"):
            ctx = _build_context(config, options)
            outcome = PipelineEngine(ctx).run()
    except LockError as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(3) from None
    if outcome.success:
        typer.secho(outcome.message, fg=typer.colors.GREEN)
    else:
        typer.secho(outcome.message, fg=typer.colors.RED, err=True)
    raise typer.Exit(outcome.exit_code)


@app.command()
def run(
    config: ConfigOption = _DEFAULT_CONFIG,
    force: Annotated[
        bool, typer.Option("--force", help="Bypass the schedule window and update now.")
    ] = False,
    no_countdown: Annotated[
        bool, typer.Option("--no-countdown", help="Skip the player warning countdown.")
    ] = False,
) -> None:
    """Run the full update pipeline (this is what cron should call)."""
    _execute(config, RunOptions(force=force, skip_countdown=no_countdown))


@app.command(name="dry-run")
def dry_run(config: ConfigOption = _DEFAULT_CONFIG) -> None:
    """Preview planned updates and unpublished changes without touching anything."""
    _execute(config, RunOptions(dry_run=True))


@app.command()
def status(config: ConfigOption = _DEFAULT_CONFIG) -> None:
    """Show the last run, the last published version, and unpublished local changes."""
    cfg = _load(config)
    store = StateStore(cfg.state_dir)
    state = store.load()

    typer.echo(f"ModBridge {__version__}")
    typer.echo(f"Server directory:    {cfg.server_dir}")
    typer.echo(f"Last run:            {state.last_run_at or 'never'}"
               + (f" ({state.last_run_status})" if state.last_run_status else ""))
    if state.last_run_summary:
        typer.echo(f"Last run summary:    {state.last_run_summary}")
    typer.echo(f"Last published:      {state.last_committed_version or 'never'}"
               + (f" at {state.last_committed_at}" if state.last_committed_at else ""))

    manifest = scan_mods_dir(cfg.mods_dir)
    typer.echo(f"Mods on disk:        {len(manifest.mods)}")
    committed = state.last_committed_manifest
    if committed is None:
        typer.secho("Unpublished changes: initial commit pending", fg=typer.colors.YELLOW)
    elif committed.content_hash() != manifest.content_hash():
        typer.secho("Unpublished changes: YES (run `modbridge run` to publish)",
                    fg=typer.colors.YELLOW)
    else:
        typer.secho("Unpublished changes: none", fg=typer.colors.GREEN)

    unidentified = [m.filename for m in manifest.mods if m.mod_id is None]
    if unidentified:
        typer.echo("Jars without readable metadata (still tracked by hash):")
        for name in unidentified:
            typer.echo(f"  - {name}")


@app.command()
def setup(
    config: ConfigOption = _DEFAULT_CONFIG,
    update: Annotated[
        bool,
        typer.Option("--update", help="Also replace installed tools with newer releases."),
    ] = False,
) -> None:
    """Download the maintainer jar and the SakuraUpdater mod from GitHub releases."""
    cfg = _load(config)
    setup_logging(cfg.log_level, cfg.state_dir)
    manager = DependencyManager(cfg)
    try:
        actions = manager.ensure_all(update=update)
    except (DependencyError, GitHubError) as exc:
        typer.secho(f"✗ {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from None
    for action in actions:
        typer.secho(f"✓ {action}", fg=typer.colors.GREEN)
    if not actions:
        typer.secho("✓ Everything already installed and up to date", fg=typer.colors.GREEN)
    if any("SakuraUpdater" in a for a in actions):
        typer.echo(
            "Note: the new SakuraUpdater jar will be published to players on the next "
            "`modbridge run` (restart + commit)."
        )


@app.command()
def validate(config: ConfigOption = _DEFAULT_CONFIG) -> None:
    """Validate the configuration file and referenced paths."""
    cfg = _load(config)
    problems: list[str] = []
    if not cfg.server_dir.is_dir():
        problems.append(f"server.directory does not exist: {cfg.server_dir}")
    if not cfg.maintainer_jar.is_file():
        if cfg.dependencies.auto_install:
            typer.echo(
                f"maintainer.jar missing ({cfg.maintainer_jar}); it will be downloaded "
                "automatically on the next run (or now, via `modbridge setup`)"
            )
        else:
            problems.append(f"maintainer.jar not found: {cfg.maintainer_jar}")
    if not cfg.mods_dir.is_dir():
        problems.append(f"mods directory not found: {cfg.mods_dir}")
    if cfg.changelog.template and not cfg.changelog.template.is_file():
        problems.append(f"changelog.template not found: {cfg.changelog.template}")

    from modbridge.adapters.sakura_config import (
        read_sakura_config,
        sakura_config_path,
        sakura_config_synced,
    )

    sakura_file = sakura_config_path(cfg)
    if cfg.sakura.manage_config:
        if not sakura_config_synced(cfg):
            typer.echo(
                f"SakuraUpdater config ({sakura_file.name}) out of sync with modbridge.yaml; "
                "it will be rewritten during the next run's restart window"
            )
    else:
        _, sync_dirs = read_sakura_config(sakura_file)
        if not sync_dirs:
            problems.append(
                f"SakuraUpdater SYNC_DIR is empty or unreadable in {sakura_file} — commits "
                "would contain no files. Fix it manually or set sakura.manage_config: true. "
                "(Note: the mod reads sakuraupdater-common.toml, not the README's "
                "sakuraupdater-server.toml.)"
            )
    if problems:
        for p in problems:
            typer.secho(f"✗ {p}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2)
    typer.secho("✓ Configuration is valid", fg=typer.colors.GREEN)


@app.callback()
def _main() -> None:
    """ModBridge: unattended Minecraft server updates, published to players."""


if __name__ == "__main__":
    app()
