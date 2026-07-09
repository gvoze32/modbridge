"""flock-based run lock. The maintainer's `.pending` file is a crash marker, not a
mutex, so overlapping cron runs must be prevented on our side."""

from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType


class LockError(Exception):
    pass


class RunLock:
    def __init__(self, lock_file: Path) -> None:
        self.lock_file = lock_file
        self._fd: int | None = None

    def __enter__(self) -> RunLock:
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.lock_file, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise LockError(
                f"Another ModBridge run is already in progress (lock: {self.lock_file})"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        self._fd = fd
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None
