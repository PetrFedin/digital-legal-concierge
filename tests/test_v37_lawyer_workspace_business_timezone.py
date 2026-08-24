from datetime import datetime, timezone
from pathlib import Path

from app.api.lawyer_product import _business_schedule_note, _business_today
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lawyer_workspace_today_uses_business_date(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")
    now = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)
    scheduled = datetime(2026, 8, 24, 21, 45, tzinfo=timezone.utc)

    assert _business_today(scheduled, now=now) is True


def test_lawyer_workspace_schedule_note_uses_business_formatter(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")
    monkeypatch.setattr(settings, "business_timezone_label", "МСК")
    scheduled = datetime(2026, 8, 24, 21, 45, tzinfo=timezone.utc)

    note = _business_schedule_note(scheduled)
    assert note is not None
    assert note.startswith("Назначено на ")
    assert "МСК" in note
    assert "T21:45" not in note


def test_lawyer_workspace_product_owns_business_timezone_normalization():
    source = read("app/api/lawyer_product.py")

    assert "to_business_timezone(scheduled_at).date()" in source
    assert "to_business_timezone(current).date()" in source
    assert "format_business_datetime(scheduled_at)" in source
    assert "business_timezone_guided_workspace_data" in source
    assert '"/lawyer/workspace/data"' in source
    route = source.split('router.add_api_route(\n    "/lawyer/workspace/data"', 1)[1].split(
        "router.add_api_route(", 1
    )[0]
    assert "business_timezone_guided_workspace_data" in route
    assert "guided_workspace_data," not in route


def test_lawyer_workspace_recomputes_booked_m2_priority_without_overriding_unread_message():
    source = read("app/api/lawyer_product.py")
    wrapper = source.split("async def business_timezone_guided_workspace_data", 1)[1].split(
        'router.add_api_route(\n    "/lawyer/ui"', 1
    )[0]

    assert 'ConsultationStatus.BOOKED.value' in wrapper
    assert 'item["consultation_today"] = is_today' in wrapper
    assert 'item["consultation_today_at"] = scheduled_at if is_today else None' in wrapper
    assert 'int(item.get("unread_client_messages") or 0) > 0' in wrapper
    assert '"Открыть консультацию" if is_today else "Подготовиться к консультации"' in wrapper
    assert 'item["priority"] = "high" if is_today else "normal"' in wrapper
    assert 'item["action_note"] = _business_schedule_note(scheduled_at)' in wrapper
    assert 'payload["summary"] = _rebuild_workspace_summary(cases)' in wrapper
