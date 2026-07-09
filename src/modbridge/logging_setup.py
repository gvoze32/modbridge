"""Logging: human-readable console output plus a rotating file log in the state dir."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str, state_dir: Path | None = None) -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    root.addHandler(console)

    if state_dir is not None:
        state_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            state_dir / "modbridge.log", maxBytes=2 << 20, backupCount=5, encoding="utf-8"
        )
        file_handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(file_handler)

    # httpx request lines are noise at INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
