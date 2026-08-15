from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_history import add_case_history_event
from app.domain.payments.client_payment_reconciliation import ClientPaymentReconciliationService
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["admin", "m2-payment-reservation-repair"])


async def _admin_actor(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _context(db: AsyncSession, *, payment_id: int, case_id: int):
    payment = await db.get(Payment, int(payment_id))
    case = await db.get(Case, int(case_id))
    if payment is None or case is None or int(payment.case_id) != int(case.id):
        raise HTTPException(status_code=404, detail="Платёж или связанное дело не найдено")
    user = await db.get(User, int(case.client_id))
    return payment, case, user


@router.post("/admin/workdesk/payments/{payment_id}/reconcile-m2-reservation")
async def reconcile_m2_payment_reservation(
    payment_id: int,
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin_actor(request, db, x_admin_token)
    try:
        case_id = int(payload.get("case_id"))
    except (TypeError, ValueError) as error:
        raise HTTPException(status_code=400, detail="Не указан case_id") from error
    comment = str(payload.get("comment") or "").strip()
    if len(comment) < 10:
        raise HTTPException(
            status_code=400,
            detail="Укажите основание сверки минимум в 10 символах",
        )

    try:
        payment, case, changed = await ClientPaymentReconciliationService(db).reconcile(
            payment_id=int(payment_id),
            case_id=int(case_id),
        )
        if not changed:
            await db.rollback()
            raise HTTPException(
                status_code=409,
                detail=(
                    "Платёж уже соответствует текущему M2-контексту либо не относится к активной клиентской ссылке. "
                    "Автоматическое изменение не требуется."
                ),
            )
        await add_case_history_event(
            db,
            actor_type="admin",
            actor_id=int(actor.account_id),
            case_id=int(case.id),
            action="ADMIN_M2_PAYMENT_RESERVATION_RECONCILED",
            new_value={
                "payment_id": int(payment.id),
                "payment_status": str(payment.status),
                "case_status": str(case.status),
            },
            comment=comment,
        )
        await db.commit()
    except HTTPException:
        raise
    except (LookupError, ValueError) as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise

    return {
        "ok": True,
        "payment_id": int(payment.id),
        "payment_status": str(payment.status),
        "case_id": int(case.id),
        "case_status": str(case.status),
        "result": "stale_client_payment_expired",
    }


@router.get(
    "/admin/workdesk/payments/{payment_id}/reconcile-m2-reservation/ui",
    response_class=HTMLResponse,
)
async def reconcile_m2_payment_reservation_ui(
    payment_id: int,
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin_actor(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise

    payment, case, user = await _context(
        db,
        payment_id=int(payment_id),
        case_id=int(case_id),
    )
    client = escape(str(user.full_name if user else f"Клиент #{case.client_id}"))
    case_number = escape(str(case.case_number))
    case_status = escape(str(case.status))
    payment_status = escape(str(payment.status))
    reservation = escape(str(payment.reservation_key or "не задан"))
    return HTMLResponse(
        f"""<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>
<title>Сверка M2-платежа</title><style>:root{{--bg:#f5f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--amber:#a15c00;--amberbg:#fff7e6}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;color:var(--ink)}}main{{max-width:760px;margin:38px auto;padding:0 18px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:22px;box-shadow:0 14px 36px rgba(16,24,40,.07)}}.eyebrow{{font-size:12px;color:var(--muted);font-weight:800;letter-spacing:.04em}}.warn{{background:var(--amberbg);border:1px solid #fedf89;border-radius:12px;padding:13px;line-height:1.45}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:16px 0}}.cell{{background:#f8fafc;border-radius:12px;padding:11px}}.cell span{{display:block;color:var(--muted);font-size:11px;margin-bottom:4px}}textarea{{width:100%;min-height:92px;padding:11px;border:1px solid #d0d5dd;border-radius:10px;margin-top:8px}}button,.btn{{border:0;border-radius:10px;padding:10px 13px;background:var(--blue);color:#fff;font-weight:750;text-decoration:none;cursor:pointer;display:inline-block}}.secondary{{background:#475467}}#msg{{margin-top:12px}}@media(max-width:640px){{.grid{{grid-template-columns:1fr}}}}</style></head><body><main><div class='card'>
<div class='eyebrow'>PROCESS INTEGRITY · M2 PAYMENT</div><h1>Сверить старую ссылку оплаты</h1><div class='warn'><b>Что делает действие:</b> повторно проверяет конкретные Payment → Case → Consultation → Slot под блокировкой. Если ссылка устарела, она станет EXPIRED. Действие не ставит PAID, не делает возврат и не подтверждает консультацию.</div>
<div class='grid'><div class='cell'><span>Дело</span>{case_number}</div><div class='cell'><span>Клиент</span>{client}</div><div class='cell'><span>Статус дела</span>{case_status}</div><div class='cell'><span>Статус платежа</span>{payment_status}</div><div class='cell' style='grid-column:1/-1'><span>reservation_key</span>{reservation}</div></div>
<label><b>Основание сверки</b></label><textarea id='comment' placeholder='Например: Process Integrity обнаружил несовпадение reservation_key с текущим слотом'></textarea><p><button id='go' onclick='repair()'>Проверить и закрыть старую ссылку</button> <a class='btn secondary' href='/admin/workdesk/ui?case_id={int(case.id)}'>Отмена</a></p><div id='msg'></div></div></main><script>
async function repair(){{const comment=document.getElementById('comment').value.trim(),b=document.getElementById('go'),m=document.getElementById('msg');if(comment.length<10){{m.textContent='Укажите основание минимум в 10 символах.';return}}b.disabled=true;b.textContent='Сверяем…';try{{const r=await fetch('/admin/workdesk/payments/{int(payment.id)}/reconcile-m2-reservation',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{case_id:{int(case.id)},comment}})}});const d=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Сверка не выполнена');m.textContent='Готово: старая клиентская ссылка закрыта безопасно. Обновляем Workdesk…';setTimeout(()=>location.href='/admin/workdesk/ui?case_id={int(case.id)}',900)}}catch(e){{m.textContent=e.message;b.disabled=false;b.textContent='Проверить и закрыть старую ссылку'}}}}
</script></body></html>"""
    )


__all__ = ["router"]
