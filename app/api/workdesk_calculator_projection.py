from __future__ import annotations

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


async def guarded_workdesk_case_calculator(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Project durable client inputs separately from immutable calculation evidence."""

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
            "client_type": calculation.client_type,
            "rule_revision_id": calculation.rule_revision_id,
            "rule_revision_key": calculation.rule_revision_key,
            "rule_snapshot_sha256": calculation.rule_snapshot_sha256,
            "segment_count": len(calculation.applied_segments or []),
            "is_preliminary": bool(calculation.is_preliminary),
            "created_at": _date(calculation.created_at),
        }

    return {
        "case_id": int(case_id),
        "intake": intake_payload,
        "latest_calculation": calculation_payload,
        "evidence_note": (
            "Полный snapshot правил не выводится в рабочую карточку; идентификатор ревизии и SHA-256 доступны для аудита."
        ),
    }


__all__ = ["guarded_workdesk_case_calculator"]
