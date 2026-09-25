from __future__ import annotations

from typing import Any

from fastapi import Depends, Header, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.workdesk_projections import _admin_ui_gate
from app.db.session import get_db
from app.models.calculation import Calculation
from app.models.calculation_intake import CalculationIntake
from app.models.case import Case


def _date(value):
    return value.isoformat() if value is not None else None


def _decimal(value):
    return str(value) if value is not None else None


def _add_refs(target: list[str], raw: object) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        code = str(item or "").strip()
        if code and code not in target:
            target.append(code)


def _calculation_source_codes(calculation: Calculation) -> list[str]:
    """Return only sources actually used by this calculation projection."""

    snapshot = calculation.rule_snapshot or {}
    if not isinstance(snapshot, dict):
        return []

    refs: list[str] = []
    formula = snapshot.get("formula")
    if isinstance(formula, dict):
        _add_refs(refs, formula.get("source_refs"))

    rate_policy = snapshot.get("rate_policy")
    if isinstance(rate_policy, dict):
        _add_refs(refs, rate_policy.get("source_refs"))

    client_types = snapshot.get("client_types")
    client_type = str(calculation.client_type or "")
    if isinstance(client_types, dict):
        client_rule = client_types.get(client_type)
        if isinstance(client_rule, dict):
            _add_refs(refs, client_rule.get("source_refs"))

    if bool(getattr(calculation, "unique_object", False)):
        unique_rule = snapshot.get("unique_object")
        if isinstance(unique_rule, dict):
            _add_refs(refs, unique_rule.get("source_refs"))

    for segment in list(calculation.applied_segments or []):
        if not isinstance(segment, dict):
            continue
        _add_refs(refs, segment.get("base_rate_source_refs"))
        _add_refs(refs, segment.get("cap_source_refs"))

    for segment in list(getattr(calculation, "excluded_segments", None) or []):
        if isinstance(segment, dict):
            _add_refs(refs, segment.get("source_refs"))
    return refs


def _source_projection(calculation: Calculation) -> list[dict[str, Any]]:
    snapshot = calculation.rule_snapshot or {}
    sources = snapshot.get("sources") if isinstance(snapshot, dict) else None
    if not isinstance(sources, dict):
        return []

    items: list[dict[str, Any]] = []
    for code in _calculation_source_codes(calculation):
        raw = sources.get(code)
        if not isinstance(raw, dict):
            # Missing source is itself important evidence for staff. Do not
            # fabricate a URL or silently hide the integrity problem.
            items.append(
                {
                    "code": code,
                    "title": "Источник отсутствует в сохранённом снимке",
                    "locator": None,
                    "url": None,
                    "checked_at": None,
                    "integrity_ok": False,
                }
            )
            continue
        url = str(raw.get("url") or "").strip()
        items.append(
            {
                "code": code,
                "title": str(raw.get("title") or code),
                "locator": str(raw.get("locator") or "") or None,
                "url": url if url.startswith("https://") else None,
                "checked_at": str(raw.get("checked_at") or "") or None,
                "integrity_ok": bool(url.startswith("https://")),
            }
        )
    return items


def _segment_projection(raw_segments: object, *, excluded: bool = False) -> list[dict]:
    if not isinstance(raw_segments, list):
        return []
    items: list[dict] = []
    for raw in raw_segments:
        if not isinstance(raw, dict):
            continue
        if excluded:
            items.append(
                {
                    "code": raw.get("code"),
                    "start": raw.get("start"),
                    "end": raw.get("end"),
                    "days": raw.get("days"),
                    "source_refs": list(raw.get("source_refs") or []),
                }
            )
            continue
        items.append(
            {
                "start": raw.get("start"),
                "end": raw.get("end"),
                "days": raw.get("days"),
                "base_rate": raw.get("base_rate"),
                "rate": raw.get("rate"),
                "cap": raw.get("cap"),
                "multiplier": raw.get("multiplier"),
                "amount_before_final_rounding": raw.get(
                    "amount_before_final_rounding"
                ),
                "base_rate_source_refs": list(
                    raw.get("base_rate_source_refs") or []
                ),
                "cap_source_refs": list(raw.get("cap_source_refs") or []),
            }
        )
    return items


