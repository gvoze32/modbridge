from pathlib import Path

from modbridge.adapters.tmux import LogWatcher


def test_starts_at_eof_and_reads_only_new_content(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("old boot: Done (12.3s)\n")
    watcher = LogWatcher(log)
    assert watcher.read_new() == ""
    with log.open("a") as f:
        f.write("fresh line\n")
    assert watcher.read_new() == "fresh line\n"


def test_rotation_resets_to_top_of_new_file(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("previous session content that is quite long\n")
    watcher = LogWatcher(log)
    # Rotation: old file renamed away, new file created (new inode, smaller).
    log.rename(tmp_path / "old.log.gz")
    log.write_text("boot!\n")
    assert watcher.read_new() == "boot!\n"


def test_truncation_resets(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    log.write_text("aaaa bbbb cccc\n")
    watcher = LogWatcher(log)
    log.write_text("x\n")  # same inode, shrunk
    assert watcher.read_new() == "x\n"


def test_missing_file_then_created(tmp_path: Path) -> None:
    log = tmp_path / "latest.log"
    watcher = LogWatcher(log)
    assert watcher.read_new() == ""
    log.write_text("hello\n")
    assert watcher.read_new() == "hello\n"
