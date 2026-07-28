from app.config import settings
from app.scheduler.scheduler import AppScheduler


def test_scheduler_uses_configured_cadence_by_default():
    scheduler = AppScheduler()

    assert scheduler.interval_seconds == max(
        30,
        settings.scheduler_interval_seconds,
    )
    assert scheduler.interval_seconds <= 60


def test_scheduler_accepts_safe_explicit_override():
    assert AppScheduler(interval_seconds=120).interval_seconds == 120
    assert AppScheduler(interval_seconds=1).interval_seconds == 30
