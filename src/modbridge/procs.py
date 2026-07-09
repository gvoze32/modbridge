"""Linux process inspection helpers.

Needed because Minecraft Server Maintainer's post-update startup verification
spawns the server through ``run.sh`` and kills only the shell afterwards — the
java process survives as an orphan (reparented to PID 1), silently holding the
world's ``session.lock`` and SakuraUpdater's port. Any server started after
that dies instantly. ModBridge therefore hunts down stray server processes
before starting the real one.
"""

from __future__ import annotations

import logging
import os
import signal
import time
from collections.abc import Callable
from pathlib import Path

log = logging.getLogger(__name__)

_PROC = Path("/proc")


def find_processes_by_cwd(cwd: Path, comm: str = "java", proc_root: Path = _PROC) -> list[int]:
    """PIDs of processes named ``comm`` whose working directory is ``cwd``.

    Uses /proc, so it returns [] on non-Linux systems (and in that case the
    stray-process protection is simply inactive).
    """
    if not proc_root.is_dir():
        return []
    try:
        target = cwd.resolve()
    except OSError:
        return []
    pids: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            proc_cwd = Path(os.readlink(entry / "cwd")).resolve()
            proc_comm = (entry / "comm").read_text().strip()
        except OSError:
            continue  # process vanished or not ours to inspect
        if proc_comm == comm and proc_cwd == target:
            pids.append(int(entry.name))
    return sorted(pids)


def terminate_process(
    pid: int,
    term_wait: float = 60.0,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Stop a process: SIGTERM first (a Minecraft server saves the world in its
    shutdown hook), escalating to SIGKILL. Returns True when the process is gone."""

    def alive() -> bool:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True
    deadline = time.monotonic() + term_wait
    while time.monotonic() < deadline:
        if not alive():
            return True
        sleep(1.0)
    log.warning("Process %d ignored SIGTERM for %.0fs; sending SIGKILL", pid, term_wait)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    sleep(1.0)
    return not alive()
