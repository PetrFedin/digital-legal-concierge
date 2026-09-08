from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.document import Document
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

router = APIRouter(prefix="/admin/export", tags=["admin-export"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


def _csv_cell(value):
    if value is None:
        return ""
    if not isinstance(value, str):
        return value
    # Spreadsheet programs may execute cells starting with formula sigils. A
    # bulk export is data, never a spreadsheet command channel.
    stripped = value.lstrip()
    if stripped.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def csv_response(filename: str, rows: list[dict]) -> StreamingResponse:
    buffer = io.StringIO(newline="")
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(
            {key: _csv_cell(value) for key, value in row.items()}
            for row in rows
        )
    else:
        buffer.write("empty\n")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "no-store",
        },
    )


async def _audit_export(
    db: AsyncSession,
    *,
    actor_id: int,
    export_kind: str,
    row_count: int,
) -> None:
    db.add(
        AuditLog(
            actor_type="admin",
            actor_id=actor_id,
            action="ADMIN_BULK_EXPORT_CREATED",
            entity_type="export",
            entity_id=None,
            old_value=None,
            new_value={"kind": export_kind, "row_count": int(row_count)},
            comment="Создана административная CSV-выгрузка",
        )
    )
    await db.commit()


@router.get("/cases.csv")
async def export_cases(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    result = await db.execute(select(Case).order_by(Case.created_at.desc()).limit(5000))
    rows = [
        {
            "id": item.id,
            "case_number": item.case_number,
            "client_id": item.client_id,
            "route": item.route,
            "status": item.status,
            "lawyer_id": item.assigned_lawyer_id,
            "next_action": item.next_action,
            "created_at": item.created_at.isoformat() if item.created_at else "",
            "updated_at": item.updated_at.isoformat() if item.updated_at else "",
        }
        for item in result.scalars().all()
    ]
    await _audit_export(db, actor_id=int(actor.account_id), export_kind="cases", row_count=len(rows))
    return csv_response("cases.csv", rows)


@router.get("/clients.csv")
async def export_clients(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(5000))
    rows = [
        {
            "id": item.id,
            "telegram_id": item.telegram_id,
            "telegram_username": item.telegram_username,
            "full_name": item.full_name,
            "phone": item.phone,
            "email": item.email,
            "created_at": item.created_at.isoformat() if item.created_at else "",
        }
        for item in result.scalars().all()
    ]
    await _audit_export(db, actor_id=int(actor.account_id), export_kind="clients", row_count=len(rows))
    return csv_response("clients.csv", rows)


@router.get("/payments.csv")
async def export_payments(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    result = await db.execute(select(Payment).order_by(Payment.created_at.desc()).limit(5000))
    rows = [
        {
            "id": item.id,
            "case_id": item.case_id,
            "code": item.payment_code,
            "title": item.title,
            "amount": float(item.amount),
            "currency": item.currency,
            "status": item.status,
            "provider": item.provider,
            "provider_payment_id": item.provider_payment_id,
            "created_at": item.created_at.isoformat() if item.created_at else "",
        }
        for item in result.scalars().all()
    ]
    await _audit_export(db, actor_id=int(actor.account_id), export_kind="payments", row_count=len(rows))
    return csv_response("payments.csv", rows)


@router.get("/documents.csv")
async def export_documents(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    result = await db.execute(select(Document).order_by(Document.created_at.desc()).limit(5000))
    rows = [
        {
            "id": item.id,
            "case_id": item.case_id,
            "type": item.document_type,
            "title": item.title,
            "file_name": item.file_name,
            "status": item.status,
            "version": item.version,
            "created_at": item.created_at.isoformat() if item.created_at else "",
        }
        for item in result.scalars().all()
    ]
    await _audit_export(db, actor_id=int(actor.account_id), export_kind="documents", row_count=len(rows))
    return csv_response("documents.csv", rows)
