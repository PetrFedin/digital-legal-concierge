from __future__ import annotations

import csv
import io
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.models.case import Case
from app.models.payment import Payment
from app.models.document import Document
from app.models.user import User

router = APIRouter(prefix="/admin/export", tags=["admin-export"])


def check(token: str | None, query_token: str | None = None) -> None:
    final_token = token or (query_token if settings.allow_token_query else None)
    if final_token != settings.admin_api_token:
        raise HTTPException(status_code=401, detail="bad token")


def csv_response(filename: str, rows: list[dict]) -> StreamingResponse:
    buffer = io.StringIO()
    if rows:
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    else:
        buffer.write("empty\n")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/cases.csv")
async def export_cases(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check(x_admin_token, token)
    result = await db.execute(select(Case).order_by(Case.created_at.desc()).limit(5000))
    rows = [
        {
            "id": c.id,
            "case_number": c.case_number,
            "client_id": c.client_id,
            "route": c.route,
            "status": c.status,
            "lawyer_id": c.assigned_lawyer_id,
            "next_action": c.next_action,
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "updated_at": c.updated_at.isoformat() if c.updated_at else "",
        }
        for c in result.scalars().all()
    ]
    return csv_response("cases.csv", rows)


@router.get("/clients.csv")
async def export_clients(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check(x_admin_token, token)
    result = await db.execute(select(User).order_by(User.created_at.desc()).limit(5000))
    rows = [
        {
            "id": u.id,
            "telegram_id": u.telegram_id,
            "telegram_username": u.telegram_username,
            "full_name": u.full_name,
            "phone": u.phone,
            "email": u.email,
            "created_at": u.created_at.isoformat() if u.created_at else "",
        }
        for u in result.scalars().all()
    ]
    return csv_response("clients.csv", rows)


@router.get("/payments.csv")
async def export_payments(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check(x_admin_token, token)
    result = await db.execute(select(Payment).order_by(Payment.created_at.desc()).limit(5000))
    rows = [
        {
            "id": p.id,
            "case_id": p.case_id,
            "code": p.payment_code,
            "title": p.title,
            "amount": float(p.amount),
            "currency": p.currency,
            "status": p.status,
            "provider": p.provider,
            "provider_payment_id": p.provider_payment_id,
            "created_at": p.created_at.isoformat() if p.created_at else "",
        }
        for p in result.scalars().all()
    ]
    return csv_response("payments.csv", rows)


@router.get("/documents.csv")
async def export_documents(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
    token: str | None = Query(default=None),
):
    check(x_admin_token, token)
    result = await db.execute(select(Document).order_by(Document.created_at.desc()).limit(5000))
    rows = [
        {
            "id": d.id,
            "case_id": d.case_id,
            "type": d.document_type,
            "title": d.title,
            "file_name": d.file_name,
            "status": d.status,
            "version": d.version,
            "created_at": d.created_at.isoformat() if d.created_at else "",
        }
        for d in result.scalars().all()
    ]
    return csv_response("documents.csv", rows)
