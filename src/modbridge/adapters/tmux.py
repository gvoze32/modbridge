"""tmux-based server supervisor.

ModBridge deliberately leaves the server under tmux (the admin's existing setup)
instead of letting the maintainer's built-in supervisor own the process. Commands
are typed into the console with ``send-keys``; readiness and command responses are
observed by tailing the server log with rotation awareness (NeoForge recreates
``logs/latest.log`` on every start).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from pathlib import Path

from modbridge.config.schema import Config

log = logging.getLogger(__name__)

_PLAYER_LIST_RE = re.compile(r"There are (\d+) of a max(?:imum)? (?:of )?\d+ players online")


class TmuxError(Exception):
    pass


class LogWatcher:
    """Incremental reader for a log file that may be rotated/recreated.

    Starts at the current end of file, so pre-existing content (e.g. a stale
    ``Done (`` from the previous boot) is never matched. Rotation is detected by
    inode change or shrinkage, after which reading restarts from the top of the
    new file.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset, self.inode = self._stat()

    def _stat(self) -> tuple[int, int | None]:
        try:
            st = self.path.stat()
            return st.st_size, st.st_ino
        except OSError:
            return 0, None

    def read_new(self) -> str:
        size, inode = self._stat()
        if inode != self.inode or size < self.offset:  # rotated or truncated: start over
            self.offset = 0
            self.inode = inode
        if size == self.offset:
            return ""
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                data = f.read()
                self.offset = f.tell()
                return data
        except OSError:
            return ""

    def wait_for(self, pattern: str, timeout: float, poll: float = 0.5) -> bool:
        regex = re.compile(pattern)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if regex.search(self.read_new()):
                return True
            time.sleep(poll)
        return False


class TmuxSupervisor:
    def __init__(self, config: Config) -> None:
        self.session = config.server.tmux_session
        self.server_dir = config.server_dir
        self.start_command = config.server.start_command
        self.log_file = config.server_log
        self.ready_pattern = config.server.ready_pattern

    # --- tmux plumbing ---

    def _tmux(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        proc = subprocess.run(
            ["tmux", *args], capture_output=True, text=True, timeout=30, check=False
        )
        if check and proc.returncode != 0:
            raise TmuxError(f"tmux {' '.join(args)} failed: {proc.stderr.strip()}")
        return proc

    def session_exists(self) -> bool:
        # '=' prefix forces exact session-name match (no prefix matching).
        return self._tmux("has-session", "-t", f"={self.session}", check=False).returncode == 0

    def _first_pane(self) -> tuple[str, int] | None:
        """(pane_id, pane_pid) of the session's first pane, or None.

        All later commands target the concrete pane id (e.g. ``%0``): name-based
        targets like ``=mc`` resolve inconsistently between tmux commands and
        versions, while pane ids are unambiguous everywhere.
        """
        proc = self._tmux(
            "list-panes", "-s", "-t", f"={self.session}",
            "-F", "#{pane_id} #{pane_pid}", check=False,
        )
        if proc.returncode != 0:
            return None
        lines = proc.stdout.strip().splitlines()
        if not lines:
            return None
        pane_id, _, pid = lines[0].partition(" ")
        try:
            return pane_id, int(pid)
        except ValueError:
            return None

    @staticmethod
    def _descendants(root_pid: int) -> list[tuple[int, str]]:
        proc = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,comm="], capture_output=True, text=True, check=False
        )
        children: dict[int, list[tuple[int, str]]] = {}
        for line in proc.stdout.splitlines():
            parts = line.split(None, 2)
            if len(parts) < 3:
                continue
            try:
                pid, ppid = int(parts[0]), int(parts[1])
            except ValueError:
                continue
            children.setdefault(ppid, []).append((pid, parts[2]))
        result: list[tuple[int, str]] = []
        queue = [root_pid]
        while queue:
            for child in children.get(queue.pop(), []):
                result.append(child)
                queue.append(child[0])
        return result

    def is_server_running(self) -> bool:
        pane = self._first_pane()
        if pane is None:
            return False
        return any("java" in comm.lower() for _, comm in self._descendants(pane[1]))

    # --- console interaction ---

    def send_command(self, command: str) -> None:
        pane = self._first_pane()
        if pane is None:
            raise TmuxError(f"tmux session '{self.session}' does not exist (or has no panes)")
        # -l sends the text literally (no key-name interpretation), Enter separately.
        self._tmux("send-keys", "-t", pane[0], "-l", "--", command)
        self._tmux("send-keys", "-t", pane[0], "Enter")

    def say(self, message: str) -> None:
        try:
            self.send_command(f"say {message}")
        except TmuxError as exc:
            log.warning("Could not broadcast message: %s", exc)

    def online_players(self) -> int | None:
        if not self.is_server_running():
            return None
        watcher = LogWatcher(self.log_file)
        try:
            self.send_command("list")
        except TmuxError:
            return None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            match = _PLAYER_LIST_RE.search(watcher.read_new())
            if match:
                return int(match.group(1))
            time.sleep(0.5)
        return None

    # --- lifecycle ---

    def stop(self, timeout: float) -> bool:
        if not self.is_server_running():
            return True
        log.info("Stopping server (timeout %.0fs)", timeout)
        self.send_command("stop")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.is_server_running():
                return True
            time.sleep(1.0)
        log.error("Server did not stop within %.0fs", timeout)
        return False

    def start(self) -> None:
        if self.is_server_running():
            return
        if not self.session_exists():
            log.info("Creating tmux session '%s'", self.session)
            self._tmux("new-session", "-d", "-s", self.session, "-c", str(self.server_dir))
        log.info("Starting server: %s", self.start_command)
        self.send_command(self.start_command)

    def wait_ready(self, timeout: float) -> bool:
        """Wait until the ready pattern appears in log lines written after start.

        The watcher starts at the current EOF, so a stale ``Done (`` from the
        previous boot never matches; the rotation on server start resets the
        watcher to the top of the fresh log.
        """
        watcher = LogWatcher(self.log_file)
        log.info("Waiting up to %.0fs for server to become ready", timeout)
        regex = re.compile(self.ready_pattern)
        deadline = time.monotonic() + timeout
        grace_until = time.monotonic() + 15.0  # run.sh may take a moment to spawn java
        while time.monotonic() < deadline:
            if regex.search(watcher.read_new()):
                log.info("Server is ready")
                return True
            if time.monotonic() > grace_until and not self.is_server_running():
                log.error("Server process is gone while waiting for readiness")
                return False
            time.sleep(1.0)
        log.error("Server not ready within %.0fs", timeout)
        return False
