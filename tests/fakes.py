"""In-memory fakes for the adapter protocols, used by pipeline tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from modbridge.adapters.base import Notification, UpdatePlan, UpdateResult


class FakeUpdater:
    def __init__(
        self,
        plan: UpdatePlan | None = None,
        result: UpdateResult | None = None,
        problems: list[str] | None = None,
        mc_version: str = "1.21.1",
        on_update: Callable[[], None] | None = None,
        on_rollback: Callable[[], None] | None = None,
        rollback_ok: bool = True,
    ) -> None:
        self._plan = plan or UpdatePlan()
        self._result = result or UpdateResult(success=True)
        self._problems = problems or []
        self.mc_version = mc_version
        self.on_update = on_update
        self.on_rollback = on_rollback
        self.rollback_ok = rollback_ok
        self.update_called = 0
        self.rollback_called = 0

    def preflight(self) -> list[str]:
        return list(self._problems)

    def plan(self) -> UpdatePlan:
        return self._plan

    def update(self) -> UpdateResult:
        self.update_called += 1
        if self.on_update:
            self.on_update()
        return self._result

    def rollback(self) -> bool:
        self.rollback_called += 1
        if self.on_rollback:
            self.on_rollback()
        return self.rollback_ok

    def current_minecraft_version(self) -> str | None:
        return self.mc_version


class FakeSupervisor:
    def __init__(
        self,
        running: bool = True,
        players: int | None = 0,
        stop_ok: bool = True,
        ready_ok: bool = True,
    ) -> None:
        self.running = running
        self.players = players
        self.stop_ok = stop_ok
        self.ready_ok = ready_ok
        self.commands: list[str] = []
        self.said: list[str] = []
        self.stop_calls = 0
        self.start_calls = 0

    def is_server_running(self) -> bool:
        return self.running

    def send_command(self, command: str) -> None:
        self.commands.append(command)

    def say(self, message: str) -> None:
        self.said.append(message)

    def online_players(self) -> int | None:
        return self.players

    def stop(self, timeout: float) -> bool:
        self.stop_calls += 1
        if self.stop_ok:
            self.running = False
        return self.stop_ok

    def start(self) -> None:
        self.start_calls += 1
        self.running = True

    def wait_ready(self, timeout: float) -> bool:
        if not self.ready_ok:
            self.running = False
            return False
        return self.running


class FakeDistributor:
    def __init__(self, healthy: bool = True, commit_ok: bool = True) -> None:
        self.healthy = healthy
        self.commit_ok = commit_ok
        self.versions: list[str] = []
        self.committed_changelogs: list[Path] = []

    def is_healthy(self, retries: int = 1, delay: float = 1.0) -> bool:
        return self.healthy

    def latest_version(self) -> str | None:
        return self.versions[-1] if self.versions else None

    def next_version(self) -> str:
        return f"2026.07.09-{len(self.versions) + 1}"

    def commit(self, version: str, changelog_file: Path) -> bool:
        if not self.commit_ok:
            return False
        self.versions.append(version)
        self.committed_changelogs.append(changelog_file)
        return True


class FakeNotifier:
    def __init__(self) -> None:
        self.sent: list[Notification] = []

    def send(self, notification: Notification) -> None:
        self.sent.append(notification)
