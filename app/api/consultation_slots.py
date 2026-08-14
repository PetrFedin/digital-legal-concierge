from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.consultations.slot_service import SlotService
from app.models.audit_log import AuditLog
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(prefix="/consultation-slots", tags=["consultation-slots"])
ACTIVE_SLOT_STATUSES = frozenset({"available", "held", "booked"})
MIN_SLOT_MINUTES = 15
MAX_SLOT_MINUTES = 8 * 60
MAX_NOTE_LENGTH = 500


async def _staff(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}:
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return actor


def _is_admin(actor) -> bool:
    return actor.role in {ROLE_ADMIN, ROLE_SUPERADMIN}


def _serialize_slot(row: ConsultationSlot, *, include_internal: bool) -> dict:
    payload = {
        "id": int(row.id),
        "lawyer_id": int(row.lawyer_id),
        "starts_at": row.starts_at.isoformat(),
        "ends_at": row.ends_at.isoformat(),
        "status": str(row.status),
        "hold_expires_at": row.hold_expires_at.isoformat() if row.hold_expires_at else None,
        "note": row.note,
    }
    if include_internal:
        payload["held_by_user_id"] = row.held_by_user_id
        payload["consultation_id"] = row.consultation_id
    return payload


def _parse_aware_datetime(value: object, field_name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, f"{field_name}: используйте ISO-дату со смещением часового пояса") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise HTTPException(400, f"{field_name}: обязательно укажите часовой пояс")
    return parsed.astimezone(timezone.utc)


def _clean_note(value: object) -> str | None:
    note = str(value or "").strip()
    if len(note) > MAX_NOTE_LENGTH:
        raise HTTPException(400, f"Комментарий не должен быть длиннее {MAX_NOTE_LENGTH} символов")
    return note or None


