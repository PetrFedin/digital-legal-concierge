from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.consultation_outcomes import (
    OUTCOMES_HTML,
    actor_id_from_token,
    available_slots,
    rebook_after_lawyer_no_show,
    refund_after_lawyer_no_show,
    require_admin,
)
from app.api.guided_consultation_outcomes import (
    _inject_client_no_show_ui,
    close_after_client_no_show,
    guided_outcome_queue,
    rebook_after_client_no_show,
)
from app.api.legacy_consultation_outcome_guard import (
    inject_legacy_outcome_ui,
    legacy_outcome_queue,
    resolve_legacy_outcome,
)
from app.config import settings
from app.db.session import get_db
from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import DocumentAccessError, resolve_document_actor

router = APIRouter(
    prefix="/admin/consultation-outcomes",
    tags=["admin", "consultation-outcomes"],
)


async def product_mark_lawyer_no_show(
    consultation_id: int,
    payload: dict,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Record no-show against the exact slot snapshot visible to the admin.

    A consultation can be rebooked while another tab is stale. Binding the
    mutation to ``expected_slot_id`` prevents an old no-show action from being
    applied to the replacement appointment. The domain service separately
    verifies exact actor/comment provenance for network retries after success.
    """

    actor = require_admin(x_admin_token)
    raw_expected_slot = payload.get("expected_slot_id")
    try:
        expected_slot_id = (
            int(raw_expected_slot)
            if raw_expected_slot not in {None, ""}
            else None
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(
            status_code=400,
            detail="Некорректный снимок времени консультации. Обновите карточку.",
        ) from error

    try:
        consultation = await ConsultationOutcomeService(db).mark_lawyer_no_show(
            consultation_id=consultation_id,
            admin_id=actor_id_from_token(actor),
            comment=payload.get("comment") or "",
            expected_slot_id=expected_slot_id,
        )
        await db.commit()
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ConsultationOutcomeError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": consultation.id,
        "status": consultation.status,
        "slot_id": consultation.slot_id,
    }


# One runtime owner per public path. Existing endpoint functions remain the
# implementation source while this module is the only router mounted by the
# application. The lawyer-no-show path is wrapped here because stale-slot
# provenance is a product-boundary requirement shared by API and browser UI.
router.add_api_route(
    "",
    guided_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_queue",
)
router.add_api_route(
    "/slots",
    available_slots,
    methods=["GET"],
    name="consultation_outcomes_slots",
)
router.add_api_route(
    "/{consultation_id}/lawyer-no-show",
    product_mark_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show",
)
router.add_api_route(
    "/{consultation_id}/rebook",
    rebook_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/refund",
    refund_after_lawyer_no_show,
    methods=["POST"],
    name="consultation_outcomes_lawyer_no_show_refund",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/rebook",
    rebook_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_rebook",
)
router.add_api_route(
    "/{consultation_id}/client-no-show/close",
    close_after_client_no_show,
    methods=["POST"],
    name="consultation_outcomes_client_no_show_close",
)
router.add_api_route(
    "/legacy",
    legacy_outcome_queue,
    methods=["GET"],
    name="consultation_outcomes_legacy_queue",
)
router.add_api_route(
    "/{consultation_id}/legacy/resolve",
    resolve_legacy_outcome,
    methods=["POST"],
    name="consultation_outcomes_legacy_resolve",
)


def _js_string(value: object) -> str:
    return json.dumps(str(value or ""), ensure_ascii=False).replace("<", "\\u003c")


def _inject_business_timezone_ui(html: str) -> str:
    """Unify outcome-desk time, hierarchy and stale-action recovery.

    Staff may work from another region, so the same consultation must look
    identical in Telegram, Workdesk and this desk. Mutating forms also retain
    their local drafts on a 409 and refresh the authoritative queue instead of
    leaving the operator with a stale success/failure ambiguity.
    """

    head, marker, tail = html.rpartition("</body>")
    if not marker:
        raise RuntimeError("Consultation outcomes template contract changed: closing body missing")
    zone = _js_string(settings.business_timezone)
    label = _js_string(settings.business_timezone_label)
    patch = f"""
<style>
.guided-section-label{{margin:12px 0 6px;color:#667085;font-size:11px;font-weight:800;text-transform:uppercase;letter-spacing:.08em}}
.guided-secondary{{border-top:1px solid #e4e7ec;margin-top:12px;padding-top:10px}}
.guided-secondary .row{{margin-top:0!important}}
</style>
<script>
(function(){{
  const businessTimeZone={zone};
  const businessTimeLabel={label};
  dt=function(v){{
    if(!v)return '—';
    try{{
      const rendered=new Intl.DateTimeFormat('ru-RU',{{dateStyle:'short',timeStyle:'short',timeZone:businessTimeZone}}).format(new Date(v));
      return businessTimeLabel?rendered+' '+businessTimeLabel:rendered;
    }}catch(_){{return String(v)}}
  }};

  // Preserve HTTP status on browser errors. The legacy API helper intentionally
  // exposed only text, which made a stale 409 indistinguishable from a network
  // error and prevented deterministic state refresh.
  api=async function(path,opts={{}}){{
    if(!token)throw new Error('Персональная сессия не загружена');
    const r=await fetch(path,{{...opts,credentials:'same-origin',cache:'no-store',headers:{{'x-admin-token':token,'Content-Type':'application/json',...(opts.headers||{{}})}}}});
    if(r.status===401||r.status===403){{location.href='/login';const e=new Error('Сессия истекла или недостаточно прав');e.status=r.status;throw e}}
    const d=await r.json().catch(()=>({{}}));
    if(!r.ok){{const e=new Error(d.detail||'Ошибка запроса');e.status=r.status;e.payload=d;throw e}}
    return d;
  }};

  async function refreshAfterConflict(e,row){{
    if(Number(e?.status)!==409)return false;
    let refreshed=true;
    try{{await load()}}catch(refreshError){{refreshed=false}}
    feedback(
      `Данные по делу ${{row?.case_number||'уже'}} изменились в другой вкладке или другим сотрудником. Старое действие не применено. `+
      (refreshed?'Показано актуальное состояние. ':'Не удалось обновить список автоматически. ')+
      `Введённый черновик не удалён. ${{e.message||''}}`,
      'warn'
    );
    return true;
  }}

  markNoShow=async function(id,button){{
    const row=rowsById.get(Number(id));
    const comment=document.getElementById(`comment_${{id}}_no_show`)?.value||'';
    if(!row){{feedback('Консультация уже изменилась. Обновите список.','bad');return}}
    if(!validComment(comment))return;
    if(!confirm(`Подтвердить неявку юриста по делу ${{row.case_number}}? После фиксации потребуется выбрать бесплатный перенос или возврат.`))return;
    return withConsultationAction(id,button,async()=>{{
      try{{
        await api('/admin/consultation-outcomes/'+id+'/lawyer-no-show',{{method:'POST',body:JSON.stringify({{comment:comment.trim(),expected_slot_id:row.slot_id}})}});
        drafts.delete(draftKey(id,'no_show'));
        feedback(`Неявка юриста по делу ${{row.case_number}} зафиксирована.`,'ok');
        try{{await load()}}catch(e){{feedback(`Неявка сохранена, но список не обновился: ${{e.message}}`,'warn')}}
      }}catch(e){{
        if(await refreshAfterConflict(e,row))return;
        feedback(`Неявка по делу ${{row.case_number}} не сохранена: ${{e.message}}. Черновик остаётся на экране.`,'bad');
      }}
    }});
  }};

  rebook=async function(id,button){{
    const row=rowsById.get(Number(id));
    const slotId=Number(document.getElementById('slot_'+id)?.value||0);
    const comment=document.getElementById(`comment_${{id}}_rebook`)?.value||'';
    if(!row){{feedback('Консультация уже изменилась. Обновите список.','bad');return}}
    if(!slotId){{feedback(slots.length?'Выберите новое свободное время.':'Свободных слотов сейчас нет. Свяжитесь с клиентом и вернитесь позже.','bad');return}}
    if(!validComment(comment))return;
    if(!confirm(`Подтвердить бесплатный перенос по делу ${{row.case_number}}? Повторная оплата с клиента не взимается.`))return;
    return withConsultationAction(id,button,async()=>{{
      try{{
        await api('/admin/consultation-outcomes/'+id+'/rebook',{{method:'POST',body:JSON.stringify({{slot_id:slotId,comment:comment.trim()}})}});
        drafts.delete(draftKey(id,'rebook'));selectedSlots.delete(Number(id));
        feedback(`Консультация по делу ${{row.case_number}} перенесена без повторной оплаты.`,'ok');
        try{{await load()}}catch(e){{feedback(`Перенос сохранён, но список не обновился: ${{e.message}}`,'warn')}}
      }}catch(e){{
        if(await refreshAfterConflict(e,row))return;
        feedback(`Перенос по делу ${{row.case_number}} не сохранён: ${{e.message}}. Черновик остаётся на экране.`,'bad');
      }}
    }});
  }};

  refund=async function(id,button){{
    const row=rowsById.get(Number(id));
    const comment=document.getElementById(`comment_${{id}}_refund`)?.value||'';
    if(!row){{feedback('Консультация уже изменилась. Обновите список.','bad');return}}
    if(!validComment(comment))return;
    if(!confirm(`Направить оплату консультации по делу ${{row.case_number}} в очередь возврата? Это не выполняет банковский возврат автоматически.`))return;
    return withConsultationAction(id,button,async()=>{{
      try{{
        const result=await api('/admin/consultation-outcomes/'+id+'/refund',{{method:'POST',body:JSON.stringify({{comment:comment.trim()}})}});
        drafts.delete(draftKey(id,'refund'));
        const status=paymentLabels[String(result.payment_status||'')]||'Возврат требует проверки';
        feedback(`Оплата по делу ${{row.case_number}} направлена в очередь возврата. Статус: ${{status}}.`,'ok');
        try{{await load()}}catch(e){{feedback(`Направление на возврат сохранено, но список не обновился: ${{e.message}}`,'warn')}}
      }}catch(e){{
        if(await refreshAfterConflict(e,row))return;
        feedback(`Направление по делу ${{row.case_number}} на возврат не сохранено: ${{e.message}}. Черновик остаётся на экране.`,'bad');
      }}
    }});
  }};

  async function clientNoShowProductAction(id,mode,path,button,success,confirmation){{
    const row=rowsById.get(Number(id));
    const comment=document.getElementById(`comment_${{id}}_${{mode}}`)?.value||'';
    rememberDraft(id,mode,comment);
    if(!row){{feedback('Консультация уже изменилась. Обновите список.','bad');return}}
    if(!validComment(comment))return;
    if(!confirm(confirmation.replace('{{case}}',row.case_number||'—')))return;
    return withConsultationAction(id,button,async()=>{{
      try{{
        await api(path,{{method:'POST',body:JSON.stringify({{comment:comment.trim()}})}});
        drafts.delete(draftKey(id,mode));
        feedback(success,'ok');
        try{{await load()}}catch(e){{feedback(`Действие сохранено, но список не обновился: ${{e.message}}`,'warn')}}
      }}catch(e){{
        if(await refreshAfterConflict(e,row))return;
        feedback(`Действие по делу ${{row.case_number}} не сохранено: ${{e.message}}. Черновик остаётся на экране.`,'bad');
      }}
    }});
  }}

  window.clientNoShowRebook=(id,button)=>clientNoShowProductAction(
    id,
    'client_rebook',
    `/admin/consultation-outcomes/${{id}}/client-no-show/rebook`,
    button,
    'Новая запись подготовлена. Клиенту открыт следующий актуальный шаг.',
    'Открыть новую платную запись по делу {{case}}? Старый платёж не будет переиспользован.'
  );
  window.clientNoShowClose=(id,button)=>clientNoShowProductAction(
    id,
    'client_close',
    `/admin/consultation-outcomes/${{id}}/client-no-show/close`,
    button,
    'Обращение закрыто. Неявка и платёжная история сохранены.',
    'Закрыть обращение {{case}} после подтверждённой неявки клиента? Платёжная история останется без изменений.'
  );

  const subtitle=document.querySelector('header .header p');
  if(subtitle)subtitle.textContent=subtitle.textContent+' · Время: '+(businessTimeLabel||businessTimeZone);

  function applyGuidedHierarchy(){{
    document.querySelectorAll('.case').forEach(card=>{{
      const meta=card.querySelector('.meta');
      if(meta&&!card.querySelector('.guided-now')){{
        const now=document.createElement('div');
        now.className='guided-section-label guided-now';
        now.textContent='Сейчас';
        meta.parentNode.insertBefore(now,meta);
      }}
      const next=card.querySelector('.next');
      const nextHeading=next?.querySelector('b');
      if(nextHeading)nextHeading.textContent='Главный следующий шаг';

      const actions=card.querySelector('.actions');
      if(!actions||actions.querySelector('.guided-secondary'))return;
      const links=Array.from(actions.querySelectorAll('a.button.secondary'));
      if(!links.length)return;
      const secondary=document.createElement('div');
      secondary.className='guided-secondary';
      const heading=document.createElement('div');
      heading.className='guided-section-label';
      heading.textContent='Вторичные действия';
      const row=document.createElement('div');
      row.className='row';
      links.forEach(link=>row.appendChild(link));
      secondary.appendChild(heading);
      secondary.appendChild(row);
      actions.appendChild(secondary);
      Array.from(actions.children)
        .filter(node=>node!==secondary&&node.matches('.row')&&!node.children.length)
        .forEach(node=>node.remove());
    }});
  }}

  const previousRender=render;
  render=function(rows){{
    const result=previousRender(rows);
    applyGuidedHierarchy();
    return result;
  }};
  applyGuidedHierarchy();
}})();
</script>
"""
    return head + patch + marker + tail


@router.get("/ui", response_class=HTMLResponse, name="consultation_outcomes_ui")
async def consultation_outcomes_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    """Single authenticated staff UI for every existing M2 outcome decision."""

    token = x_admin_token or request.cookies.get(settings.admin_session_cookie)
    try:
        actor = await resolve_document_actor(db, token)
    except DocumentAccessError as error:
        if error.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    except HTTPException as error:
        if error.status_code in {403, 409}:
            return RedirectResponse(url="/admin-ui", status_code=303)
        raise
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        return RedirectResponse(url="/admin-ui", status_code=303)

    html = _inject_client_no_show_ui(OUTCOMES_HTML)
    html = inject_legacy_outcome_ui(html)
    return HTMLResponse(_inject_business_timezone_ui(html))


__all__ = ["consultation_outcomes_ui", "product_mark_lawyer_no_show", "router"]
