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
from modbridge.procs import find_processes_by_cwd, terminate_process

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

    def _panes(self) -> list[tuple[str, int, str]]:
        """(pane_id, pane_pid, current_command) for every pane in the session.

        Commands always target concrete pane ids (e.g. ``%0``): name-based
        targets like ``=mc`` resolve inconsistently between tmux commands and
        versions, while pane ids are unambiguous everywhere.
        """
        proc = self._tmux(
            "list-panes", "-s", "-t", f"={self.session}",
            "-F", "#{pane_id}\t#{pane_pid}\t#{pane_current_command}", check=False,
        )
        if proc.returncode != 0:
            return []
        panes: list[tuple[str, int, str]] = []
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                pid = int(parts[1])
            except ValueError:
                continue
            panes.append((parts[0], pid, parts[2] if len(parts) > 2 else ""))
        return panes

    def _server_pane(self) -> tuple[str, int, str] | None:
        """The pane running the server: tmux's own view of the foreground command
        (``pane_current_command``) is checked first, the process tree second."""
        for pane in self._panes():
            if "java" in pane[2].lower():
                return pane
            if any("java" in comm.lower() for _, comm in self._descendants(pane[1])):
                return pane
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
        running = self._server_pane() is not None
        if not running:
            log.debug("No java in tmux session '%s'; panes: %s", self.session, self._panes())
        return running

    def pane_snapshot(self, lines: int = 15) -> str:
        """Last visible lines of the (server) pane — attached to error logs so a
        failed start can be diagnosed after the fact."""
        pane = self._server_pane()
        panes = self._panes()
        target = pane[0] if pane else (panes[0][0] if panes else None)
        if target is None:
            return "(tmux session not found)"
        proc = self._tmux("capture-pane", "-p", "-t", target, check=False)
        if proc.returncode != 0:
            return "(could not capture pane)"
        return "\n".join(proc.stdout.rstrip().splitlines()[-lines:])

    # --- console interaction ---

    def send_command(self, command: str) -> None:
        # Prefer the pane actually running the server; fall back to the first pane.
        pane = self._server_pane()
        if pane is None:
            panes = self._panes()
            if not panes:
                raise TmuxError(
                    f"tmux session '{self.session}' does not exist (or has no panes)"
                )
            pane = panes[0]
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

    def _managed_java_pids(self) -> set[int]:
        pids: set[int] = set()
        for pane in self._panes():
            for pid, comm in self._descendants(pane[1]):
                if "java" in comm.lower():
                    pids.add(pid)
        return pids

    def kill_stray_servers(self) -> None:
        """Stop java processes running in the server directory OUTSIDE our tmux
        session. The maintainer's post-update startup verification leaks exactly
        such an orphan (it kills run.sh but not java), which then holds
        ``session.lock`` and the SakuraUpdater port, instantly killing any
        properly-started server."""
        managed = self._managed_java_pids()
        for pid in find_processes_by_cwd(self.server_dir):
            if pid in managed:
                continue
            log.warning(
                "Stray server process found (pid %d, cwd %s, outside tmux) — likely leaked "
                "by the maintainer's startup verification. Stopping it (SIGTERM saves the "
                "world) before starting the real server.",
                pid,
                self.server_dir,
            )
            if terminate_process(pid):
                log.info("Stray server pid %d stopped", pid)
            else:
                raise TmuxError(
                    f"Could not stop stray server process {pid}; refusing to start a "
                    "second instance against a locked world"
                )

    def start(self) -> None:
        if self.is_server_running():
            return
        self.kill_stray_servers()
        if not self.session_exists():
            log.info("Creating tmux session '%s'", self.session)
            self._tmux("new-session", "-d", "-s", self.session, "-c", str(self.server_dir))
        log.info("Starting server: %s", self.start_command)
        self.send_command(self.start_command)

    # Early "server is dead" verdicts need BOTH no log growth and no visible java
    # process for this long. A booting server always keeps writing its log, so a
    # healthy (if slow) boot can never be declared dead by a flaky process check.
    STALL_TIMEOUT = 30.0

    def wait_ready(self, timeout: float) -> bool:
        """Wait until the ready pattern appears in log lines written after start.

        The watcher starts at the current EOF, so a stale ``Done (`` from the
        previous boot never matches; the rotation on server start resets the
        watcher to the top of the fresh log. Log growth is treated as proof of
        life; the process check alone is never enough to fail early.
        """
        watcher = LogWatcher(self.log_file)
        log.info("Waiting up to %.0fs for server to become ready", timeout)
        regex = re.compile(self.ready_pattern)
        deadline = time.monotonic() + timeout
        last_activity = time.monotonic()
        tail = ""  # keeps matches working across chunk boundaries
        while time.monotonic() < deadline:
            chunk = watcher.read_new()
            if chunk:
                last_activity = time.monotonic()
                if regex.search(tail + chunk):
                    log.info("Server is ready")
                    return True
                tail = (tail + chunk)[-200:]
            elif self.is_server_running():
                last_activity = time.monotonic()
            elif time.monotonic() - last_activity > self.STALL_TIMEOUT:
                log.error(
                    "Server looks dead: no log output and no java process for %.0fs. "
                    "Last console output:\n%s",
                    self.STALL_TIMEOUT,
                    self.pane_snapshot(),
                )
                return False
            time.sleep(1.0)
        log.error(
            "Server not ready within %.0fs. Last console output:\n%s",
            timeout,
            self.pane_snapshot(),
        )
        return False
