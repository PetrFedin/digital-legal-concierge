"""Canonical, JSON-safe snapshot of exactly the preliminary calculation shown.

This is a comparison receipt, not a legal certification or a second Case authority.
The stored snapshot must agree with a freshly checked calculation before a new
matter can be materialized. Decimal values are normalized for DB round-trips.
"""
from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from typing import Any


def _number(value: Any) -> str:
    return format(Decimal(str(value)).normalize(), "f")


def preview_input_facts(
    *,
    contract_price: Decimal,
    planned_transfer_date: Any,
    calculation_date: Any,
    object_transferred: bool,
    actual_transfer_date: Any,
) -> dict[str, str | bool | None]:
    """Stable representation of the facts supplied for the shown calculation."""
    return {
        "contract_price": _number(contract_price),
        "planned_transfer_date": planned_transfer_date.isoformat(),
        "calculation_date": calculation_date.isoformat(),
        "object_transferred": bool(object_transferred),
        "actual_transfer_date": (
            actual_transfer_date.isoformat() if actual_transfer_date is not None else None
        ),
    }


def preview_receipt(result: Any) -> dict[str, str | int | bool | None]:
    return {
        **preview_input_facts(
            contract_price=result.contract_price,
            planned_transfer_date=result.planned_transfer_date,
            calculation_date=result.calculation_date,
            object_transferred=result.object_transferred,
            actual_transfer_date=result.actual_transfer_date,
        ),
        "delay_days": int(result.delay_days),
        "key_rate": _number(result.key_rate),
        "consumer_multiplier": _number(result.consumer_multiplier),
        "penalty_amount": _number(result.penalty_amount),
        "formula_version": str(result.formula_version),
    }

def preview_receipt_fingerprint(receipt: dict) -> str:
    """Short identity tag used with the per-preview random callback token."""
    payload = json.dumps(
        receipt, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return sha256(payload).hexdigest()[:12]
