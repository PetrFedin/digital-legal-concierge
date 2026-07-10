from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.consultation_slot import ConsultationSlot
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN, decode_access_token, normalize_roles

router = APIRouter(prefix="/consultation-slots", tags=["consultation-slots"])


def require_staff(token: str | None) -> dict:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or not roles.intersection({ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}):
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return payload


@router.get("")
async def list_slots(
    lawyer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    query = select(ConsultationSlot).order_by(ConsultationSlot.starts_at.asc())
    if lawyer_id is not None:
        query = query.where(ConsultationSlot.lawyer_id == lawyer_id)
    rows = (await db.execute(query)).scalars().all()
    return [
        {
            "id": row.id,
            "lawyer_id": row.lawyer_id,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
            "status": row.status,
            "hold_expires_at": row.hold_expires_at.isoformat() if row.hold_expires_at else None,
            "held_by_user_id": row.held_by_user_id,
            "consultation_id": row.consultation_id,
            "note": row.note,
        }
        for row in rows
    ]


@router.post("")
async def create_slot(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    try:
        lawyer_id = int(payload["lawyer_id"])
        starts_at = datetime.fromisoformat(str(payload["starts_at"]).replace("Z", "+00:00"))
        ends_at = datetime.fromisoformat(str(payload["ends_at"]).replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "Передайте lawyer_id, starts_at и ends_at в ISO-формате")
    if ends_at <= starts_at:
        raise HTTPException(400, "Время окончания должно быть позже времени начала")
    overlap = (
        await db.execute(
            select(ConsultationSlot).where(
                ConsultationSlot.lawyer_id == lawyer_id,
                ConsultationSlot.status.in_(["available", "held", "booked"]),
                ConsultationSlot.starts_at < ends_at,
                ConsultationSlot.ends_at > starts_at,
            )
        )
    ).scalars().first()
    if overlap:
        raise HTTPException(409, "У юриста уже есть пересекающийся слот")
    slot = ConsultationSlot(
        lawyer_id=lawyer_id,
        starts_at=starts_at,
        ends_at=ends_at,
        status="available",
        note=str(payload.get("note") or "").strip() or None,
    )
    db.add(slot)
    await db.commit()
    await db.refresh(slot)
    return {"ok": True, "id": slot.id}


@router.delete("/{slot_id}")
async def delete_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    slot = (await db.execute(select(ConsultationSlot).where(ConsultationSlot.id == slot_id))).scalars().first()
    if not slot:
        raise HTTPException(404, "Слот не найден")
    if slot.status in {"held", "booked"}:
        raise HTTPException(409, "Нельзя удалить удерживаемый или забронированный слот")
    await db.delete(slot)
    await db.commit()
    return {"ok": True}
