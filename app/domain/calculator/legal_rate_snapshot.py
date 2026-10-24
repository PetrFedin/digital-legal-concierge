"""Frozen, source-attributed CBR key-rate observations for 2026 preview dates.

This is a short-term release candidate, not a network feed or legal sign-off.
Every date past the verified boundary is blocked until a new verified snapshot
is approved and deployed. Client input may not supply or override this rate.

Source: https://www.cbr.ru/hd_base/procstav/ir_chg_mpo/
23 Oct confirmation: https://www.cbr.ru/press/pr/?file=23102026_133000key.htm
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.domain.calculator.legal_periods import ManualLegalReviewRequired

RATE_SNAPSHOT_VERSION = "cbr-2026-10-23"
RATE_VERIFIED_THROUGH = date(2026, 10, 24)
# Effective dates and annual decimal fractions; the dates are inclusive.
_EFFECTIVE_RATES: tuple[tuple[date, Decimal], ...] = (
    (date(2026, 1, 1), Decimal("0.16")),
    (date(2026, 2, 16), Decimal("0.155")),
    (date(2026, 3, 23), Decimal("0.15")),
    (date(2026, 4, 27), Decimal("0.145")),
    (date(2026, 6, 22), Decimal("0.1425")),
    (date(2026, 7, 27), Decimal("0.14")),
)


def key_rate_for_execution_date(execution_date: date) -> Decimal:
    """Return source-verified rate; never silently extrapolate or guess."""
    if not date(2026, 1, 1) <= execution_date <= RATE_VERIFIED_THROUGH:
        raise ManualLegalReviewRequired(
            "Для даты исполнения вне проверенного периода ставок ЦБ "
            "нужна индивидуальная юридическая проверка и обновление справочника."
        )
    for effective, rate in reversed(_EFFECTIVE_RATES):
        if execution_date >= effective:
            return rate
    raise ManualLegalReviewRequired("Для выбранной даты отсутствует подтверждённая ставка.")
