"""Dated legal-period arithmetic for preliminary DDU 214-FZ estimates.

The 2024-03-22..2025-12-31 exclusion is based on Government Decree
No. 326 (18.03.2024), as amended by No. 2227 (30.12.2025).
https://base.garant.ru/413396336/

This source-controlled candidate is NOT legal sign-off. Earlier chargeable
periods need counsel-approved historical rate caps and exceptional rules.
It deliberately fails closed rather than multiplying all days by one rate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ExcludedPeriod:
    start: date
    end: date
    source: str


@dataclass(frozen=True)
class PeriodBreakdown:
    calendar_delay_days: int
    excluded_days: int
    accrual_days: int
    rules_version: str


class ManualLegalReviewRequired(ValueError):
    """No reliable automatic monetary estimate without approved dated rules."""


@dataclass(frozen=True)
class PeriodRules:
    version: str
    automatic_supported_from: date
    excluded: tuple[ExcludedPeriod, ...]

    def breakdown(self, planned_transfer_date: date, end_date: date) -> PeriodBreakdown:
        raw_days = max((end_date - planned_transfer_date).days, 0)
        if not raw_days:
            return PeriodBreakdown(0, 0, 0, self.version)

        period_start = planned_transfer_date + timedelta(days=1)
        if period_start < self.automatic_supported_from:
            raise ManualLegalReviewRequired(
                "Просрочка началась до 22.03.2024. Для этих периодов требуется "
                "индивидуальная проверка ставки, ограничений и исключений юристом."
            )

        overlaps: list[tuple[date, date]] = []
        for item in sorted(self.excluded, key=lambda value: value.start):
            start = max(period_start, item.start)
            end = min(end_date, item.end)
            if start <= end:
                overlaps.append((start, end))

        # Merge overlapping intervals to avoid accidentally subtracting a day twice.
        excluded_days = 0
        merged_start: date | None = None
        merged_end: date | None = None
        for start, end in overlaps:
            if merged_start is None:
                merged_start, merged_end = start, end
            elif merged_end is not None and start <= merged_end + timedelta(days=1):
                merged_end = max(merged_end, end)
            else:
                assert merged_end is not None
                excluded_days += (merged_end - merged_start).days + 1
                merged_start, merged_end = start, end
        if merged_start is not None and merged_end is not None:
            excluded_days += (merged_end - merged_start).days + 1

        return PeriodBreakdown(
            calendar_delay_days=raw_days,
            excluded_days=excluded_days,
            accrual_days=raw_days - excluded_days,
            rules_version=self.version,
        )


# Versioned candidate. Replacing this source configuration is a separate
# reviewed release step; an admin-editable DB rule registry is still pending.
PRELIMINARY_2026_RULES = PeriodRules(
    version="ru-214fz-326-2227-candidate-2026-10-24",
    automatic_supported_from=date(2024, 3, 22),
    excluded=(
        ExcludedPeriod(
            start=date(2024, 3, 22),
            end=date(2025, 12, 31),
            source="RU Government Decree 326, amendment 2227",
        ),
    ),
)