async def _resolve_target_lawyer(db: AsyncSession, *, actor, requested_lawyer_id: object) -> Lawyer:
    if actor.role == ROLE_LAWYER:
        lawyer_id = int(actor.lawyer_id or 0)
        if requested_lawyer_id not in {None, "", lawyer_id, str(lawyer_id)}:
            raise HTTPException(403, "Юрист может управлять только собственным расписанием")
    else:
        try:
            lawyer_id = int(requested_lawyer_id)
        except (TypeError, ValueError) as error:
            raise HTTPException(400, "Выберите юриста") from error

    lawyer = (
        await db.execute(
            select(Lawyer)
            .where(Lawyer.id == lawyer_id)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if lawyer is None or not lawyer.is_active:
        raise HTTPException(404, "Активный юрист не найден")
    return lawyer


async def _audit_slot(
    db: AsyncSession,
    *,
    actor,
    action: str,
    slot: ConsultationSlot | None,
    new_value: dict | None,
    old_value: dict | None = None,
    comment: str | None = None,
) -> None:
    db.add(
        AuditLog(
            actor_type="lawyer" if actor.role == ROLE_LAWYER else "admin",
            actor_id=int(actor.lawyer_id) if actor.role == ROLE_LAWYER else int(actor.account_id),
            action=action,
            entity_type="consultation_slot",
            entity_id=int(slot.id) if slot and slot.id else None,
            old_value=old_value,
            new_value=new_value,
            comment=comment,
        )
    )
    await db.flush()


@router.get("/context")
async def schedule_context(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    return {
        "role": actor.role,
        "lawyer_id": int(actor.lawyer_id) if actor.lawyer_id else None,
        "can_manage_all_lawyers": _is_admin(actor),
        "hold_minutes": await SlotService(db).get_hold_minutes(),
    }


@router.get("/lawyers")
async def list_lawyers(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    query = select(Lawyer).where(Lawyer.is_active.is_(True))
    if actor.role == ROLE_LAWYER:
        query = query.where(Lawyer.id == int(actor.lawyer_id or 0))
    rows = (await db.execute(query.order_by(Lawyer.full_name.asc()))).scalars().all()
    return [
        {
            "id": int(row.id),
            "full_name": row.full_name,
            "email": row.email if _is_admin(actor) else None,
        }
        for row in rows
    ]


@router.get("")
async def list_slots(
    request: Request,
    lawyer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    if actor.role == ROLE_LAWYER:
        own_id = int(actor.lawyer_id or 0)
        if lawyer_id is not None and int(lawyer_id) != own_id:
            raise HTTPException(403, "Чужое расписание недоступно")
        lawyer_id = own_id

    query = select(ConsultationSlot).order_by(
        ConsultationSlot.starts_at.asc(), ConsultationSlot.id.asc()
    )
    if lawyer_id is not None:
        query = query.where(ConsultationSlot.lawyer_id == lawyer_id)
    rows = (await db.execute(query.limit(500))).scalars().all()
    return [
        _serialize_slot(row, include_internal=_is_admin(actor))
        for row in rows
    ]


@router.post("")
async def create_slot(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    starts_at = _parse_aware_datetime(payload.get("starts_at"), "Начало")
    ends_at = _parse_aware_datetime(payload.get("ends_at"), "Окончание")
    now = datetime.now(timezone.utc)
    if starts_at <= now:
        raise HTTPException(400, "Нельзя создать слот в прошлом или на уже начавшееся время")
    if ends_at <= starts_at:
        raise HTTPException(400, "Время окончания должно быть позже времени начала")
    duration_minutes = int((ends_at - starts_at).total_seconds() // 60)
    if duration_minutes < MIN_SLOT_MINUTES or duration_minutes > MAX_SLOT_MINUTES:
        raise HTTPException(
            400,
            f"Продолжительность слота должна быть от {MIN_SLOT_MINUTES} минут до {MAX_SLOT_MINUTES // 60} часов",
        )
    note = _clean_note(payload.get("note"))

    try:
        lawyer = await _resolve_target_lawyer(
            db,
            actor=actor,
            requested_lawyer_id=payload.get("lawyer_id"),
        )
        # Lawyer row lock serializes overlapping slot creation for the same
        # calendar even when two browser tabs submit simultaneously.
        overlap = (
            await db.execute(
                select(ConsultationSlot.id).where(
                    ConsultationSlot.lawyer_id == lawyer.id,
                    ConsultationSlot.status.in_(ACTIVE_SLOT_STATUSES),
                    ConsultationSlot.starts_at < ends_at,
                    ConsultationSlot.ends_at > starts_at,
                )
            )
        ).scalar_one_or_none()
        if overlap is not None:
            raise HTTPException(409, "У юриста уже есть пересекающийся активный слот")

        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=ends_at,
            status="available",
            note=note,
        )
        db.add(slot)
        await db.flush()
        await _audit_slot(
            db,
            actor=actor,
            action="CONSULTATION_SLOT_CREATED",
            slot=slot,
            new_value={
                "lawyer_id": int(lawyer.id),
                "starts_at": starts_at.isoformat(),
                "ends_at": ends_at.isoformat(),
                "status": "available",
            },
            comment=note or "Добавлен свободный слот консультации",
        )
        await db.commit()
        await db.refresh(slot)
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    return {"ok": True, "slot": _serialize_slot(slot, include_internal=_is_admin(actor))}


@router.post("/test")
async def create_test_slots(
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    # Compatibility for isolated automated/local fixtures only. This route is
    # deliberately absent as a production capability and is not shown in UI.
    if str(settings.app_env or "").strip().lower() not in {"local", "test"}:
        raise HTTPException(404, "Маршрут недоступен")
    actor = await _staff(request, db, x_admin_token)
    if not _is_admin(actor):
        raise HTTPException(403, "Тестовые данные доступны только администратору")
    try:
        lawyer = await _resolve_target_lawyer(
            db,
            actor=actor,
            requested_lawyer_id=payload.get("lawyer_id"),
        )
        count = int(payload.get("count", 2))
        duration_minutes = int(payload.get("duration_minutes", 60))
        slots = await SlotService(db).create_test_slots(
            lawyer_id=lawyer.id,
            count=count,
            duration_minutes=duration_minutes,
        )
        for slot in slots:
            await _audit_slot(
                db,
                actor=actor,
                action="CONSULTATION_TEST_SLOT_CREATED",
                slot=slot,
                new_value={
                    "lawyer_id": int(lawyer.id),
                    "starts_at": slot.starts_at.isoformat(),
                    "ends_at": slot.ends_at.isoformat(),
                },
                comment="Локальный/test fixture",
            )
        await db.commit()
    except (TypeError, ValueError) as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from error
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    return {"ok": True, "created": len(slots)}


@router.post("/release-expired")
async def release_expired_slots(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    if not _is_admin(actor):
        raise HTTPException(403, "Массовая сверка резервов доступна только администратору")
    released = await SlotService(db).release_expired_holds()
    if released:
        await _audit_slot(
            db,
            actor=actor,
            action="CONSULTATION_EXPIRED_HOLDS_RELEASED",
            slot=None,
            new_value={"released": int(released)},
            comment="Администратор запустил адресную системную сверку истёкших hold",
        )
    await db.commit()
    return {"ok": True, "released": int(released)}


@router.delete("/{slot_id}")
async def delete_slot(
    slot_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _staff(request, db, x_admin_token)
    try:
        slot = (
            await db.execute(
                select(ConsultationSlot)
                .where(ConsultationSlot.id == int(slot_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if slot is None:
            raise HTTPException(404, "Слот не найден")
        if actor.role == ROLE_LAWYER and int(slot.lawyer_id) != int(actor.lawyer_id or 0):
            raise HTTPException(403, "Юрист может удалить только собственный свободный слот")
        if str(slot.status) != "available":
            raise HTTPException(409, "Удалить можно только свободный слот без резерва и записи")
        old_value = {
            "lawyer_id": int(slot.lawyer_id),
            "starts_at": slot.starts_at.isoformat(),
            "ends_at": slot.ends_at.isoformat(),
            "status": str(slot.status),
        }
        await _audit_slot(
            db,
            actor=actor,
            action="CONSULTATION_SLOT_DELETED",
            slot=slot,
            old_value=old_value,
            new_value={"deleted": True},
            comment="Свободный слот удалён из расписания",
        )
        await db.delete(slot)
        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise
    return {"ok": True}


@router.get("/ui", response_class=HTMLResponse)
async def slots_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await _staff(request, db, x_admin_token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login?next=/consultation-slots/ui", status_code=303)
        raise
    return HTMLResponse(SLOTS_HTML)


SLOTS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Расписание консультаций</title>
<style>
:root{--bg:#f4f6fa;--card:#fff;--ink:#172033;--muted:#667085;--line:#e4e7ec;--blue:#3157d5;--green:#14804a;--red:#b42318;--amber:#a15c00;--blue2:#eef2ff;--shadow:0 10px 28px rgba(16,24,40,.06)}*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:var(--bg);margin:0;color:var(--ink)}header{background:linear-gradient(135deg,#111827,#26334f);color:#fff;padding:18px 22px}.head,main{max-width:1140px;margin:auto}.head{display:flex;justify-content:space-between;gap:12px;align-items:center}.links{display:flex;gap:8px;flex-wrap:wrap}.button,button{display:inline-block;border:0;border-radius:10px;padding:9px 12px;background:var(--blue);color:#fff;text-decoration:none;font-weight:750;cursor:pointer}.button.secondary,button.secondary{background:#475467}.danger{background:var(--red)}main{padding:20px}.intro,.card{background:var(--card);border:1px solid var(--line);border-radius:15px;padding:15px;margin-bottom:13px;box-shadow:var(--shadow)}.intro{background:var(--blue2);border-color:#c7d2fe}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:11px}.field label{display:block;font-size:12px;font-weight:750;margin-bottom:5px}.field input,.field select{width:100%;padding:10px;border:1px solid #d0d5dd;border-radius:10px;font:inherit}.actions{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:12px}.slot{display:grid;grid-template-columns:1.1fr 1.5fr .8fr auto;gap:10px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line)}.slot:last-child{border-bottom:0}.badge{display:inline-flex;border-radius:999px;padding:5px 8px;background:#eef2f6;font-size:12px;font-weight:750}.muted{color:var(--muted);font-size:12px;line-height:1.45}.feedback{min-height:20px;margin-top:9px}.ok{color:var(--green)}.bad{color:var(--red)}button:disabled{opacity:.55;cursor:wait}@media(max-width:760px){.head{align-items:flex-start;flex-direction:column}.grid,.slot{grid-template-columns:1fr}.links,.actions{width:100%}.button,button{width:100%;text-align:center}main{padding:12px}}
</style></head><body><header><div class="head"><div><h1 style="margin:0 0 4px;font-size:22px">🗓 Расписание консультаций</h1><div style="font-size:13px;color:#d0d5dd">Свободное время → резерв клиента → подтверждённая консультация</div></div><div class="links"><a class="button secondary" id="backLink" href="/operator">Рабочий кабинет</a></div></div></header><main>
<section class="intro"><b>Как работает запись.</b> Клиент видит только свободные слоты. После выбора время удерживается на <span id="holdMinutes">—</span> мин.; если подтверждение/оплата не завершены вовремя, резерв освобождается системой.</section>
<section class="card"><h2 style="margin-top:0">Добавить свободное время</h2><div class="grid"><div class="field" id="lawyerField"><label for="lawyer">Юрист</label><select id="lawyer"></select></div><div class="field"><label for="starts">Начало</label><input id="starts" type="datetime-local"></div><div class="field"><label for="ends">Окончание</label><input id="ends" type="datetime-local"></div><div class="field"><label for="note">Комментарий</label><input id="note" maxlength="500" placeholder="Например: онлайн"></div></div><div class="actions"><button id="createButton" onclick="createSlot(this)">Добавить слот</button><button id="releaseButton" class="secondary" onclick="releaseExpired(this)" hidden>Сверить истёкшие резервы</button></div><div id="message" class="feedback muted" role="status" aria-live="polite"></div></section>
<section class="card"><div style="display:flex;justify-content:space-between;gap:10px;align-items:center;flex-wrap:wrap"><div><h2 style="margin:0">Текущее расписание</h2><div class="muted">Удалить можно только свободный слот. Резерв и подтверждённая запись защищены.</div></div><button class="secondary" onclick="loadSlots(this)">Обновить</button></div><div id="slots"><div class="muted" style="padding:18px 0">Загрузка…</div></div></section>
</main><script>
let context=null,lawyers={};
const statusNames={available:'Свободен',held:'Временный резерв',booked:'Подтверждён',cancelled:'Отменён',client_no_show:'Неявка клиента',lawyer_no_show:'Неявка юриста',completed:'Завершён'};
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function fmt(v){return v?new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'}).format(new Date(v)):'—'}
function feedback(text,ok=true){message.textContent=text;message.className='feedback '+(ok?'ok':'bad')}
async function api(path,opts={}){const r=await fetch(path,{...opts,credentials:'same-origin',cache:'no-store',headers:{'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(r.status===401||r.status===403){if(r.status===401)location.href='/login?next=/consultation-slots/ui';throw new Error(d.detail||'Недостаточно прав')}if(!r.ok)throw new Error(d.detail||'Ошибка запроса');return d}
async function busy(button,work){if(button?.disabled)return;const label=button?.textContent||'';if(button){button.disabled=true;button.textContent='Сохраняем…'}try{return await work()}finally{if(button){button.disabled=false;button.textContent=label}}}
function holdText(x){if(x.status!=='held'||!x.hold_expires_at)return '';const ms=new Date(x.hold_expires_at)-new Date();return `<div class="muted">${ms>0?'Резерв до '+fmt(x.hold_expires_at)+' · '+Math.ceil(ms/60000)+' мин.':'Резерв истёк; система вернёт клиента к выбору времени'}</div>`}
function slotCard(x){const lawyerName=lawyers[x.lawyer_id]||('Юрист #'+x.lawyer_id),canDelete=x.status==='available';return `<div class="slot"><div><b>${esc(lawyerName)}</b><div class="muted">${esc(x.note||'Без комментария')}</div></div><div><b>${esc(fmt(x.starts_at))} — ${esc(fmt(x.ends_at))}</b>${holdText(x)}</div><div><span class="badge">${esc(statusNames[x.status]||x.status)}</span></div><div>${canDelete?`<button class="danger" onclick="removeSlot(${Number(x.id)},this)">Удалить свободный слот</button>`:'<span class="muted">Изменение недоступно</span>'}</div></div>`}
async function loadSlots(button=null){return busy(button,async()=>{try{const selected=context?.can_manage_all_lawyers&&lawyer.value?`?lawyer_id=${encodeURIComponent(lawyer.value)}`:'';const rows=await api('/consultation-slots'+selected);slots.innerHTML=rows.length?rows.map(slotCard).join(''):'<div class="muted" style="padding:18px 0">Слотов по выбранному юристу пока нет.</div>'}catch(e){slots.innerHTML=`<div class="bad" style="padding:18px 0">${esc(e.message)}</div>`}})}
async function createSlot(button){return busy(button,async()=>{try{if(!starts.value||!ends.value)throw new Error('Укажите начало и окончание');const lawyerId=context.can_manage_all_lawyers?lawyer.value:context.lawyer_id;const body={lawyer_id:lawyerId,starts_at:new Date(starts.value).toISOString(),ends_at:new Date(ends.value).toISOString(),note:note.value};await api('/consultation-slots',{method:'POST',body:JSON.stringify(body)});feedback('Слот добавлен. Он сразу доступен клиентам как свободное время.');note.value='';await loadSlots()}catch(e){feedback(e.message,false)}})}
async function removeSlot(id,button){if(!confirm('Удалить этот свободный слот? Подтверждённые записи и резервы этой кнопкой удалить нельзя.'))return;return busy(button,async()=>{try{await api('/consultation-slots/'+id,{method:'DELETE'});feedback('Свободный слот удалён.');await loadSlots()}catch(e){feedback(e.message,false)}})}
async function releaseExpired(button){return busy(button,async()=>{try{const d=await api('/consultation-slots/release-expired',{method:'POST',body:'{}'});feedback(`Сверка завершена. Освобождено резервов: ${d.released}.`);await loadSlots()}catch(e){feedback(e.message,false)}})}
async function boot(){try{context=await api('/consultation-slots/context');holdMinutes.textContent=String(context.hold_minutes||'—');const ls=await api('/consultation-slots/lawyers');lawyers=Object.fromEntries(ls.map(x=>[Number(x.id),x.full_name]));lawyer.innerHTML=ls.length?ls.map(x=>`<option value="${Number(x.id)}">${esc(x.full_name)}</option>`).join(''):'<option value="">Нет активных юристов</option>';if(!context.can_manage_all_lawyers){lawyerField.hidden=true;backLink.href='/lawyer/workspace/ui'}else{backLink.href='/admin/workdesk/ui';releaseButton.hidden=false;lawyer.addEventListener('change',()=>loadSlots())}await loadSlots()}catch(e){document.querySelector('main').innerHTML=`<section class="card bad"><b>Расписание не загружено</b><p>${esc(e.message)}</p><a class="button secondary" href="/operator">Вернуться в кабинет</a></section>`}}
boot();
</script></body></html>
"""
