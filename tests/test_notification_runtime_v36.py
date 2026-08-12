from __future__ import annotations

from pathlib import Path

import pytest

import app.scheduler.notification_dispatcher as dispatcher_module
from app.scheduler.notification_dispatcher import NotificationDispatcher
from app.scheduler.scheduler import DEFAULT_INTERVAL_SECONDS as SCHEDULER_INTERVAL_SECONDS


class FakeSession:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0

    async def commit(self) -> None:
        self.commit_calls += 1

    async def rollback(self) -> None:
        self.rollback_calls += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> FakeSession:
        return self.session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class FakeSender:
    def __init__(self, db: FakeSession, *, sent: int = 0, error: Exception | None = None):
        self.db = db
        self.sent = sent
        self.error = error
        self.limits: list[int] = []

    async def send_pending(self, limit: int = 50) -> int:
        self.limits.append(limit)
        if self.error is not None:
            raise self.error
        return self.sent


@pytest.mark.asyncio
async def test_notification_dispatcher_commits_successful_cycle_and_tracks_int_result(monkeypatch):
    session = FakeSession()
    sender_holder: dict[str, FakeSender] = {}

    monkeypatch.setattr(
        dispatcher_module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    def sender_factory(db):
        sender = FakeSender(db, sent=3)
        sender_holder["sender"] = sender
        return sender

    dispatcher = NotificationDispatcher(sender_factory=sender_factory)
    result = await dispatcher._run_once()

    assert result == 3
    assert dispatcher.last_result == 3
    assert sender_holder["sender"].db is session
    assert sender_holder["sender"].limits == [100]
    assert session.commit_calls == 1
    assert session.rollback_calls == 0


@pytest.mark.asyncio
async def test_notification_dispatcher_rolls_back_failed_cycle(monkeypatch):
    session = FakeSession()

    monkeypatch.setattr(
        dispatcher_module,
        "AsyncSessionLocal",
        lambda: FakeSessionContext(session),
    )

    dispatcher = NotificationDispatcher(
        sender_factory=lambda db: FakeSender(
            db,
            error=RuntimeError("telegram delivery failed"),
        )
    )

    with pytest.raises(RuntimeError, match="telegram delivery failed"):
        await dispatcher._run_once()

    assert dispatcher.last_result is None
    assert session.commit_calls == 0
    assert session.rollback_calls == 1


@pytest.mark.asyncio
async def test_notification_dispatcher_retries_after_cycle_error(monkeypatch):
    dispatcher = NotificationDispatcher(interval_seconds=1)
    run_calls = 0
    sleep_calls: list[int] = []

    async def fake_run_once() -> int:
        nonlocal run_calls
        run_calls += 1
        if run_calls == 1:
            raise RuntimeError("temporary notification error")
        dispatcher._running = False
        return 0

    async def fake_sleep(seconds: int) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(dispatcher, "_run_once", fake_run_once)
    monkeypatch.setattr(dispatcher_module.asyncio, "sleep", fake_sleep)

    await dispatcher.run_forever()

    assert run_calls == 2
    assert sleep_calls == [1]
    assert dispatcher.is_running is False


def test_notification_dispatcher_is_separate_from_hourly_heavy_scheduler():
    process_source = Path("app/process.py").read_text(encoding="utf-8")
    sender_source = Path(
        "app/domain/notifications/notification_sender.py"
    ).read_text(encoding="utf-8")

    assert NotificationDispatcher.DEFAULT_INTERVAL_SECONDS == 60
    assert SCHEDULER_INTERVAL_SECONDS == 60 * 60
    assert 'name="notification-dispatcher"' in process_source
    assert "notification_dispatcher.run_forever" in process_source
    assert 'name="scheduler"' in process_source
    assert "scheduler.run_forever" in process_source
    assert ".with_for_update(skip_locked=True)" in sender_source
