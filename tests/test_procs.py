from __future__ import annotations

import os
import subprocess
from pathlib import Path

from modbridge.procs import find_processes_by_cwd, terminate_process


def fake_proc_entry(proc_root: Path, pid: int, comm: str, cwd: Path) -> None:
    entry = proc_root / str(pid)
    entry.mkdir(parents=True)
    (entry / "comm").write_text(comm + "\n")
    os.symlink(cwd, entry / "cwd")


def test_finds_java_with_matching_cwd(tmp_path: Path) -> None:
    proc_root = tmp_path / "proc"
    server_dir = tmp_path / "server"
    other_dir = tmp_path / "other"
    server_dir.mkdir()
    other_dir.mkdir()
    fake_proc_entry(proc_root, 100, "java", server_dir)
    fake_proc_entry(proc_root, 200, "java", other_dir)  # wrong cwd
    fake_proc_entry(proc_root, 300, "bash", server_dir)  # wrong comm
    (proc_root / "self").mkdir()  # non-numeric entries are ignored

    assert find_processes_by_cwd(server_dir, proc_root=proc_root) == [100]


def test_missing_proc_root_returns_empty(tmp_path: Path) -> None:
    assert find_processes_by_cwd(tmp_path, proc_root=tmp_path / "nope") == []


def test_terminate_process_with_sigterm(tmp_path: Path) -> None:
    proc = subprocess.Popen(["sleep", "300"])
    try:
        # The sleep callback reaps the child: in production the stray is an
        # orphan reaped by init, but here WE are its parent and a un-reaped
        # zombie still responds to os.kill(pid, 0).
        assert terminate_process(proc.pid, term_wait=10.0, sleep=lambda s: proc.poll())
    finally:
        if proc.poll() is None:
            proc.kill()


def test_terminate_missing_process_is_true() -> None:
    proc = subprocess.Popen(["sleep", "0.01"])
    proc.wait()
    assert terminate_process(proc.pid) is True
