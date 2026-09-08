from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.api.consultation_slots import (
    _parse_business_local_datetime,
    _parse_slot_datetimes,
)
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_staff_datetime_local_is_interpreted_in_business_timezone(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")

    value = _parse_business_local_datetime("2026-08-25T10:00", "Начало")

    assert value == datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)


def test_dst_ambiguous_business_wall_time_fails_closed(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "America/New_York")

    with pytest.raises(HTTPException) as error:
        _parse_business_local_datetime("2026-11-01T01:30", "Начало")

    assert error.value.status_code == 400
    assert "неоднозначно" in str(error.value.detail)


def test_staff_schedule_ui_posts_wall_time_not_browser_timezone_conversion():
    source = read("app/api/consultation_slots.py")

    assert '"business_timezone": settings.business_timezone' in source
    assert '"business_timezone_label": settings.business_timezone_label' in source
    assert "starts_local:starts.value" in source
    assert "ends_local:ends.value" in source
    assert "new Date(starts.value).toISOString()" not in source
    assert "timeZone:businessTimeZone" in source
    assert "startsLabel.textContent='Начало ('+zoneLabel+')'" in source
    assert "endsLabel.textContent='Окончание ('+zoneLabel+')'" in source


def test_programmatic_aware_iso_payload_remains_supported(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")

    starts_at, ends_at = _parse_slot_datetimes(
        {
            "starts_at": "2026-08-25T10:00:00+03:00",
            "ends_at": "2026-08-25T11:00:00+03:00",
        }
    )

    assert starts_at == datetime(2026, 8, 25, 7, 0, tzinfo=timezone.utc)
    assert ends_at == datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
