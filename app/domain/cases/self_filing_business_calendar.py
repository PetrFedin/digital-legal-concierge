from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.system.settings_service import SettingsService


class BusinessCalendarError(ValueError):
    pass


@dataclass(frozen=True)
class BusinessCalendarSnapshot:
    timezone: str
    coverage_through: date
    non_working_dates: frozenset[date]
    additional_working_dates: frozenset[date]


def _parse_dates(raw: object, *, label: str) -> frozenset[date]:
    if raw in (None, ""):
        return frozenset()
    if not isinstance(raw, (list, tuple)):
        raise BusinessCalendarError(f"{label}: ожидается список дат")
    result: set[date] = set()
    for item in raw:
        try:
            result.add(date.fromisoformat(str(item)))
        except ValueError as error:
            raise BusinessCalendarError(
                f"{label}: некорректная дата {item!r}"
            ) from error
    return frozenset(result)


async def load_business_calendar(db) -> BusinessCalendarSnapshot:
    service = SettingsService(db)
    raw_coverage = await service.get_value(
        "self_filing.business_calendar_coverage_through"
    )
    if not str(raw_coverage or "").strip():
        raise BusinessCalendarError(
            "Календарь рабочих дней для услуги не подтверждён. "
            "Укажите self_filing.business_calendar_coverage_through."
        )
    try:
        coverage = date.fromisoformat(str(raw_coverage))
    except ValueError as error:
        raise BusinessCalendarError(
            "Календарь рабочих дней содержит некорректную дату покрытия"
        ) from error

    non_working = _parse_dates(
        await service.get_value("self_filing.non_working_dates"),
        label="Дополнительные нерабочие даты",
    )
    additional_working = _parse_dates(
        await service.get_value("self_filing.additional_working_dates"),
        label="Дополнительные рабочие даты",
    )
    overlap = non_working & additional_working
    if overlap:
        shown = ", ".join(sorted(item.isoformat() for item in overlap))
        raise BusinessCalendarError(
            "Одна дата одновременно отмечена рабочей и нерабочей: " + shown
        )
    return BusinessCalendarSnapshot(
        timezone=settings.business_timezone,
        coverage_through=coverage,
        non_working_dates=non_working,
        additional_working_dates=additional_working,
    )


def is_business_day(day: date, calendar: BusinessCalendarSnapshot) -> bool:
    if day > calendar.coverage_through:
        raise BusinessCalendarError(
            "Календарь рабочих дней не покрывает дату " + day.isoformat()
        )
    if day in calendar.additional_working_dates:
        return True
    if day in calendar.non_working_dates:
        return False
    return day.weekday() < 5


def add_business_days(
    started_at: datetime,
    *,
    business_days: int,
    calendar: BusinessCalendarSnapshot,
) -> datetime:
    """Return the same local wall-clock time after N subsequent business days.

    The starting date is day zero. This avoids inventing an unstated business
    cut-off; the service promise is measured from the exact moment both payment
    and lawyer-confirmed completeness exist.
    """

    count = int(business_days)
    if count < 1:
        raise BusinessCalendarError("Количество рабочих дней должно быть положительным")

    zone = ZoneInfo(calendar.timezone)
    if started_at.tzinfo is None:
        local_started = started_at.replace(tzinfo=zone)
    else:
        local_started = started_at.astimezone(zone)

    cursor = local_started.date()
    accepted = 0
    while accepted < count:
        cursor += timedelta(days=1)
        if is_business_day(cursor, calendar):
            accepted += 1

    local_due = datetime.combine(
        cursor,
        time(
            local_started.hour,
            local_started.minute,
            local_started.second,
            local_started.microsecond,
        ),
        tzinfo=zone,
    )
    return local_due.astimezone(started_at.tzinfo or zone)


__all__ = [
    "BusinessCalendarError",
    "BusinessCalendarSnapshot",
    "add_business_days",
    "is_business_day",
    "load_business_calendar",
]
