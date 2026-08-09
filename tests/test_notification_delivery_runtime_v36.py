from pathlib import Path

from app.api.notification_center import router as notification_center_router
from app.scheduler.notification_dispatcher import (
    DEFAULT_NOTIFICATION_INTERVAL_SECONDS,
)
from app.scheduler.scheduler import DEFAULT_INTERVAL_SECONDS


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_failed_delivery_recovery_routes_are_registered_through_main_router():
    paths = {route.path for route in notification_center_router.routes}

    assert "/notification-center/ui" in paths
    assert "/notification-center/status" in paths
    assert "/admin/notification-delivery/ui" in paths
    assert "/admin/notification-delivery/data" in paths
    assert "/admin/notification-delivery/{notification_id}/retry" in paths


def test_notification_dispatcher_is_fast_but_heavy_scheduler_stays_hourly():
    assert DEFAULT_NOTIFICATION_INTERVAL_SECONDS == 60
    assert DEFAULT_INTERVAL_SECONDS == 60 * 60


def test_process_supervises_notification_dispatcher_without_replacing_scheduler():
    source = read("app/process.py")

    assert 'name="notification-dispatcher"' in source
    assert "factory=notification_dispatcher.run_forever" in source
    assert 'BackgroundService(name="scheduler", factory=scheduler.run_forever)' in source
    assert "if settings.run_scheduler:" in source


def test_durable_dispatcher_commits_delivery_separately_from_business_mutations():
    source = read("app/scheduler/notification_dispatcher.py")
    sender = read("app/domain/notifications/notification_sender.py")

    assert "async with AsyncSessionLocal() as db:" in source
    assert "NotificationSender(db).send_pending" in source
    assert "await db.commit()" in source
    assert "await db.rollback()" in source
    assert "timeout=self.interval_seconds" in source
    assert "self.last_sent_count = int(sent_count)" in source
    assert "async def send_pending(self, limit: int = 50) -> int:" in sender
    assert 'return int(summary["sent"])' in sender
    assert "dict(sent_count)" not in source
