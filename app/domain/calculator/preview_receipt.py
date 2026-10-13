"""Canonical, JSON-safe snapshot of exactly the preliminary calculation shown.

This is a comparison receipt, not a legal certification or a second Case authority.
The stored snapshot must agree with a freshly checked calculation before a new
matter can be materialized. Decimal values are normalized for DB round-trips.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any


def _number(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def preview_receipt(result: Any) -> dict[str, str | int | bool | None]:
    return {
        "contract_price": _number(result.contract_price),
        "planned_transfer_date": result.planned_transfer_date.isoformat(),
        "calculation_date": result.calculation_date.isoformat(),
        "object_transferred": bool(result.object_transferred),
        "actual_transfer_date": (
            result.actual_transfer_date.isoformat()
            if result.actual_transfer_date is not None else None
        ),
        "delay_days": int(result.delay_days),
        "key_rate": _number(result.key_rate),
        "consumer_multiplier": _number(result.consumer_multiplier),
        "penalty_amount": _number(result.penalty_amount),
        "formula_version": str(result.formula_version),
    }
