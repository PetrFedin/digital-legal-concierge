from __future__ import annotations

import csv
import io
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.security.current_admin import current_admin_gateway
from app.db.session import get_db
from app.models.case import Case
from app.models.payment import Payment
from app.models.document import Document
from app.models.user import User

router = APIRouter(prefix="/admin/export", tags=["admin-export"], dependencies=[Depends(current_admin_gateway)])


# CSV is intended for spreadsheet viewing, not a lossless raw-data API.
# Excel/LibreOffice may interpret client-provided strings as executable formulae.
# See OWASP CSV Injection: https://owasp.org/www-community/attacks/CSV_Injection
_FORMULA_PREFIXES = "=+-@＝＋－＠"
_LEADING_SPREADSHEET_WHITESPACE = " \t\r\n\v\f\ufeff"


def safe_spreadsheet_cell(value: object) -> object:
    """Escape only risky text, leaving typed IDs and amounts numeric.

    The apostrophe is an intentional presentation escape. No single CSV
    mitigation protects every spreadsheet application's save/reopen behavior;
    never treat this as the authoritative raw value stored in the database.
    """
    if not isinstance(value, str) or not value:
        return value
    trimmed = value.lstrip(_LEADING_SPREADSHEET_WHITESPACE)
    if value[0] in "\t\r\n" or (
        trimmed and trimmed[0] in _FORMULA_PREFIXES
    ):
        return "'" + value
    return value


def csv_response(filename: str, rows: list[dict]) -> StreamingResponse:
    buffer = io.StringIO(newline="")
    if rows:
        columns = list(rows[0].keys())
        writer = csv.DictWriter(
            buffer, fieldnames=columns, quoting=csv.QUOTE_ALL,
            lineterminator="\r\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({
                column: safe_spreadsheet_cell(row.get(column))
                for column in columns
            })
    else:
        buffer.write("empty\r\n")
    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/cases.csv")
async def export_cases(
    db: AsyncSession = Depends(get_db),
):
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
            "enforcement_number": c.enforcement_number,
            "enforcement_status": c.enforcement_status,
            "enforcement_started_at": (
                c.enforcement_started_at.isoformat()
                if c.enforcement_started_at
                else ""
            ),
            "received_amount": (
                str(c.received_amount) if c.received_amount is not None else ""
            ),
            "received_at": c.received_at.isoformat() if c.received_at else "",
            "success_fee_amount": (
                str(c.success_fee_amount)
                if c.success_fee_amount is not None
                else ""
            ),
            "created_at": c.created_at.isoformat() if c.created_at else "",
            "updated_at": c.updated_at.isoformat() if c.updated_at else "",
        }
        for c in result.scalars().all()
    ]
    return csv_response("cases.csv", rows)


@router.get("/clients.csv")
async def export_clients(
    db: AsyncSession = Depends(get_db),
):
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
):
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
):
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
