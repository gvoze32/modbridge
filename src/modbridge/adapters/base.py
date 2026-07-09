"""Adapter protocols. Every external system sits behind one of these interfaces so
future backends (Packwiz, systemd, RCON, other distributors) are drop-in plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class PlannedChange:
    name: str
    old_version: str
    new_version: str


@dataclass(frozen=True)
class UpdatePlan:
    """Result of a dry run: what the updater intends to change."""

    changes: tuple[PlannedChange, ...] = ()
    raw_output: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.changes)


@dataclass(frozen=True)
class UpdateResult:
    """Result of a real update run."""

    success: bool
    rolled_back: bool = False
    applied: tuple[PlannedChange, ...] = ()
    errors: tuple[str, ...] = ()
    exit_code: int = 0
    raw_output: str = ""


class UpdaterBackend(Protocol):
    """Updates server files (MC / loader / mods). Implementation: Minecraft Server Maintainer."""

    def preflight(self) -> list[str]:
        """Return a list of problems that make updating impossible (empty = OK)."""
        ...

    def plan(self) -> UpdatePlan: ...

    def update(self) -> UpdateResult: ...

    def rollback(self) -> bool: ...


class ServerSupervisor(Protocol):
    """Controls the Minecraft server process. Implementation: tmux."""

    def is_server_running(self) -> bool: ...

    def send_command(self, command: str) -> None:
        """Type a command into the server console."""
        ...

    def say(self, message: str) -> None: ...

    def online_players(self) -> int | None:
        """Player count, or None if it could not be determined."""
        ...

    def stop(self, timeout: float) -> bool: ...

    def start(self) -> None: ...

    def wait_ready(self, timeout: float) -> bool: ...


class Distributor(Protocol):
    """Publishes updates to players. Implementation: SakuraUpdater."""

    def is_healthy(self, retries: int = 1, delay: float = 1.0) -> bool: ...

    def latest_version(self) -> str | None: ...

    def next_version(self) -> str: ...

    def commit(self, version: str, changelog_file: Path) -> bool: ...


@dataclass(frozen=True)
class Notification:
    title: str
    body: str
    success: bool
    fields: dict[str, str] = field(default_factory=dict)


class NotificationSink(Protocol):
    def send(self, notification: Notification) -> None: ...
