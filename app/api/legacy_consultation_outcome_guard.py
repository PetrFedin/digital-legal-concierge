from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.domain.consultations.legacy_outcome_resolution_service import (
    LegacyConsultationOutcomeResolutionError,
    LegacyConsultationOutcomeResolutionService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN
from app.security.document_access import resolve_document_actor

router = APIRouter(tags=["legacy-consultation-outcomes"])


async def _admin(request: Request, db: AsyncSession, header_token: str | None):
    token = header_token or request.cookies.get(settings.admin_session_cookie)
    actor = await resolve_document_actor(db, token)
    if actor.role not in {ROLE_ADMIN, ROLE_SUPERADMIN}:
        raise HTTPException(status_code=403, detail="Доступ только для администратора")
    return actor


@router.get("/admin/consultation-outcomes/legacy")
async def legacy_outcome_queue(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await _admin(request, db, x_admin_token)
    rows = list(
        (
            await db.execute(
                select(Consultation, Case, User, Lawyer)
                .join(Case, Case.id == Consultation.case_id)
                .join(User, User.id == Case.client_id)
                .outerjoin(Lawyer, Lawyer.id == Consultation.lawyer_id)
                .where(Consultation.status == ConsultationStatus.DONE.value)
                .where(Consultation.decision == "other")
                .where(Case.route == "M2")
                .where(Case.status == CaseStatus.M2_CONSULTATION_DONE.value)
                .order_by(Consultation.updated_at.asc(), Consultation.id.asc())
                .limit(300)
            )
        ).all()
    )
    return [
        {
            "consultation_id": int(consultation.id),
            "case_id": int(case.id),
            "case_number": case.case_number,
            "client_name": user.full_name,
            "lawyer_id": consultation.lawyer_id,
            "lawyer_name": lawyer.full_name if lawyer else "Юрист не указан",
            "status": str(consultation.status),
            "status_label": "Старый неопределённый результат — требуется решение",
            "resolution_kind": "legacy_other",
            "lawyer_result": str(consultation.lawyer_result or "").strip() or None,
            "decision": str(consultation.decision or ""),
            "scheduled_at": (
                consultation.scheduled_at.isoformat()
                if consultation.scheduled_at
                else None
            ),
            "updated_at": (
                consultation.updated_at.isoformat()
                if consultation.updated_at
                else None
            ),
            "next_action": (
                "Выберите законченный итог: закрыть обращение, перевести в M1 "
                "или рекомендовать повторную консультацию"
            ),
        }
        for consultation, case, user, lawyer in rows
    ]


@router.post("/admin/consultation-outcomes/{consultation_id}/legacy/resolve")
async def resolve_legacy_outcome(
    consultation_id: int,
    payload: dict,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    actor = await _admin(request, db, x_admin_token)
    try:
        consultation, case = await LegacyConsultationOutcomeResolutionService(db).resolve(
            consultation_id=consultation_id,
            admin_id=int(actor.account_id),
            decision=payload.get("decision"),
            comment=payload.get("comment") or "",
        )
        await db.commit()
        await db.refresh(consultation)
        await db.refresh(case)
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except LegacyConsultationOutcomeResolutionError as error:
        await db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise
    return {
        "ok": True,
        "consultation_id": int(consultation.id),
        "decision": str(consultation.decision),
        "case_id": int(case.id),
        "case_status": str(case.status),
        "case_route": case.route,
        "next_action": case.next_action,
    }


_LEGACY_OUTCOME_UI_PATCH = r"""
<script>
(function(){
  const requestedCaseId=Number(new URLSearchParams(location.search).get('case_id')||0);
  const previousCard=card;
  card=function(x){
    if(x.resolution_kind!=='legacy_other')return previousCard(x);
    const id=Number(x.consultation_id),caseId=Number(x.case_id),result=x.lawyer_result||'Отдельный текст результата не сохранён.';
    const saved=drafts.get(draftKey(id,'legacy'))||'';
    return `<article class="case"><div class="case-head"><div><h3>${esc(x.case_number)}</h3><div class="muted">${esc(x.client_name)} · ${esc(x.lawyer_name||'Юрист не указан')}</div></div><span class="badge red">Незавершённый старый итог</span></div><div class="meta"><div class="cell"><span>Встреча</span>${esc(dt(x.scheduled_at))}</div><div class="cell"><span>Старое решение</span>Иное / неопределённое</div></div><div class="next"><b>Результат юриста</b><br>${esc(result)}</div><div class="notice"><b>Нужно завершить старый процесс.</b> Новые консультации больше не допускают неопределённый итог. Выберите ровно один законченный следующий шаг; generic-смена статуса для этого запрещена.</div><label class="rule" for="legacy_decision_${id}"><b>Конечный следующий шаг</b></label><select id="legacy_decision_${id}" class="select"><option value="">Выберите решение</option><option value="close">Закрыть обращение</option><option value="to_m1">Перевести в полное ведение M1</option><option value="follow_up">Рекомендовать повторную консультацию</option></select><label class="rule" for="legacy_comment_${id}"><b>Основание решения</b></label><textarea id="legacy_comment_${id}" oninput="rememberDraft(${id},'legacy',this.value)" placeholder="Что проверено и почему выбран этот следующий шаг">${esc(saved)}</textarea><div class="hint">Минимум 10 символов. При follow-up новая консультация не создаётся автоматически — клиент сам подтвердит повторную запись в Telegram.</div><div class="rule"><b>Ничего не изменится</b>, пока вы не подтвердите решение.</div><div class="row" style="margin-top:9px"><button data-consultation-id="${id}" class="green" onclick="resolveLegacyOutcome(${id},this)">Проверить и завершить</button><a class="button secondary" href="/message-center/ui?case_id=${caseId}">Переписка</a><a class="button secondary" href="/admin/workdesk/ui?case_id=${caseId}">Открыть дело</a></div></article>`;
  };

  window.resolveLegacyOutcome=async function(id,button){
    const row=rowsById.get(Number(id)),decision=(document.getElementById('legacy_decision_'+id)?.value||'').trim(),comment=(document.getElementById('legacy_comment_'+id)?.value||'').trim();
    if(!row){feedback('Запись уже изменилась. Обновите очередь.','bad');return}
    if(!['close','to_m1','follow_up'].includes(decision)){feedback('Выберите конечный следующий шаг.','bad');return}
    if(comment.length<10){feedback('Опишите основание минимум в 10 символах. Черновик сохранён.','bad');return}
    rememberDraft(id,'legacy',comment);
    const labels={close:'закрыть обращение',to_m1:'перевести дело в M1',follow_up:'рекомендовать повторную консультацию'};
    if(!confirm(`Подтвердить: ${labels[decision]}? Старый неопределённый итог будет заменён этим audited-решением.`))return;
    return withConsultationAction(id,button,async()=>{
      try{
        const result=await api(`/admin/consultation-outcomes/${id}/legacy/resolve`,{method:'POST',body:JSON.stringify({decision,comment})});
        drafts.delete(draftKey(id,'legacy'));
        feedback(`Старый итог завершён. Новый статус дела: ${result.case_status}.`,'ok');
        try{await load()}catch(e){feedback(`Решение сохранено, но очередь не обновилась: ${e.message}`,'warn')}
      }catch(e){feedback(`Решение не сохранено: ${e.message}. Черновик оставлен.`,'bad')}
    });
  };

  load=async function(){
    const data=await Promise.all([
      api('/admin/consultation-outcomes'),
      api('/admin/consultation-outcomes/slots'),
      api('/admin/consultation-outcomes/legacy')
    ]);
    slots=data[1]||[];
    let rows=[...(data[0]||[]),...(data[2]||[])];
    rows.sort((a,b)=>{
      const rank=x=>x.resolution_kind==='legacy_other'?0:String(x.status)==='CLIENT_NO_SHOW'?1:String(x.status)==='LAWYER_NO_SHOW'?2:3;
      return rank(a)-rank(b)||String(a.starts_at||a.scheduled_at||'9999').localeCompare(String(b.starts_at||b.scheduled_at||'9999'))||Number(a.consultation_id)-Number(b.consultation_id);
    });
    if(requestedCaseId)rows=rows.filter(x=>Number(x.case_id)===requestedCaseId);
    render(rows);
    if(requestedCaseId){
      feedback(rows.length?'Показаны незавершённые решения по выбранному делу.':'По выбранному делу незавершённых консультационных решений больше нет.','muted');
    }else{
      feedback(rows.length?'Показаны консультации, требующие законченного решения.':'Очередь обработана.','muted');
    }
  };
})();
</script>
"""


def inject_legacy_outcome_ui(html: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError("Consultation outcomes template contract changed")
    return html.replace(marker, _LEGACY_OUTCOME_UI_PATCH + marker, 1)


__all__ = ["inject_legacy_outcome_ui", "router"]
