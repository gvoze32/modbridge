"""Adapter for Minecraft Server Maintainer (worflor), driven as a subprocess.

Integration contract discovered from the upstream source (v1.0.4-beta):
- ``--update-only --yes --no-relaunch`` updates and terminates with exit code 0/1.
  ``--no-relaunch`` is mandatory: without a TTY the tool otherwise forks itself
  into a new GUI terminal. ``NO_COLOR=1`` forces plain ASCII output (``->`` arrows).
- ``woflo/update.log`` gains ``[ts] Update | <name> <old> -> <new>`` lines per change,
  ``ERROR |`` lines on failure, and rollback markers when a backup was restored.
- ``woflo/.pending`` present after exit means an update was interrupted and the
  next maintainer start will auto-rollback: never publish in that state.
- ``current_version.txt`` holds the authoritative Minecraft version.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from modbridge.adapters.base import PlannedChange, UpdatePlan, UpdateResult
from modbridge.config.schema import Config

log = logging.getLogger(__name__)

_LOG_LINE_RE = re.compile(r"^\[(?P<ts>[^\]]+)\]\s+(?P<type>\w+)\s*\|\s*(?P<msg>.*)$")
_CHANGE_RE = re.compile(r"^(?P<name>.+?)\s+(?P<old>\S+)\s*(?:->|→)\s*(?P<new>\S+)$")
_ROLLBACK_MARKERS = ("rolling back", "restoring backup", "restored from backup")


def parse_change_line(line: str) -> PlannedChange | None:
    match = _CHANGE_RE.match(line.strip())
    if not match:
        return None
    return PlannedChange(
        name=match.group("name").strip(),
        old_version=match.group("old"),
        new_version=match.group("new"),
    )


def parse_update_log(text: str) -> tuple[list[PlannedChange], list[str], bool]:
    """Parse update.log content into (applied changes, errors, rollback happened)."""
    applied: list[PlannedChange] = []
    errors: list[str] = []
    rolled_back = False
    for line in text.splitlines():
        m = _LOG_LINE_RE.match(line)
        if not m:
            continue
        kind, msg = m.group("type"), m.group("msg").strip()
        if kind == "Update":
            change = parse_change_line(msg)
            if change:
                applied.append(change)
        elif kind == "ERROR":
            errors.append(msg)
        if any(marker in msg.lower() for marker in _ROLLBACK_MARKERS):
            rolled_back = True
    return applied, errors, rolled_back


class MaintainerAdapter:
    def __init__(self, config: Config) -> None:
        self.server_dir = config.server_dir
        self.jar = config.maintainer_jar
        self.java = config.maintainer.java
        self.timeout = config.maintainer.timeout

    @property
    def update_log(self) -> Path:
        return self.server_dir / "woflo" / "update.log"

    @property
    def pending_marker(self) -> Path:
        return self.server_dir / "woflo" / ".pending"

    def current_minecraft_version(self) -> str | None:
        try:
            return (self.server_dir / "current_version.txt").read_text().strip() or None
        except OSError:
            return None

    def preflight(self) -> list[str]:
        problems: list[str] = []
        if not self.server_dir.is_dir():
            problems.append(f"Server directory does not exist: {self.server_dir}")
        if not self.jar.is_file():
            problems.append(f"Maintainer jar not found: {self.jar}")
        java = shutil.which(self.java)
        if java is None:
            problems.append(f"Java executable not found: {self.java!r}")
        return problems

    def _run(self, *flags: str) -> subprocess.CompletedProcess[str]:
        cmd = [self.java, "-jar", str(self.jar), *flags]
        env = os.environ | {"NO_COLOR": "1", "SERVER_MAINTAINER_TERM": "dumb"}
        log.debug("Running maintainer: %s", " ".join(cmd))
        return subprocess.run(
            cmd,
            cwd=self.server_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )

    def _log_offset(self) -> int:
        try:
            return self.update_log.stat().st_size
        except OSError:
            return 0

    def _log_since(self, offset: int) -> str:
        try:
            with self.update_log.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(min(offset, self.update_log.stat().st_size))
                return f.read()
        except OSError:
            return ""

    def plan(self) -> UpdatePlan:
        """Dry run. Parses planned ``<name> <old> -> <new>`` lines from stdout."""
        proc = self._run("--dry-run", "--no-relaunch")
        output = proc.stdout + proc.stderr
        if proc.returncode != 0:
            log.warning("Maintainer dry-run exited %d:\n%s", proc.returncode, output.strip())
            return UpdatePlan(raw_output=output)
        changes: list[PlannedChange] = []
        for line in output.splitlines():
            change = parse_change_line(line)
            if change:
                changes.append(change)
        return UpdatePlan(changes=tuple(changes), raw_output=output)

    def update(self) -> UpdateResult:
        offset = self._log_offset()
        try:
            proc = self._run("--update-only", "--yes", "--no-relaunch")
        except subprocess.TimeoutExpired:
            return UpdateResult(
                success=False,
                errors=(f"Maintainer timed out after {self.timeout}s",),
                exit_code=-1,
            )
        applied, errors, rolled_back = parse_update_log(self._log_since(offset))
        pending = self.pending_marker.exists()
        if pending:
            errors.append("woflo/.pending still present after run: update was interrupted")
        success = proc.returncode == 0 and not pending and not rolled_back
        return UpdateResult(
            success=success,
            rolled_back=rolled_back,
            applied=tuple(applied),
            errors=tuple(errors),
            exit_code=proc.returncode,
            raw_output=proc.stdout + proc.stderr,
        )

    def rollback(self) -> bool:
        try:
            proc = self._run("--rollback", "--yes", "--no-relaunch")
        except subprocess.TimeoutExpired:
            log.error("Maintainer rollback timed out")
            return False
        if proc.returncode != 0:
            log.error("Maintainer rollback failed (exit %d):\n%s", proc.returncode, proc.stdout)
        return proc.returncode == 0
