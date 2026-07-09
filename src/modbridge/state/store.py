"""Persistent pipeline state with atomic writes, plus an append-only run journal.

The state answers two questions between unattended runs:
- ``last_committed_manifest``: what mod set have clients already been given?
  (Commit idempotency: identical manifest hash => never commit again.)
- ``last_started_manifest_hash``: what mod set was the server last started with?
  (Restart avoidance during crash recovery: if the server already runs these
  mods, a re-run can commit without another restart.)
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from modbridge.domain.models import ModsManifest

log = logging.getLogger(__name__)


@dataclass
class PipelineState:
    last_committed_version: str | None = None
    last_committed_at: str | None = None
    last_committed_manifest: ModsManifest | None = None
    last_started_manifest_hash: str | None = None
    last_run_at: str | None = None
    last_run_status: str | None = None
    last_run_summary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "last_committed_version": self.last_committed_version,
            "last_committed_at": self.last_committed_at,
            "last_committed_manifest": (
                self.last_committed_manifest.to_dict() if self.last_committed_manifest else None
            ),
            "last_started_manifest_hash": self.last_started_manifest_hash,
            "last_run_at": self.last_run_at,
            "last_run_status": self.last_run_status,
            "last_run_summary": self.last_run_summary,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        manifest_data = data.get("last_committed_manifest")
        return cls(
            last_committed_version=data.get("last_committed_version"),
            last_committed_at=data.get("last_committed_at"),
            last_committed_manifest=(
                ModsManifest.from_dict(manifest_data) if manifest_data else None
            ),
            last_started_manifest_hash=data.get("last_started_manifest_hash"),
            last_run_at=data.get("last_run_at"),
            last_run_status=data.get("last_run_status"),
            last_run_summary=data.get("last_run_summary"),
            extra=data.get("extra", {}),
        )


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = state_dir
        self.state_file = state_dir / "state.json"
        self.journal_file = state_dir / "journal.jsonl"
        self.changelog_dir = state_dir / "changelogs"

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.changelog_dir.mkdir(parents=True, exist_ok=True)

    def load(self) -> PipelineState:
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return PipelineState()
        except (OSError, json.JSONDecodeError) as exc:
            # A corrupt state file must not brick the pipeline; worst case is one
            # redundant (but harmless, content-hash-guarded) restart cycle.
            log.error("State file %s unreadable (%s); starting fresh", self.state_file, exc)
            return PipelineState()
        return PipelineState.from_dict(data)

    def save(self, state: PipelineState) -> None:
        self.ensure_dirs()
        fd, tmp_name = tempfile.mkstemp(dir=self.state_dir, prefix=".state-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state.to_dict(), f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self.state_file)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def journal(self, run_id: str, step: str, status: str, message: str = "") -> None:
        self.ensure_dirs()
        entry = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "run_id": run_id,
            "step": step,
            "status": status,
            "message": message,
        }
        with self.journal_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
