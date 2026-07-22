from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.payment_lifecycle_service import (
    ConsultationPaymentLifecycleError,
    ConsultationPaymentLifecycleService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.lawyer.lawyer_decisions import LawyerDecisionService
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)

router = APIRouter(prefix="/lawyer", tags=["lawyer"])


def require_staff(token: str | None) -> dict:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or not roles.intersection(
        {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}
    ):
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return payload


async def resolve_authenticated_lawyer(
    *,
    token: str | None,
    db: AsyncSession,
) -> Lawyer:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or ROLE_LAWYER not in roles:
        raise HTTPException(403, "Подтверждение доступно только юристу")

    try:
        admin_user_id = int(payload.get("uid"))
    except (TypeError, ValueError):
        raise HTTPException(403, "Не удалось определить аккаунт юриста")
    if admin_user_id <= 0:
        raise HTTPException(
            403,
            "Для подтверждения войдите под персональным аккаунтом юриста",
        )

    admin_user = (
        await db.execute(
            select(AdminUser).where(
                AdminUser.id == admin_user_id,
                AdminUser.is_active.is_(True),
            )
        )
    ).scalar_one_or_none()
    if admin_user is None:
        raise HTTPException(403, "Аккаунт юриста не найден или отключён")

    normalized_email = admin_user.email.strip().lower()
    lawyers = list(
        (
            await db.execute(
                select(Lawyer)
                .where(
                    Lawyer.is_active.is_(True),
                    Lawyer.email.is_not(None),
                    func.lower(Lawyer.email) == normalized_email,
                )
                .order_by(Lawyer.id.asc())
            )
        )
        .scalars()
        .all()
    )
    if not lawyers:
        raise HTTPException(
            403,
            "Для аккаунта не настроен активный профиль юриста с тем же email",
        )
    if len(lawyers) != 1:
        raise HTTPException(
            409,
            "Для email найдено несколько активных профилей юриста. Обратитесь к администратору",
        )
    return lawyers[0]


async def get_case_or_404(*, case_id: int, db: AsyncSession) -> Case:
    case = (
        await db.execute(select(Case).where(Case.id == case_id))
    ).scalar_one_or_none()
    if case is None:
        raise HTTPException(404, "Дело не найдено")
    return case


@router.get("/ui", response_class=HTMLResponse)
async def lawyer_ui():
    return HTMLResponse(LAWYER_UI_HTML)


@router.get("/consultations/pending-confirmation")
async def pending_consultations(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    lawyer = await resolve_authenticated_lawyer(
        token=x_admin_token,
        db=db,
    )
    rows = (
        await db.execute(
            select(Consultation, Case)
            .join(Case, Case.id == Consultation.case_id)
            .where(
                Consultation.lawyer_id == lawyer.id,
                Consultation.status
                == ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
            )
            .order_by(
                Consultation.scheduled_at.asc(),
                Consultation.id.asc(),
            )
        )
    ).all()
    return [
        {
            "consultation_id": consultation.id,
            "case_id": case.id,
            "case_number": case.case_number,
            "case_title": case.title,
            "status": consultation.status,
            "consultation_type": consultation.consultation_type,
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "slot_id": consultation.slot_id,
            "client_description": consultation.client_description,
        }
        for consultation, case in rows
    ]


@router.post("/cases/{case_id}/confirm-consultation")
async def confirm_consultation(
    case_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    lawyer = await resolve_authenticated_lawyer(
        token=x_admin_token,
        db=db,
    )
    case = await get_case_or_404(case_id=case_id, db=db)
    try:
        consultation = await ConsultationPaymentLifecycleService(
            db
        ).confirm_by_lawyer(
            case=case,
            lawyer_id=lawyer.id,
            source="lawyer_api",
        )
        await db.commit()
    except ConsultationPaymentLifecycleError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc)) from exc

    return {
        "ok": True,
        "case_id": case.id,
        "case_status": case.status,
        "consultation_id": consultation.id,
        "consultation_status": consultation.status,
        "lawyer_id": lawyer.id,
        "slot_id": consultation.slot_id,
        "scheduled_at": (
            consultation.scheduled_at.isoformat()
            if consultation.scheduled_at
            else None
        ),
    }


