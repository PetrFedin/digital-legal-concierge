from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    _slot_status_label,
    actor_id_from_token,
    list_outcome_queue,
    require_admin,
)
from app.config import settings
from app.db.session import get_db
from app.domain.consultations.client_no_show_resolution_service import (
    ClientNoShowResolutionError,
    ClientNoShowResolutionService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(
    prefix="/admin/consultation-outcomes",
    tags=["guided-consultation-outcomes"],
)


async def _client_no_show_rows(db: AsyncSession) -> list[dict[str, object]]:
    rows = (
        await db.execute(
            select(Consultation, Case, User, ConsultationSlot, Lawyer)
            .join(Case, Case.id == Consultation.case_id)
            .join(User, User.id == Case.client_id)
            .outerjoin(ConsultationSlot, ConsultationSlot.id == Consultation.slot_id)
            .outerjoin(Lawyer, Lawyer.id == Consultation.lawyer_id)
            .where(Consultation.status == ConsultationStatus.CLIENT_NO_SHOW)
            .where(Case.route == "M2")
            .where(
                Case.status.notin_(
                    [
                        CaseStatus.M2_CLOSED.value,
                        CaseStatus.ARCHIVED.value,
                    ]
                )
            )
            .order_by(Consultation.scheduled_at.asc(), Consultation.id.asc())
            .limit(300)
        )
    ).all()
    result: list[dict[str, object]] = []
    for consultation, case, user, slot, lawyer in rows:
        starts_at = (
            slot.starts_at
            if slot is not None
            else consultation.scheduled_at
        )
        ends_at = slot.ends_at if slot is not None else None
        if starts_at is not None and ends_at is None:
            ends_at = starts_at + timedelta(hours=1)
        result.append(
            {
                "consultation_id": consultation.id,
                "case_id": case.id,
                "case_number": case.case_number,
                "client_name": user.full_name,
                "client_telegram_id": user.telegram_id,
                "lawyer_id": consultation.lawyer_id,
                "lawyer_name": lawyer.full_name if lawyer else "Юрист не указан",
                "status": str(consultation.status),
                "status_label": "Неявка клиента — требуется решение",
                "slot_id": consultation.slot_id,
                "slot_status": slot.status if slot else "client_no_show",
                "slot_status_label": _slot_status_label(
                    slot.status if slot else "client_no_show"
                ),
                "starts_at": starts_at.isoformat() if starts_at else None,
                "ends_at": ends_at.isoformat() if ends_at else None,
                "next_action": (
                    "Согласовать новую запись с новой оплатой или закрыть обращение"
                ),
                "resolution_kind": "client_no_show",
            }
        )
    return result


@router.get("")
async def guided_outcome_queue(
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    require_admin(x_admin_token)
    existing = await list_outcome_queue(db=db, x_admin_token=x_admin_token)
    seen = {int(item["consultation_id"]) for item in existing}
    for item in await _client_no_show_rows(db):
        if int(item["consultation_id"]) not in seen:
            existing.append(item)
    existing.sort(
        key=lambda item: (
            0 if str(item.get("status")) == ConsultationStatus.CLIENT_NO_SHOW.value else 1,
            str(item.get("starts_at") or "9999"),
            int(item.get("consultation_id") or 0),
        )
    )
    return existing


@router.post("/{consultation_id}/client-no-show/rebook")
async def rebook_after_client_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        previous, replacement, case = await ClientNoShowResolutionService(
            db
        ).prepare_new_paid_booking(
            consultation_id=consultation_id,
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ClientNoShowResolutionError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "previous_consultation_id": previous.id,
        "previous_consultation_status": previous.status,
        "consultation_id": replacement.id,
        "consultation_status": replacement.status,
        "case_id": case.id,
        "case_status": case.status,
        "payment_reused": False,
    }


@router.post("/{consultation_id}/client-no-show/close")
async def close_after_client_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = require_admin(x_admin_token)
    try:
        consultation, case = await ClientNoShowResolutionService(db).close_case(
            consultation_id=consultation_id,
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ClientNoShowResolutionError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "consultation_status": consultation.status,
        "case_id": case.id,
        "case_status": case.status,
        "payment_changed": False,
    }


_CLIENT_NO_SHOW_UI_PATCH = r"""
<script>
(function(){
  const legacyCard=card;
  card=function(x){
    if(x.status!=='CLIENT_NO_SHOW')return legacyCard(x);
    const id=Number(x.consultation_id),caseId=Number(x.case_id);
    const rebookDraft=drafts.get(draftKey(id,'client_rebook'))||'';
    const closeDraft=drafts.get(draftKey(id,'client_close'))||'';
    return `<article class="case"><div class="case-head"><div><h3>${esc(x.case_number)}</h3><div class="muted">${esc(x.client_name)} · ${esc(x.lawyer_name||'Юрист не указан')}</div></div><span class="badge red">Неявка клиента</span></div><div class="meta"><div class="cell"><span>Назначенное время</span>${esc(dt(x.starts_at))}</div><div class="cell"><span>Статус времени</span>${esc(slotLabel(x))}</div><div class="cell"><span>Ответственный по встрече</span>${esc(x.lawyer_name||'Юрист не указан')}</div><div class="cell"><span>Оплата прошлой встречи</span>Не переносится на новую запись автоматически</div></div><div class="next"><b>Нужно завершить ситуацию</b><br>Согласуйте с клиентом новую запись с новой оплатой либо закройте обращение. Старый платёж и старая встреча остаются только в истории.</div><div class="actions"><div class="choice"><button data-consultation-id="${id}" class="green" onclick="openForm(${id},'client_rebook')">Открыть новую запись</button><button data-consultation-id="${id}" class="secondary" onclick="openForm(${id},'client_close')">Закрыть обращение</button></div><div class="row" style="margin-top:8px"><a class="button secondary" href="/admin/workdesk/ui?case_id=${caseId}">Открыть дело</a><a class="button secondary" href="/message-center/ui?case_id=${caseId}">Переписка</a></div><div id="form_${id}_client_rebook" class="form"><b>Новая запись после неявки клиента</b><textarea id="comment_${id}_client_rebook" oninput="rememberDraft(${id},'client_rebook',this.value)" placeholder="Что согласовано с клиентом">${esc(rebookDraft)}</textarea><div class="hint">Будет создана новая консультация на том же деле. Клиент выберет новое время, а новая запись пройдёт обычное подтверждение/оплату. Прошлый платёж не переиспользуется.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите действие.</div><div class="row"><button data-consultation-id="${id}" class="green" onclick="clientNoShowRebook(${id},this)">Подтвердить новую запись</button><button class="secondary" onclick="closeForm(${id},'client_rebook')">Вернуться без сохранения</button></div></div><div id="form_${id}_client_close" class="form"><b>Закрытие после неявки клиента</b><textarea id="comment_${id}_client_close" oninput="rememberDraft(${id},'client_close',this.value)" placeholder="Почему обращение закрывается">${esc(closeDraft)}</textarea><div class="hint">Дело станет архивным для клиента. Платёжные записи не изменяются этим действием.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите закрытие.</div><div class="row"><button data-consultation-id="${id}" class="red" onclick="clientNoShowClose(${id},this)">Подтвердить закрытие</button><button class="secondary" onclick="closeForm(${id},'client_close')">Вернуться без сохранения</button></div></div></div></article>`;
  };
  async function clientAction(id,mode,path,button,success){
    const comment=document.getElementById(`comment_${id}_${mode}`)?.value.trim()||'';
    rememberDraft(id,mode,comment);
    if(!validComment(comment))return;
    await withConsultationAction(id,button,async()=>{
      try{
        await api(path,{method:'POST',body:JSON.stringify({comment})});
        drafts.delete(draftKey(id,mode));
        feedback(success,'ok');
        await reload();
      }catch(e){feedback(e.message,'bad')}
    });
  }
  window.clientNoShowRebook=(id,button)=>clientAction(id,'client_rebook',`/admin/consultation-outcomes/${id}/client-no-show/rebook`,button,'Новая запись подготовлена. Клиенту открыт следующий актуальный шаг.');
  window.clientNoShowClose=(id,button)=>clientAction(id,'client_close',`/admin/consultation-outcomes/${id}/client-no-show/close`,button,'Обращение закрыто. Неявка и платёжная история сохранены.');
  const notice=document.querySelector('.notice');
  if(notice)notice.innerHTML='<b>Когда появляется действие.</b> Подтверждённая встреча попадает сюда через 15 минут после начала, если результат ещё не зафиксирован. После неявки юриста завершите ситуацию бесплатным переносом или возвратом. После неявки клиента — согласуйте новую запись с новой оплатой либо закройте обращение.';
  const originalRender=render;
  render=function(rows){
    originalRender(rows);
    if(!rows.length){const paragraph=document.querySelector('.empty p');if(paragraph)paragraph.textContent='Просроченных консультаций и неявок, требующих решения, сейчас нет.'}
  };
})();
</script>
"""


def _inject_client_no_show_ui(html: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError("Consultation outcomes template contract changed")
    return html.replace(marker, _CLIENT_NO_SHOW_UI_PATCH + marker, 1)


@router.get("/ui", response_class=HTMLResponse)
async def guided_consultation_outcomes_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    if not token:
        return RedirectResponse(url="/login", status_code=303)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return HTMLResponse(_inject_client_no_show_ui(OUTCOMES_HTML))