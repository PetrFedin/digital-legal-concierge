from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.notification import Notification
from app.models.payment import Payment
from app.security.access_control import ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/recovery-center", tags=["recovery-center"])
MAX_RECOVERY_BATCH = 100


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


async def _require_superadmin(
    request: Request,
    db: AsyncSession,
    header_token: str | None,
):
    actor = await resolve_document_actor(db, _token(request, header_token))
    if actor.role != ROLE_SUPERADMIN:
        raise HTTPException(
            status_code=403,
            detail="Recovery Center доступен только суперадминистратору",
        )
    return actor


def _require_enabled() -> None:
    if not settings.enable_recovery_actions:
        raise HTTPException(
            status_code=403,
            detail="Восстановительные действия отключены настройкой",
        )


def _ids(payload: dict, field: str) -> list[int]:
    raw = payload.get(field)
    if not isinstance(raw, list):
        raise HTTPException(status_code=400, detail=f"Поле {field} должно быть списком ID")
    values: list[int] = []
    for value in raw:
        try:
            item_id = int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Некорректный ID в {field}")
        if item_id > 0 and item_id not in values:
            values.append(item_id)
    if not values:
        raise HTTPException(status_code=400, detail="Не выбраны записи для восстановления")
    if len(values) > MAX_RECOVERY_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"За один раз можно обработать не более {MAX_RECOVERY_BATCH} записей",
        )
    return values


@router.get("")
async def recovery_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _require_superadmin(request, db, x_admin_token)
    now = datetime.now(timezone.utc)
    failed_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Notification).where(
                    Notification.status == "FAILED"
                )
            )
        ).scalar_one()
        or 0
    )
    expired_payment_count = int(
        (
            await db.execute(
                select(func.count()).select_from(Payment).where(
                    Payment.status.in_(
                        [PaymentStatus.PENDING, PaymentStatus.WAITING_CONFIRMATION]
                    ),
                    Payment.expires_at.is_not(None),
                    Payment.expires_at <= now,
                )
            )
        ).scalar_one()
        or 0
    )
    failed = list(
        (
            await db.execute(
                select(Notification)
                .where(Notification.status == "FAILED")
                .order_by(Notification.updated_at.asc(), Notification.id.asc())
                .limit(20)
            )
        ).scalars().all()
    )
    expired = list(
        (
            await db.execute(
                select(Payment)
                .where(
                    Payment.status.in_(
                        [PaymentStatus.PENDING, PaymentStatus.WAITING_CONFIRMATION]
                    ),
                    Payment.expires_at.is_not(None),
                    Payment.expires_at <= now,
                )
                .order_by(Payment.expires_at.asc(), Payment.id.asc())
                .limit(20)
            )
        ).scalars().all()
    )
    return {
        "ok": True,
        "enabled": bool(settings.enable_recovery_actions),
        "failed_notification_count": failed_count,
        "past_due_payment_count": expired_payment_count,
        "failed_notifications": [
            {
                "id": item.id,
                "case_id": item.case_id,
                "event_code": item.event_code,
                "attempt_count": item.attempt_count,
            }
            for item in failed
        ],
        "past_due_payments": [
            {
                "id": item.id,
                "case_id": item.case_id,
                "payment_code": item.payment_code,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
            }
            for item in expired
        ],
    }


@router.post("/reset-stuck-notifications")
async def reset_stuck_notifications(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    _require_enabled()
    if payload.get("confirm") != "RESET_FAILED_NOTIFICATIONS":
        raise HTTPException(status_code=400, detail="Требуется явное подтверждение операции")
    notification_ids = _ids(payload, "notification_ids")
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.execute(
                select(Notification)
                .where(
                    Notification.id.in_(notification_ids),
                    Notification.status == "FAILED",
                )
                .with_for_update()
            )
        ).scalars().all()
    )
    for notification in rows:
        notification.status = "PENDING"
        notification.is_sent = False
        notification.last_error = None
        notification.next_attempt_at = now
        if notification.case_id:
            await add_case_history_event(
                db,
                actor_type="admin_user",
                actor_id=actor.account_id,
                case_id=int(notification.case_id),
                action="NOTIFICATION_REQUEUED_BY_SUPERADMIN",
                old_value={"notification_id": notification.id, "status": "FAILED"},
                new_value={"notification_id": notification.id, "status": "PENDING"},
                comment="Ручной повтор только явно выбранного FAILED-уведомления",
            )
    await db.commit()
    return {
        "ok": True,
        "requested": len(notification_ids),
        "updated": len(rows),
        "skipped": len(notification_ids) - len(rows),
    }


