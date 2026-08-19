from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.cases.case_timeline import get_client_visible_status
from app.domain.cases.m1_internal_payment_recovery import (
    M1InternalPaymentRecoveryError,
    M1InternalPaymentRecoveryService,
)
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(tags=["m1-internal-payment-recovery"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


async def _context(db: AsyncSession, case_id: int) -> dict[str, object]:
    case, plan, payment = await M1InternalPaymentRecoveryService(db).inspect(
        case_id=case_id
    )
    return {
        "case_id": int(case.id),
        "case_number": case.case_number,
        "current_status": str(case.status),
        "current_status_label": get_client_visible_status(str(case.status)),
        "target_status": plan.target.value,
        "target_status_label": get_client_visible_status(plan.target.value),
        "title": plan.title,
        "payment_id": int(payment.id),
        "payment_code": str(payment.payment_code),
        "payment_status": str(payment.status),
        "amount": str(payment.amount),
        "currency": payment.currency,
    }


@router.get("/admin/workdesk/cases/{case_id}/recover-payment-stage")
async def recovery_context(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    try:
        return await _context(db, case_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except M1InternalPaymentRecoveryError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/admin/workdesk/cases/{case_id}/recover-payment-stage")
async def recover_payment_stage(
    case_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    try:
        case, plan, payment = await M1InternalPaymentRecoveryService(db).recover(
            case_id=case_id,
            admin_id=int(actor.account_id),
            comment=payload.get("comment") or "",
        )
        response = {
            "ok": True,
            "case_id": int(case.id),
            "case_status": str(case.status),
            "target_status": plan.target.value,
            "payment_id": int(payment.id),
        }
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except M1InternalPaymentRecoveryError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return response


@router.get(
    "/admin/workdesk/cases/{case_id}/recover-payment-stage/ui",
    response_class=HTMLResponse,
)
async def recovery_ui(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _admin(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    try:
        item = await _context(db, case_id)
    except LookupError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except M1InternalPaymentRecoveryError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error

    html = f"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Восстановление платёжного этапа</title>
<style>
:root{{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00;--amber2:#fff7e6;--shadow:0 12px 34px rgba(16,24,40,.07)}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif}}header{{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:20px}}.head,main{{max-width:820px;margin:auto}}h1{{margin:0 0 5px;font-size:22px}}header p{{margin:0;color:#d0d5dd;font-size:13px}}main{{padding:20px}}.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:17px;box-shadow:var(--shadow)}}.warning{{background:var(--amber2);border:1px solid #fedf89;border-radius:12px;padding:12px;margin-bottom:14px;line-height:1.45}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:9px;margin:12px 0}}.cell{{background:#f8fafc;border-radius:10px;padding:10px}}.cell span{{display:block;color:var(--muted);font-size:11px;margin-bottom:3px}}label{{display:block;font-weight:750;margin:13px 0 6px}}textarea{{width:100%;min-height:110px;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit;resize:vertical}}.actions{{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}}button,.button{{border:0;border-radius:10px;padding:10px 13px;color:#fff;background:var(--blue);font-weight:750;text-decoration:none;cursor:pointer}}button:disabled{{opacity:.55}}.secondary{{background:#475467}}.feedback{{min-height:22px;margin-top:10px;color:var(--muted)}}.bad{{color:var(--red)}}.ok{{color:var(--green)}}@media(max-width:600px){{main{{padding:12px}}.grid{{grid-template-columns:1fr}}.actions{{flex-direction:column}}button,.button{{width:100%;text-align:center}}}}
</style></head><body><header><div class="head"><h1>Восстановление платёжного этапа</h1><p>Только доказанный PAID-платёж → единственный разрешённый следующий статус.</p></div></header><main><div class="warning"><b>Почему это отдельное действие.</b> Этот статус обычно существует доли секунды внутри одной платёжной транзакции. Если он сохранился, generic ручная смена статуса запрещена. Система разрешит восстановление только по уже подтверждённому платежу нужного назначения.</div><section class="card"><h2>{escape(str(item['case_number']))}</h2><div class="grid"><div class="cell"><span>Сейчас</span>{escape(str(item['current_status_label']))}</div><div class="cell"><span>После восстановления</span>{escape(str(item['target_status_label']))}</div><div class="cell"><span>Доказательство</span>Платёж #{int(item['payment_id'])} · {escape(str(item['payment_status']))}</div><div class="cell"><span>Сумма</span>{escape(str(item['amount']))} {escape(str(item['currency']))}</div></div><p><b>{escape(str(item['title']))}</b></p><label for="comment">Что проверено перед восстановлением</label><textarea id="comment" placeholder="Например: сверено поступление платежа и история webhook; дело осталось на внутреннем статусе после старой версии"></textarea><div class="actions"><button id="confirm" onclick="recover()">Подтвердить восстановление</button><a class="button secondary" href="/admin/workdesk/ui?case_id={int(item['case_id'])}">Вернуться без изменений</a></div><div id="feedback" class="feedback" role="status"></div></section></main>
<script>
const feedback=document.getElementById('feedback'),button=document.getElementById('confirm');
function esc(v){{return String(v??'').replace(/[&<>\x22\x27]/g,c=>c==='&'?'&amp;':c==='<'?'&lt;':c==='>'?'&gt;':c.charCodeAt(0)===34?'&quot;':'&#39;')}}
async function recover(){{const comment=document.getElementById('comment').value.trim();if(comment.length<10){{feedback.textContent='Опишите проверку минимум в 10 символах.';feedback.className='feedback bad';return}}if(!confirm('Подтвердить восстановление? Сервер ещё раз проверит текущий статус и PAID-платёж.'))return;button.disabled=true;try{{const r=await fetch('/admin/workdesk/cases/{int(item['case_id'])}/recover-payment-stage',{{method:'POST',credentials:'same-origin',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{comment}})}});const d=await r.json().catch(()=>({{}}));if(!r.ok)throw new Error(d.detail||'Операция не выполнена');feedback.textContent='Этап восстановлен. Открываем актуальное дело…';feedback.className='feedback ok';setTimeout(()=>location.href='/admin/workdesk/ui?case_id={int(item['case_id'])}',500)}}catch(e){{feedback.textContent='Ничего не изменено: '+e.message;feedback.className='feedback bad';button.disabled=false}}}}
</script></body></html>"""
    return HTMLResponse(html)


__all__ = ["router"]
