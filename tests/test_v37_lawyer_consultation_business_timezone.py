from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.lawyer_consultation_desk import _timing_payload
from app.config import settings
from app.domain.statuses.consultation_statuses import ConsultationStatus

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_lawyer_consultation_today_uses_business_date_not_utc_date(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")
    # 21:30 UTC is already the next calendar day in Moscow.
    now = datetime(2026, 8, 24, 21, 30, tzinfo=timezone.utc)
    consultation = SimpleNamespace(
        status=ConsultationStatus.BOOKED,
        scheduled_at=datetime(2026, 8, 24, 21, 45, tzinfo=timezone.utc),
    )

    result = _timing_payload(consultation, None, now)

    assert result["is_today"] is True


def test_lawyer_consultation_payload_exposes_business_timezone_contract():
    source = read("app/api/lawyer_consultation_desk.py")

    assert "from app.config import settings" in source
    assert "from app.presentation_time import to_business_timezone" in source
    assert '"business_timezone": settings.business_timezone' in source
    assert '"business_timezone_label": settings.business_timezone_label' in source
    assert "_business_date(scheduled_at) == business_today" in source
    assert "scheduled_at.date() == now.date()" not in source


def test_lawyer_consultation_browser_uses_server_business_timezone():
    source = read("app/api/lawyer_consultation_desk.py")

    assert "businessTimeZone='Europe/Moscow'" in source
    assert "timeZone:businessTimeZone" in source
    assert "businessTimeZone=data.business_timezone||businessTimeZone" in source
    assert "businessTimeLabel=data.business_timezone_label??businessTimeLabel" in source
    assert "сегодня (${esc(businessTimeLabel||businessTimeZone)})" in source