@router.post("/expire-waiting-payments")
async def expire_waiting_payments(
    request: Request,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _require_superadmin(request, db, x_admin_token)
    _require_enabled()
    if payload.get("confirm") != "EXPIRE_PAST_DUE_PAYMENTS":
        raise HTTPException(status_code=400, detail="Требуется явное подтверждение операции")
    payment_ids = _ids(payload, "payment_ids")
    now = datetime.now(timezone.utc)
    rows = list(
        (
            await db.execute(
                select(Payment)
                .where(
                    Payment.id.in_(payment_ids),
                    Payment.status.in_(
                        [PaymentStatus.PENDING, PaymentStatus.WAITING_CONFIRMATION]
                    ),
                    Payment.expires_at.is_not(None),
                    Payment.expires_at <= now,
                )
                .with_for_update()
            )
        ).scalars().all()
    )
    for payment in rows:
        old_status = str(payment.status)
        payment.status = PaymentStatus.EXPIRED
        await add_case_history_event(
            db,
            actor_type="admin_user",
            actor_id=actor.account_id,
            case_id=int(payment.case_id),
            action="PAYMENT_EXPIRED_BY_SUPERADMIN_RECOVERY",
            old_value={
                "payment_id": payment.id,
                "status": old_status,
                "expires_at": payment.expires_at.isoformat() if payment.expires_at else None,
            },
            new_value={"payment_id": payment.id, "status": PaymentStatus.EXPIRED.value},
            comment="Истёкшая платёжная запись закрыта по явному ID; живые платежи не затрагиваются",
        )
    await db.commit()
    return {
        "ok": True,
        "requested": len(payment_ids),
        "updated": len(rows),
        "skipped": len(payment_ids) - len(rows),
    }


@router.get("/ui", response_class=HTMLResponse)
async def recovery_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _require_superadmin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(
        """
<!doctype html><html lang='ru'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Recovery Center</title><style>
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;margin:0;background:#f4f6fa;color:#172033}.wrap{max-width:960px;margin:auto;padding:28px}.head{display:flex;justify-content:space-between;align-items:center;gap:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:12px}.card{background:#fff;border:1px solid #e4e7ec;border-radius:14px;padding:16px;margin-top:12px}.muted{color:#667085;font-size:13px}.bad{color:#b42318}.ok{color:#067647}button{border:0;border-radius:10px;padding:10px 13px;font-weight:750;cursor:pointer;background:#3157d5;color:white}input{width:100%;box-sizing:border-box;padding:10px;border:1px solid #d0d5dd;border-radius:9px;margin:8px 0}a{color:#3157d5;font-weight:700;text-decoration:none}@media(max-width:720px){.grid{grid-template-columns:1fr}}
</style></head><body><div class='wrap'><div class='head'><div><h1>Recovery Center</h1><div class='muted'>Только адресные операции по явным ID. Массовые изменения запрещены.</div></div><a href='/admin/workdesk/ui'>К рабочему столу</a></div><div id='state' class='card'>Загрузка…</div><div class='grid'><div class='card'><h3>Повтор FAILED-уведомлений</h3><div id='failed' class='muted'></div><input id='notificationIds' placeholder='ID через запятую'><button onclick='retryNotifications()'>Повторить выбранные</button></div><div class='card'><h3>Закрыть истёкшие платежные записи</h3><div id='expired' class='muted'></div><input id='paymentIds' placeholder='ID через запятую'><button onclick='expirePayments()'>Закрыть выбранные</button></div></div><div id='feedback' class='card muted'>Recovery не меняет бизнес-статус дела автоматически; после операции проверьте карточку и E2E-контроль workdesk.</div></div><script>
let token='';const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));const ids=v=>v.split(',').map(x=>Number(x.trim())).filter(x=>Number.isInteger(x)&&x>0);async function api(path,opt={}){const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...opt,headers:{'content-type':'application/json','x-admin-token':token,...(opt.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw Error(d.detail||'Ошибка');return d}async function load(){try{const s=await fetch('/auth/session',{credentials:'same-origin',cache:'no-store'});if(!s.ok){location.href='/login';return}const session=await s.json();if(!(session.roles||[session.role]).includes('superadmin'))throw Error('Требуется роль superadmin');token=session.api_token||'';const d=await api('/recovery-center');state.innerHTML=`<b class="${d.enabled?'ok':'bad'}">${d.enabled?'Recovery actions включены':'Recovery actions отключены'}</b><div class="muted">FAILED notifications: ${d.failed_notification_count} · просроченных платёжных записей: ${d.past_due_payment_count}</div>`;failed.innerHTML=(d.failed_notifications||[]).map(x=>`#${esc(x.id)} · дело ${esc(x.case_id||'—')} · ${esc(x.event_code)} · попыток ${esc(x.attempt_count)}`).join('<br>')||'Кандидатов нет';expired.innerHTML=(d.past_due_payments||[]).map(x=>`#${esc(x.id)} · дело ${esc(x.case_id)} · ${esc(x.payment_code)} · ${esc(x.expires_at)}`).join('<br>')||'Кандидатов нет'}catch(e){state.innerHTML='<b class="bad">'+esc(e.message)+'</b>'}}async function retryNotifications(){const list=ids(notificationIds.value);if(!list.length)return feedback.textContent='Укажите ID уведомлений.';if(!confirm('Повторить только выбранные FAILED-уведомления?'))return;try{const d=await api('/recovery-center/reset-stuck-notifications',{method:'POST',body:JSON.stringify({notification_ids:list,confirm:'RESET_FAILED_NOTIFICATIONS'})});feedback.textContent=`Обновлено: ${d.updated}, пропущено: ${d.skipped}`;await load()}catch(e){feedback.textContent=e.message}}async function expirePayments(){const list=ids(paymentIds.value);if(!list.length)return feedback.textContent='Укажите ID платежей.';if(!confirm('Закрыть только выбранные уже просроченные платёжные записи?'))return;try{const d=await api('/recovery-center/expire-waiting-payments',{method:'POST',body:JSON.stringify({payment_ids:list,confirm:'EXPIRE_PAST_DUE_PAYMENTS'})});feedback.textContent=`Обновлено: ${d.updated}, пропущено: ${d.skipped}`;await load()}catch(e){feedback.textContent=e.message}}load();
</script></body></html>
"""
    )
