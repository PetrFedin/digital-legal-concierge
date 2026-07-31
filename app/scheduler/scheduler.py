from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.db.session import AsyncSessionLocal
from app.domain.notifications.notification_sender import NotificationSender
from app.scheduler.jobs import SchedulerJobs
from app.scheduler.lease import SchedulerCycleLease

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 60 * 60
SCHEDULER_RETRY_SECONDS = 10 * 60
SCHEDULER_JOB_TIMEOUT_SECONDS = 30 * 60

# Public result keys are retained for compatibility with operational callers.
JOB_SPECS = (
    ("encrypted_backup", "scheduler", "create_encrypted_backup_if_due"),
    ("payment_reminders", "scheduler", "check_unpaid_payments"),
    ("released_slots", "scheduler", "release_unpaid_consultation_slots"),
    ("consultation_reminders", "scheduler", "check_consultation_reminders"),
    (
        "consultation_completion_overdue",
        "scheduler",
        "check_consultation_completion_overdue",
    ),
    ("case_sla", "scheduler", "check_case_sla"),
    ("case_retention", "scheduler", "discover_due_case_retention"),
    ("security_cleanup", "scheduler", "cleanup_security_state"),
    ("claim_deadlines", "scheduler", "check_claim_waiting_30_days"),
    ("sent_notifications", "sender", "send_pending"),
)


@dataclass(frozen=True)
class SchedulerJobOutcome:
    name: str
    ok: bool
    duration_ms: int
    result: object | None = None
    error_type: str | None = None

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SchedulerCycleResult:
    acquired: bool
    started_at: str
    completed_at: str
    duration_ms: int
    jobs: tuple[SchedulerJobOutcome, ...]

    @property
    def ok(self) -> bool:
        return self.acquired and all(job.ok for job in self.jobs)

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "acquired": self.acquired,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_ms": self.duration_ms,
            "jobs": [job.as_dict() for job in self.jobs],
        }

    def legacy_results(self) -> dict[str, object]:
        if not self.acquired:
            return {
                "scheduler_acquired": False,
                "scheduler_skipped": "singleton_lease_held",
            }
        result: dict[str, object] = {"scheduler_acquired": True}
        for job in self.jobs:
            result[job.name] = (
                job.result
                if job.ok
                else {"ok": False, "error_type": job.error_type}
            )
        result["scheduler_ok"] = self.ok
        return result


def _safe_result(value: Any) -> object | None:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {
            str(key): item
            for key, item in value.items()
            if isinstance(item, (type(None), bool, int, float, str))
        }
    if isinstance(value, (tuple, list)):
        return [
            item
            for item in value
            if isinstance(item, (type(None), bool, int, float, str))
        ]
    return type(value).__name__


class AppScheduler:
    def __init__(self, interval_seconds: int = DEFAULT_INTERVAL_SECONDS):
        self.interval_seconds = max(1, int(interval_seconds))
        self.last_cycle: SchedulerCycleResult | None = None

    async def _run_job(
        self,
        *,
        result_name: str,
        service_type: str,
        method_name: str,
    ) -> SchedulerJobOutcome:
        started = time.monotonic()
        async with AsyncSessionLocal() as db:
            service = (
                SchedulerJobs(db)
                if service_type == "scheduler"
                else NotificationSender(db)
            )
            try:
                result = await asyncio.wait_for(
                    getattr(service, method_name)(),
                    timeout=SCHEDULER_JOB_TIMEOUT_SECONDS,
                )
                await db.commit()
                outcome = SchedulerJobOutcome(
                    name=result_name,
                    ok=True,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    result=_safe_result(result),
                )
                logger.info(
                    "scheduler_job_succeeded",
                    extra={
                        "scheduler_job": result_name,
                        "duration_ms": outcome.duration_ms,
                    },
                )
                return outcome
            except asyncio.CancelledError:
                await db.rollback()
                raise
            except Exception as error:
                await db.rollback()
                outcome = SchedulerJobOutcome(
                    name=result_name,
                    ok=False,
                    duration_ms=int((time.monotonic() - started) * 1000),
                    error_type=type(error).__name__,
                )
                logger.exception(
                    "scheduler_job_failed",
                    extra={
                        "scheduler_job": result_name,
                        "duration_ms": outcome.duration_ms,
                    },
                )
                return outcome

    async def run_cycle(self) -> SchedulerCycleResult:
        cycle_started = datetime.now(timezone.utc)
        monotonic_started = time.monotonic()
        lease = SchedulerCycleLease()
        acquired = await lease.acquire()
        if not acquired:
            completed = datetime.now(timezone.utc)
            result = SchedulerCycleResult(
                acquired=False,
                started_at=cycle_started.isoformat(),
                completed_at=completed.isoformat(),
                duration_ms=int((time.monotonic() - monotonic_started) * 1000),
                jobs=(),
            )
            self.last_cycle = result
            logger.info("scheduler_cycle_skipped_lease_held")
            return result

        outcomes: list[SchedulerJobOutcome] = []
        try:
            for result_name, service_type, method_name in JOB_SPECS:
                await lease.assert_held()
                outcomes.append(
                    await self._run_job(
                        result_name=result_name,
                        service_type=service_type,
                        method_name=method_name,
                    )
                )
        finally:
            await lease.release()

        completed = datetime.now(timezone.utc)
        result = SchedulerCycleResult(
            acquired=True,
            started_at=cycle_started.isoformat(),
            completed_at=completed.isoformat(),
            duration_ms=int((time.monotonic() - monotonic_started) * 1000),
            jobs=tuple(outcomes),
        )
        self.last_cycle = result
        logger.info(
            "scheduler_cycle_completed",
            extra={
                "duration_ms": result.duration_ms,
                "jobs_total": len(result.jobs),
                "jobs_failed": sum(not job.ok for job in result.jobs),
            },
        )
        return result

    async def run_once(self) -> dict[str, object]:
        return (await self.run_cycle()).legacy_results()

    async def run_forever(self):
        while True:
            started = time.monotonic()
            try:
                cycle = await self.run_cycle()
                elapsed = time.monotonic() - started
                delay = (
                    SCHEDULER_RETRY_SECONDS
                    if cycle.acquired and not cycle.ok
                    else self.interval_seconds
                )
                await asyncio.sleep(max(1.0, delay - elapsed))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("scheduler_cycle_crashed")
                await asyncio.sleep(SCHEDULER_RETRY_SECONDS)
