from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import settings


@lru_cache(maxsize=8)
def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(name or "").strip())
    except ZoneInfoNotFoundError as error:
        raise RuntimeError(f"Unknown BUSINESS_TIMEZONE: {name!r}") from error


def to_business_timezone(value: datetime) -> datetime:
    """Convert persisted UTC datetime to the configured outward-facing zone."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_zone(settings.business_timezone))


def format_business_datetime(
    value: datetime | None,
    *,
    pattern: str = "%d.%m.%Y %H:%M",
    include_label: bool = True,
    empty: str = "время не указано",
) -> str:
    if value is None:
        return empty
    rendered = to_business_timezone(value).strftime(pattern)
    label = str(settings.business_timezone_label or "").strip()
    if include_label and label:
        return f"{rendered} {label}"
    return rendered


__all__ = ["format_business_datetime", "to_business_timezone"]
