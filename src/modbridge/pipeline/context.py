"""Run context shared by all pipeline steps."""

from __future__ import annotations

import time as _time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from modbridge.adapters.base import (
    Distributor,
    NotificationSink,
    ServerSupervisor,
    UpdatePlan,
    UpdaterBackend,
    UpdateResult,
)
from modbridge.config.schema import Config
from modbridge.domain.models import ChangeSet, ModsManifest
from modbridge.state.store import PipelineState, StateStore


class StepStatus(StrEnum):
    OK = "ok"
    SKIPPED = "skipped"
    DONE_EARLY = "done_early"  # clean, intentional early finish (nothing to do, dry run…)
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StepResult:
    status: StepStatus
    message: str = ""

    @classmethod
    def ok(cls, message: str = "") -> StepResult:
        return cls(StepStatus.OK, message)

    @classmethod
    def skipped(cls, message: str = "") -> StepResult:
        return cls(StepStatus.SKIPPED, message)

    @classmethod
    def done_early(cls, message: str) -> StepResult:
        return cls(StepStatus.DONE_EARLY, message)

    @classmethod
    def failed(cls, message: str) -> StepResult:
        return cls(StepStatus.FAILED, message)


@dataclass(frozen=True, slots=True)
class RunOptions:
    force: bool = False  # bypass the schedule window
    skip_countdown: bool = False
    dry_run: bool = False


@dataclass
class RunContext:
    config: Config
    options: RunOptions
    updater: UpdaterBackend
    supervisor: ServerSupervisor
    distributor: Distributor
    notifiers: list[NotificationSink]
    store: StateStore
    state: PipelineState
    run_id: str
    # Injectable clock/sleep so pipeline tests run instantly.
    now: Callable[[], datetime] = datetime.now
    sleep: Callable[[float], None] = _time.sleep

    # Accumulated during the run:
    mc_version_before: str | None = None
    mc_version_after: str | None = None
    pre_manifest: ModsManifest | None = None
    post_manifest: ModsManifest | None = None
    plan: UpdatePlan | None = None
    update_result: UpdateResult | None = None
    changeset: ChangeSet | None = None
    version: str | None = None
    changelog_path: Path | None = None
    changelog_text: str | None = None
    needs_update: bool = False
    needs_restart: bool = False
    stopped_server: bool = False
    warnings: list[str] = field(default_factory=list)

    def warn(self, message: str) -> None:
        self.warnings.append(message)
