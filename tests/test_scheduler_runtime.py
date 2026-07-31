from __future__ import annotations

from pathlib import Path

import pytest

import app.scheduler.jobs as jobs_module
import app.scheduler.scheduler as scheduler_module
from app.config import settings
from app.scheduler.jobs import SchedulerJobs
from app.scheduler.lease import SchedulerCycleLease, SchedulerLeaseError
from app.scheduler.scheduler import AppScheduler
from app.security.backup_freshness import BackupFreshnessStatus


@pytest.mark.asyncio
async def test_sqlite_scheduler_lease_is_nonblocking_and_reusable(tmp_path: Path):
    first = SchedulerCycleLease(dialect_name="sqlite", lock_dir=tmp_path)
    second = SchedulerCycleLease(dialect_name="sqlite", lock_dir=tmp_path)

    assert await first.acquire() is True
    await first.assert_held()
    assert await second.acquire() is False

    await first.release()
    assert await second.acquire() is True
    await second.assert_held()
    await second.release()


@pytest.mark.asyncio
async def test_scheduler_lease_context_manager_rejects_busy_lock(tmp_path: Path):
    first = SchedulerCycleLease(dialect_name="sqlite", lock_dir=tmp_path)
    second = SchedulerCycleLease(dialect_name="sqlite", lock_dir=tmp_path)
    assert await first.acquire() is True

    with pytest.raises(SchedulerLeaseError, match="занят"):
        async with second:
            raise AssertionError("busy lease must not enter the context")

    await first.release()


class FakeSession:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeSchedulerJobs:
    def __init__(self, db):
        self.db = db

    async def first(self):
        return {"value": 1}

    async def broken(self):
        raise RuntimeError("intentional scheduler job failure")

    async def last(self):
        return {"value": 3}


@pytest.mark.asyncio
async def test_scheduler_isolates_job_transactions_and_continues_after_failure(
    monkeypatch,
):
    sessions: list[FakeSession] = []

    def session_factory():
        session = FakeSession()
        sessions.append(session)
        return session

    class FakeLease:
        instance = None

        def __init__(self):
            self.assertions = 0
            self.released = False
            FakeLease.instance = self

        async def acquire(self):
            return True

        async def assert_held(self):
            self.assertions += 1

        async def release(self):
            self.released = True

    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", session_factory)
    monkeypatch.setattr(scheduler_module, "SchedulerJobs", FakeSchedulerJobs)
    monkeypatch.setattr(scheduler_module, "SchedulerCycleLease", FakeLease)
    monkeypatch.setattr(
        scheduler_module,
        "JOB_SPECS",
        (
            ("first", "scheduler", "first"),
            ("broken", "scheduler", "broken"),
            ("last", "scheduler", "last"),
        ),
    )

    result = await AppScheduler().run_cycle()

    assert result.acquired is True
    assert result.ok is False
    assert [job.name for job in result.jobs] == ["first", "broken", "last"]
    assert [job.ok for job in result.jobs] == [True, False, True]
    assert result.jobs[1].error_type == "RuntimeError"
    assert len(sessions) == 3
    assert (sessions[0].commits, sessions[0].rollbacks) == (1, 0)
    assert (sessions[1].commits, sessions[1].rollbacks) == (0, 1)
    assert (sessions[2].commits, sessions[2].rollbacks) == (1, 0)
    assert FakeLease.instance.assertions == 3
    assert FakeLease.instance.released is True


@pytest.mark.asyncio
async def test_scheduler_skips_without_opening_sessions_when_lease_is_busy(
    monkeypatch,
):
    class BusyLease:
        released = False

        async def acquire(self):
            return False

        async def release(self):
            BusyLease.released = True

    def forbidden_session():
        raise AssertionError("session must not be opened when scheduler lease is busy")

    monkeypatch.setattr(scheduler_module, "SchedulerCycleLease", BusyLease)
    monkeypatch.setattr(scheduler_module, "AsyncSessionLocal", forbidden_session)

    scheduler = AppScheduler()
    cycle = await scheduler.run_cycle()
    legacy = cycle.legacy_results()

    assert cycle.acquired is False
    assert cycle.jobs == ()
    assert legacy == {
        "scheduler_acquired": False,
        "scheduler_skipped": "singleton_lease_held",
    }
    assert BusyLease.released is False


def freshness(*, ok: bool, reason: str | None = None) -> BackupFreshnessStatus:
    return BackupFreshnessStatus(
        required=True,
        ok=ok,
        verified=ok,
        archive="latest.dlcbak" if ok else None,
        created_at="2026-07-31T08:00:00+00:00" if ok else None,
        age_seconds=3600 if ok else None,
        max_age_seconds=26 * 3600,
        archives_seen=1 if ok else 0,
        rejected_archives=0,
        cache_hit=False,
        reason=reason,
    )


def scheduler_jobs_without_database() -> SchedulerJobs:
    instance = object.__new__(SchedulerJobs)
    instance.db = None
    instance.notifications = None
    return instance


@pytest.mark.asyncio
async def test_automatic_backup_skips_when_verified_copy_is_fresh(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "automatic_encrypted_backups_enabled", True)
    monkeypatch.setattr(
        jobs_module,
        "backup_freshness_status",
        lambda **kwargs: freshness(ok=True),
    )
    monkeypatch.setattr(
        jobs_module,
        "_create_encrypted_backup",
        lambda: (_ for _ in ()).throw(AssertionError("backup must be skipped")),
    )

    result = await scheduler_jobs_without_database().create_encrypted_backup_if_due()

    assert result["created"] is False
    assert result["reason"] == "fresh_backup_exists"
    assert result["archive"] == "latest.dlcbak"


@pytest.mark.asyncio
async def test_automatic_backup_creates_verified_copy_when_rpo_is_not_met(
    monkeypatch,
):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "automatic_encrypted_backups_enabled", True)
    monkeypatch.setattr(
        jobs_module,
        "backup_freshness_status",
        lambda **kwargs: freshness(
            ok=False,
            reason="no_verified_restorable_backup",
        ),
    )
    monkeypatch.setattr(
        jobs_module,
        "_create_encrypted_backup",
        lambda: {
            "created": True,
            "archive": "new.dlcbak",
            "verified": True,
        },
    )

    result = await scheduler_jobs_without_database().create_encrypted_backup_if_due()

    assert result == {
        "created": True,
        "archive": "new.dlcbak",
        "verified": True,
    }


@pytest.mark.asyncio
async def test_automatic_backup_fails_closed_for_invalid_restore_policy(
    monkeypatch,
):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "automatic_encrypted_backups_enabled", True)
    monkeypatch.setattr(
        jobs_module,
        "backup_freshness_status",
        lambda **kwargs: freshness(ok=False, reason="restore_fence_invalid"),
    )

    with pytest.raises(RuntimeError, match="restore_fence_invalid"):
        await scheduler_jobs_without_database().create_encrypted_backup_if_due()


def test_automatic_backup_is_the_first_scheduler_job():
    assert scheduler_module.JOB_SPECS[0] == (
        "encrypted_backup",
        "scheduler",
        "create_encrypted_backup_if_due",
    )
    assert scheduler_module.JOB_SPECS[-1] == (
        "sent_notifications",
        "sender",
        "send_pending",
    )