async def guarded_workdesk_case_calculator(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Project client facts and immutable legal calculation evidence separately.

    Routine staff never receives the raw rule snapshot. Instead, the endpoint
    exposes the exact applied segments and only the legal sources referenced by
    this calculation, including their saved URLs and verification dates.
    """

    gate = await _admin_ui_gate(request, db, x_admin_token)
    if isinstance(gate, RedirectResponse):
        return gate

    case = await db.get(Case, int(case_id))
    if case is None:
        raise HTTPException(status_code=404, detail="Дело не найдено")

    intake = (
        await db.execute(
            select(CalculationIntake).where(CalculationIntake.case_id == int(case_id))
        )
    ).scalar_one_or_none()
    calculation = (
        await db.execute(
            select(Calculation)
            .where(Calculation.case_id == int(case_id))
            .order_by(Calculation.created_at.desc(), Calculation.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    intake_payload = None
    if intake is not None:
        intake_payload = {
            "status": intake.status,
            "current_step": intake.current_step,
            "version": intake.version,
            "contract_price": _decimal(intake.contract_price),
            "planned_transfer_date": _date(intake.planned_transfer_date),
            "object_transferred": intake.object_transferred,
            "actual_transfer_date": _date(intake.actual_transfer_date),
            "client_type": getattr(intake, "client_type", None),
            "unique_object": getattr(intake, "unique_object", None),
            "manual_review_flags": list(
                getattr(intake, "manual_review_flags", None) or []
            ),
            "calculation_date": _date(intake.calculation_date),
            "completed_calculation_id": intake.completed_calculation_id,
            "completed_at": _date(intake.completed_at),
            "updated_at": _date(intake.updated_at),
        }

    calculation_payload = None
    if calculation is not None:
        calculation_payload = {
            "id": calculation.id,
            "contract_price": _decimal(calculation.contract_price),
            "planned_transfer_date": _date(calculation.planned_transfer_date),
            "object_transferred": calculation.object_transferred,
            "actual_transfer_date": _date(calculation.actual_transfer_date),
            "calculation_date": _date(calculation.calculation_date),
            "delay_days_total": (
                calculation.delay_days_total
                if calculation.delay_days_total is not None
                else calculation.delay_days
            ),
            "delay_days_chargeable": (
                calculation.delay_days_chargeable
                if calculation.delay_days_chargeable is not None
                else calculation.delay_days
            ),
            "moratorium_days": calculation.moratorium_days,
            "penalty_amount": _decimal(calculation.penalty_amount),
            "gross_penalty_amount": _decimal(
                getattr(calculation, "gross_penalty_amount", None)
            ),
            "amount_cap": _decimal(getattr(calculation, "amount_cap", None)),
            "amount_cap_applied": bool(
                getattr(calculation, "amount_cap_applied", False)
            ),
            "key_rate": _decimal(calculation.key_rate),
            "consumer_multiplier": _decimal(calculation.consumer_multiplier),
            "client_type": calculation.client_type,
            "unique_object": bool(
                getattr(calculation, "unique_object", False)
            ),
            "manual_review_required": bool(
                getattr(calculation, "manual_review_required", False)
            ),
            "manual_review_reasons": list(
                getattr(calculation, "manual_review_reasons", None) or []
            ),
            "rule_revision_id": calculation.rule_revision_id,
            "rule_revision_key": calculation.rule_revision_key,
            "rule_snapshot_sha256": calculation.rule_snapshot_sha256,
            "segment_count": len(calculation.applied_segments or []),
            "applied_segments": _segment_projection(
                calculation.applied_segments or []
            ),
            "excluded_segments": _segment_projection(
                getattr(calculation, "excluded_segments", None) or [],
                excluded=True,
            ),
            "sources": _source_projection(calculation),
            "is_preliminary": bool(calculation.is_preliminary),
            "created_at": _date(calculation.created_at),
        }

    return {
        "case_id": int(case_id),
        "intake": intake_payload,
        "latest_calculation": calculation_payload,
        "evidence_note": (
            "Показаны только фактически применённые периоды и связанные с ними "
            "правовые/расчётные источники из неизменяемого снимка расчёта. "
            "Полный внутренний JSON правил в рабочую карточку не выводится."
        ),
    }


__all__ = ["guarded_workdesk_case_calculator"]
