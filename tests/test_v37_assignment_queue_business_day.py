from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_consultation_queue_uses_configured_business_day_not_utc_calendar_day():
    source = read("app/api/assignment_queue.py")

    queue = source.split("async def consultation_queue_with_slot_lawyer", 1)[1]
    assert "business_now = to_business_timezone(now)" in queue
    assert "business_date = business_now.date()" in queue
    assert "datetime.combine(business_date, time.min, tzinfo=business_zone)" in queue
    assert "business_date + timedelta(days=1)" in queue
    assert ".astimezone(timezone.utc)" in queue
    assert "Consultation.scheduled_at >= today_start" in queue
    assert "Consultation.scheduled_at < tomorrow_start" in queue


def test_consultation_queue_exposes_business_day_context_for_staff_clients():
    source = read("app/api/assignment_queue.py")

    queue = source.split("async def consultation_queue_with_slot_lawyer", 1)[1]
    assert '"business_date": business_date.isoformat()' in queue
    assert '"business_timezone": settings.business_timezone' in queue
    assert '"business_timezone_label": settings.business_timezone_label' in queue
