from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN, decode_access_token, normalize_roles

router = APIRouter(prefix="/consultation-slots", tags=["consultation-slots"])


def require_staff(token: str | None) -> dict:
    payload = decode_access_token(token)
    roles = set(normalize_roles(payload.get("roles") if payload else None))
    if not payload or not roles.intersection({ROLE_SUPERADMIN, ROLE_ADMIN, ROLE_LAWYER}):
        raise HTTPException(403, "Доступ только для юриста или администратора")
    return payload


@router.get("/lawyers")
async def list_lawyers(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_staff(x_admin_token)
    rows = (await db.execute(select(Lawyer).where(Lawyer.is_active.is_(True)).order_by(Lawyer.full_name.asc()))).scalars().all()
    return [{"id": row.id, "full_name": row.full_name, "email": row.email} for row in rows]


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


@router.get("/ui", response_class=HTMLResponse)
async def slots_ui():
    return HTMLResponse(SLOTS_HTML)


SLOTS_HTML = r"""
<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Свободные слоты юристов</title>
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;background:#f4f5f7;margin:0;color:#111827}header{background:#111827;color:#fff;padding:18px 24px;display:flex;justify-content:space-between}main{max-width:1100px;margin:auto;padding:24px}.card{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:18px;margin-bottom:16px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}input,select{width:100%;padding:10px;border:1px solid #d1d5db;border-radius:9px;box-sizing:border-box}button{border:0;border-radius:9px;padding:10px 13px;background:#2563eb;color:#fff;font-weight:700;cursor:pointer}.danger{background:#dc2626}.badge{display:inline-block;padding:4px 8px;border-radius:999px;background:#e5e7eb;font-size:12px}.row{display:grid;grid-template-columns:1.2fr 1fr 1fr auto;gap:10px;align-items:center;padding:10px 0;border-bottom:1px solid #e5e7eb}.muted{color:#6b7280;font-size:13px}@media(max-width:700px){.grid,.row{grid-template-columns:1fr}}</style></head>
<body><header><b>⚖ Свободные слоты юристов</b><a href="/admin-ui" style="color:white">Админка</a></header><main>
<div class="card"><h2>Добавить свободное время</h2><div class="grid"><div><label>Юрист</label><select id="lawyer"></select></div><div><label>Начало</label><input id="starts" type="datetime-local"></div><div><label>Окончание</label><input id="ends" type="datetime-local"></div><div><label>Комментарий</label><input id="note" placeholder="Например: онлайн"></div></div><p><button onclick="createSlot()">Добавить слот</button></p><div id="message" class="muted"></div></div>
<div class="card"><h2>Слоты</h2><div id="slots">Загрузка…</div></div></main>
<script>
let token='';let lawyers={};
async function api(path,opts={}){const r=await fetch(path,{...opts,headers:{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{})}});const d=await r.json().catch(()=>({}));if(!r.ok)throw new Error(d.detail||'Ошибка');return d}
async function boot(){const r=await fetch('/auth/session');if(!r.ok){location.href='/login';return}const s=await r.json();token=s.api_token;const ls=await api('/consultation-slots/lawyers');lawyer.innerHTML=ls.map(x=>`<option value="${x.id}">${esc(x.full_name)}</option>`).join('');lawyers=Object.fromEntries(ls.map(x=>[x.id,x.full_name]));await loadSlots()}
async function loadSlots(){const rows=await api('/consultation-slots');slots.innerHTML=rows.length?rows.map(x=>`<div class="row"><div><b>${esc(lawyers[x.lawyer_id]||('Юрист #'+x.lawyer_id))}</b><br><span class="muted">${esc(x.note||'')}</span></div><div>${fmt(x.starts_at)} — ${fmt(x.ends_at)}</div><div><span class="badge">${esc(x.status)}</span></div><div>${x.status==='available'?`<button class="danger" onclick="removeSlot(${x.id})">Удалить</button>`:''}</div></div>`).join(''):'Свободных слотов пока нет.'}
async function createSlot(){try{await api('/consultation-slots',{method:'POST',body:JSON.stringify({lawyer_id:lawyer.value,starts_at:new Date(starts.value).toISOString(),ends_at:new Date(ends.value).toISOString(),note:note.value})});message.textContent='Слот добавлен';await loadSlots()}catch(e){message.textContent=e.message}}
async function removeSlot(id){if(!confirm('Удалить свободный слот?'))return;try{await api('/consultation-slots/'+id,{method:'DELETE'});await loadSlots()}catch(e){alert(e.message)}}
function fmt(v){return new Date(v).toLocaleString('ru-RU')}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
boot();
</script></body></html>
"""