@router.post("/cases/{case_id}/accept")
async def accept(
    case_id: int,
    lawyer_id: int = 1,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    case = await get_case_or_404(case_id=case_id, db=db)
    await LawyerDecisionService(db).accept_m1_case(
        case=case,
        lawyer_id=lawyer_id,
    )
    await db.commit()
    return {"ok": True}


@router.post("/cases/{case_id}/request-documents")
async def request_docs(
    case_id: int,
    payload: dict,
    lawyer_id: int = 1,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    case = await get_case_or_404(case_id=case_id, db=db)
    comment = str(payload.get("comment") or "").strip()
    if not comment:
        comment = "Нужны дополнительные документы"
    await LawyerDecisionService(db).request_more_documents(
        case=case,
        lawyer_id=lawyer_id,
        comment=comment,
    )
    await db.commit()
    return {"ok": True}


LAWYER_UI_HTML = r"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Digital Legal Concierge — кабинет юриста</title>
  <style>
    :root{--bg:#f4f6f8;--card:#fff;--text:#111827;--muted:#6b7280;--line:#e5e7eb;--green:#15803d;--green2:#166534;--red:#b91c1c;--dark:#111827}
    *{box-sizing:border-box}body{margin:0;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);color:var(--text)}
    header{background:var(--dark);color:#fff;padding:16px 22px;display:flex;align-items:center;justify-content:space-between;gap:14px;position:sticky;top:0;z-index:5}
    header h1{font-size:18px;margin:0}.header-actions{display:flex;gap:10px;align-items:center;flex-wrap:wrap}.header-actions a{color:#fff}.header-actions form{margin:0}
    main{max-width:1050px;margin:auto;padding:24px}.summary{display:grid;grid-template-columns:1fr auto;gap:14px;align-items:center;margin-bottom:18px}
    .card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 1px 2px rgba(0,0,0,.04)}
    .metric{font-size:30px;font-weight:800;margin-top:4px}.muted{color:var(--muted);font-size:13px}.queue{display:grid;gap:14px}
    .consultation{display:grid;grid-template-columns:1fr auto;gap:18px;align-items:start}.title{font-size:18px;font-weight:800;margin-bottom:6px}.meta{display:flex;gap:8px;flex-wrap:wrap;margin:8px 0 12px}.pill{display:inline-block;padding:5px 9px;border-radius:999px;background:#ecfdf5;color:#166534;font-size:12px;font-weight:700}.question{background:#f9fafb;border:1px solid var(--line);border-radius:12px;padding:12px;white-space:pre-wrap;line-height:1.45}
    button{border:0;border-radius:10px;padding:10px 14px;font-weight:750;cursor:pointer;background:var(--green);color:#fff}button:hover{background:var(--green2)}button.secondary{background:#e5e7eb;color:#111827}button:disabled{opacity:.6;cursor:wait}.error{border-color:#fecaca;background:#fef2f2;color:var(--red)}.empty{text-align:center;padding:34px}.status{min-height:22px;margin:10px 0}.actions{min-width:190px;text-align:right}
    @media(max-width:720px){header{align-items:flex-start;flex-direction:column}.summary,.consultation{grid-template-columns:1fr}.actions{text-align:left;min-width:0}.actions button{width:100%}}
  </style>
</head>
<body>
<header>
  <div><h1>⚖ Кабинет юриста</h1><div class="muted" id="identity" style="color:#d1d5db">Проверка сессии…</div></div>
  <div class="header-actions"><a id="adminLink" href="/admin-ui" hidden>Админка</a><form method="post" action="/logout"><button type="submit" class="secondary">Выйти</button></form></div>
</header>
<main>
  <section class="summary">
    <div class="card"><div class="muted">Оплаченные консультации, ожидающие подтверждения</div><div class="metric" id="count">—</div></div>
    <button onclick="loadQueue()" class="secondary">Обновить</button>
  </section>
  <div id="status" class="status muted"></div>
  <section id="queue" class="queue"><div class="card empty">Загрузка…</div></section>
</main>
<script>
let token='';
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function fmt(v){if(!v)return 'Время не указано';const d=new Date(v);return Number.isNaN(d.getTime())?String(v):d.toLocaleString('ru-RU',{dateStyle:'long',timeStyle:'short'})}
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();const roles=Array.isArray(s.roles)?s.roles:[];if(!roles.includes('lawyer')){queue.innerHTML='<div class="card error">Для этого кабинета требуется роль юриста.</div>';count.textContent='0';identity.textContent=s.username||'Пользователь';adminLink.hidden=!roles.some(x=>x==='admin'||x==='superadmin');return}token=s.api_token;identity.textContent=s.username||'Юрист';adminLink.hidden=!roles.some(x=>x==='admin'||x==='superadmin');await loadQueue()}
async function loadQueue(){status.textContent='Обновление…';try{const rows=await api('/lawyer/consultations/pending-confirmation');count.textContent=rows.length;queue.innerHTML=rows.length?rows.map(renderCard).join(''):'<div class="card empty"><b>Нет консультаций, ожидающих подтверждения.</b><p class="muted">Новые оплаченные записи появятся здесь автоматически.</p></div>';status.textContent='Данные обновлены: '+new Date().toLocaleTimeString('ru-RU')}catch(e){count.textContent='—';queue.innerHTML='<div class="card error"><b>Не удалось загрузить очередь.</b><p>'+esc(e.message)+'</p></div>';status.textContent='Ошибка загрузки'}}
function renderCard(x){return `<article class="card consultation"><div><div class="title">${esc(x.case_title||'Консультация')}</div><div class="muted">Дело ${esc(x.case_number)} · консультация #${esc(x.consultation_id)}</div><div class="meta"><span class="pill">Оплачено</span><span class="pill">${esc(fmt(x.scheduled_at))}</span><span class="pill">${esc(x.consultation_type||'online')}</span></div><div class="question">${esc(x.client_description||'Клиент не добавил описание вопроса.')}</div></div><div class="actions"><button id="confirm_${x.case_id}" onclick="confirmConsultation(${x.case_id},'${esc(x.case_number)}')">Подтвердить запись</button></div></article>`}
async function confirmConsultation(caseId,caseNumber){if(!confirm('Подтвердить консультацию по делу '+caseNumber+'?'))return;const button=document.getElementById('confirm_'+caseId);button.disabled=true;button.textContent='Подтверждение…';try{await api('/lawyer/cases/'+caseId+'/confirm-consultation',{method:'POST'});status.textContent='Консультация по делу '+caseNumber+' подтверждена.';await loadQueue()}catch(e){button.disabled=false;button.textContent='Подтвердить запись';alert(e.message)}}
boot();
</script>
</body>
</html>
"""
