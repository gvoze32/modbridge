"""Pipeline engine: runs the steps in order, journals every transition, recovers
the server on failure, and notifies the configured sinks."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from modbridge.adapters.base import Notification
from modbridge.pipeline.context import RunContext, StepResult, StepStatus
from modbridge.pipeline.steps import PIPELINE

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RunOutcome:
    success: bool
    committed_version: str | None
    message: str

    @property
    def exit_code(self) -> int:
        return 0 if self.success else 1


class PipelineEngine:
    def __init__(self, ctx: RunContext) -> None:
        self.ctx = ctx

    def run(self) -> RunOutcome:
        ctx = self.ctx
        log.info("ModBridge run %s starting", ctx.run_id)
        for name, step in PIPELINE:
            try:
                result = step(ctx)
            except Exception as exc:  # a step crashing must still recover + notify
                log.exception("Step '%s' raised", name)
                result = StepResult.failed(f"{type(exc).__name__}: {exc}")
            ctx.store.journal(ctx.run_id, name, result.status, result.message)

            if result.status == StepStatus.OK:
                log.info("[%s] ok%s", name, f": {result.message}" if result.message else "")
            elif result.status == StepStatus.SKIPPED:
                log.info("[%s] skipped: %s", name, result.message)
            elif result.status == StepStatus.DONE_EARLY:
                log.info("[%s] %s", name, result.message)
                return self._finish_success(result.message, committed=None)
            else:
                log.error("[%s] FAILED: %s", name, result.message)
                return self._finish_failure(name, result.message)

        summary = f"Published {ctx.version}: {ctx.changeset.summary() if ctx.changeset else ''}"
        return self._finish_success(summary, committed=ctx.version)

    # --- terminal states ---

    def _finish_success(self, message: str, committed: str | None) -> RunOutcome:
        ctx = self.ctx
        self._record_run("success", message)
        if committed and ctx.config.notifications.on_success:
            body = ctx.changelog_text or message
            self._notify(
                Notification(
                    title=f"✅ Server updated — {committed}",
                    body=body,
                    success=True,
                    fields={"Run": ctx.run_id},
                )
            )
        return RunOutcome(success=True, committed_version=committed, message=message)

    def _finish_failure(self, step: str, message: str) -> RunOutcome:
        ctx = self.ctx
        self._ensure_server_up_after_failure()
        self._record_run("failure", f"{step}: {message}")
        if ctx.config.notifications.on_failure:
            self._notify(
                Notification(
                    title=f"❌ ModBridge run failed at step '{step}'",
                    body=message,
                    success=False,
                    fields={"Run": ctx.run_id},
                )
            )
        return RunOutcome(success=False, committed_version=None, message=f"{step}: {message}")

    def _ensure_server_up_after_failure(self) -> None:
        """Never leave the server down because the pipeline failed mid-flight."""
        ctx = self.ctx
        if not ctx.stopped_server:
            return
        try:
            if ctx.supervisor.is_server_running():
                return
            log.warning("Pipeline failed after stopping the server; attempting restart")
            ctx.supervisor.start()
            if ctx.supervisor.wait_ready(ctx.config.server.startup_timeout):
                log.info("Server restarted after pipeline failure")
            else:
                log.critical("Could not restart server after failure — manual intervention needed")
        except Exception:
            log.exception("Recovery restart failed")

    def _record_run(self, status: str, summary: str) -> None:
        ctx = self.ctx
        ctx.state.last_run_at = ctx.now().isoformat(timespec="seconds")
        ctx.state.last_run_status = status
        ctx.state.last_run_summary = summary
        try:
            ctx.store.save(ctx.state)
        except OSError:
            log.exception("Could not persist state")

    def _notify(self, notification: Notification) -> None:
        for sink in self.ctx.notifiers:
            try:
                sink.send(notification)
            except Exception:
                log.exception("Notifier %s failed", type(sink).__name__)
