from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.domain.consultations.slot_service import SlotService
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    decode_access_token,
    normalize_roles,
)

router = APIRouter(prefix="/consultation-slots", tags=["consultation-slots"])


def require_staff(token: str | None) -> dict:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or not roles.intersection({ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}):
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return payload


def serialize_slot(row: ConsultationSlot) -> dict:
    return {
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


@router.get("/lawyers")
async def list_lawyers(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    rows = (
        await db.execute(
            select(Lawyer)
            .where(Lawyer.is_active.is_(True))
            .order_by(Lawyer.full_name.asc())
        )
    ).scalars().all()
    return [
        {
            "id": row.id,
            "full_name": row.full_name,
            "email": row.email,
        }
        for row in rows
    ]


@router.get("")
async def list_slots(
    lawyer_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    released = await SlotService(db).release_expired_holds()
    query = select(ConsultationSlot).order_by(ConsultationSlot.starts_at.asc())
    if lawyer_id is not None:
        query = query.where(ConsultationSlot.lawyer_id == lawyer_id)
    rows = (await db.execute(query)).scalars().all()
    if released:
        await db.commit()
    return [serialize_slot(row) for row in rows]


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
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(400, "Передайте lawyer_id, starts_at и ends_at в ISO-формате") from error

    lawyer = await db.get(Lawyer, lawyer_id)
    if not lawyer or not lawyer.is_active:
        raise HTTPException(404, "Активный юрист не найден")
    if starts_at <= datetime.now(timezone.utc):
        raise HTTPException(400, "Нельзя создать слот в прошлом")
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
    return {"ok": True, "slot": serialize_slot(slot)}


@router.post("/test")
async def create_test_slots(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    try:
        lawyer_id = int(payload["lawyer_id"])
        count = int(payload.get("count", 2))
        duration_minutes = int(payload.get("duration_minutes", 60))
    except (KeyError, TypeError, ValueError) as error:
        raise HTTPException(400, "Передайте lawyer_id и корректное количество слотов") from error

    lawyer = await db.get(Lawyer, lawyer_id)
    if not lawyer or not lawyer.is_active:
        raise HTTPException(404, "Активный юрист не найден")

    try:
        slots = await SlotService(db).create_test_slots(
            lawyer_id=lawyer_id,
            count=count,
            duration_minutes=duration_minutes,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        raise HTTPException(409, str(error)) from error

    return {
        "ok": True,
        "created": len(slots),
        "hold_minutes": SlotService.HOLD_MINUTES,
        "slots": [serialize_slot(slot) for slot in slots],
    }


@router.post("/release-expired")
async def release_expired_slots(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    released = await SlotService(db).release_expired_holds()
    await db.commit()
    return {"ok": True, "released": released}


@router.delete("/{slot_id}")
async def delete_slot(
    slot_id: int,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    slot = await db.get(ConsultationSlot, slot_id)
    if not slot:
        raise HTTPException(404, "Слот не найден")
    if slot.status in {"held", "booked"}:
        raise HTTPException(409, "Нельзя удалить удерживаемый или забронированный слот")
    await db.delete(slot)
    await db.commit()
    return {"ok": True}


@router.get("/ui", response_class=HTMLResponse)
async def slots_ui():
    return HTMLResponse(SLOTS_HTML)


SLOTS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Свободные слоты юристов</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1100px;margin:auto;padding:24px}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}input,select{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:9px;box-sizing:border-box}button{border:0;border-radius:9px;padding:10px 13px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer;margin:3px}button:disabled{opacity:.55;cursor:wait}.secondary{background:#4b5563}.danger{background:#dc2626}.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#e5e7eb;font-size:12px}.row{display:grid;grid-template-columns:1.2fr 1.2fr 1fr auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid #e5e7eb}.muted{color:#6b7280;font-size:13px}.notice{background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:12px;margin-bottom:14px}.ok{color:#15803d}.bad{color:#b91c1c}@media(max-width:700px){.grid,.row{grid-template-columns:1fr}}</style></head>
<body><header><b>⚖ Свободные слоты юристов</b><a href="/admin-ui" style="color:white">Админка</a></header><main>
<div class="card"><h2>Тестирование записи в Telegram</h2><div class="notice">Выберите юриста и создайте 1–2 ближайших свободных слота. После выбора в боте слот резервируется на 10 минут.</div><p><button onclick="createTestSlots(1,this)">Создать 1 тестовый слот</button><button onclick="createTestSlots(2,this)">Создать 2 тестовых слота</button><button class="secondary" onclick="releaseExpired(this)">Освободить истёкшие резервы</button></p><div id="testMessage" class="muted" role="status" aria-live="polite"></div></div>
<div class="card"><h2>Добавить свободное время вручную</h2><div class="grid"><div><label>Юрист</label><select id="lawyer"></select></div><div><label>Начало</label><input id="starts" type="datetime-local"></div><div><label>Окончание</label><input id="ends" type="datetime-local"></div><div><label>Комментарий</label><input id="note" placeholder="Например: онлайн"></div></div><p><button onclick="createSlot(this)">Добавить слот</button></p><div id="message" class="muted" role="status" aria-live="polite"></div></div>
<div class="card"><h2>Слоты</h2><div id="slotsMessage" class="muted" role="status" aria-live="polite"></div><div id="slots">Загрузка…</div></div></main>
<script>
let token='';let lawyers={};
const statusNames={available:'Свободен',held:'Резерв',booked:'Подтверждён',cancelled:'Отменён'};
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function withButton(button,work){if(button&&button.disabled)return;const label=button?button.textContent:'';if(button){button.disabled=true;button.textContent='Выполняется…'}try{return await work()}finally{if(button){button.disabled=false;button.textContent=label}}}
function feedback(target,text,ok){target.textContent=text;target.className=ok?'muted ok':'muted bad'}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;const ls=await api('/consultation-slots/lawyers');lawyer.innerHTML=ls.length?ls.map(x=>`<option value="${x.id}">${esc(x.full_name)}</option>`).join(''):'<option value="">Нет активных юристов</option>';lawyers=Object.fromEntries(ls.map(x=>[x.id,x.full_name]));await loadSlots()}
function holdText(x){if(x.status!=='held'||!x.hold_expires_at)return '';const ms=new Date(x.hold_expires_at)-new Date();return ms>0?`<br><span class="muted">до ${fmt(x.hold_expires_at)} · осталось ${Math.ceil(ms/60000)} мин.</span>`:'<br><span class="muted">резерв истёк</span>'}
async function loadSlots(){const rows=await api('/consultation-slots');slots.innerHTML=rows.length?rows.map(x=>`<div class="row"><div><b>${esc(lawyers[x.lawyer_id]||('Юрист #'+x.lawyer_id))}</b><br><span class="muted">${esc(x.note||'')}</span></div><div>${fmt(x.starts_at)} — ${fmt(x.ends_at)}${holdText(x)}</div><div><span class="badge">${esc(statusNames[x.status]||x.status)}</span></div><div>${x.status==='available'?`<button class="danger" onclick="removeSlot(${x.id},this)">Удалить</button>`:''}</div></div>`).join(''):'Свободных слотов пока нет.'}
async function createTestSlots(count,button){if(!lawyer.value){feedback(testMessage,'Сначала добавьте активного юриста',false);return}return withButton(button,async()=>{try{const d=await api('/consultation-slots/test',{method:'POST',body:JSON.stringify({lawyer_id:lawyer.value,count,duration_minutes:60})});feedback(testMessage,`Создано слотов: ${d.created}. Резерв в боте: ${d.hold_minutes} минут.`,true);await loadSlots()}catch(e){feedback(testMessage,e.message,false)}})}
async function releaseExpired(button){return withButton(button,async()=>{try{const d=await api('/consultation-slots/release-expired',{method:'POST',body:'{}'});feedback(testMessage,`Освобождено резервов: ${d.released}`,true);await loadSlots()}catch(e){feedback(testMessage,e.message,false)}})}
async function createSlot(button){return withButton(button,async()=>{try{if(!starts.value||!ends.value)throw new Error('Укажите начало и окончание');await api('/consultation-slots',{method:'POST',body:JSON.stringify({lawyer_id:lawyer.value,starts_at:new Date(starts.value).toISOString(),ends_at:new Date(ends.value).toISOString(),note:note.value})});feedback(message,'Слот добавлен',true);await loadSlots()}catch(e){feedback(message,e.message,false)}})}
async function removeSlot(id,button){if(!confirm('Удалить свободный слот?'))return;return withButton(button,async()=>{try{await api('/consultation-slots/'+id,{method:'DELETE'});feedback(slotsMessage,'Слот удалён',true);await loadSlots()}catch(e){feedback(slotsMessage,e.message,false)}})}
function fmt(v){return new Date(v).toLocaleString('ru-RU')}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
boot();setInterval(()=>loadSlots().catch(e=>feedback(slotsMessage,e.message,false)),30000);
</script></body></html>
"""