from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.message_center import (
    CLIENT_URGENCY_NOTE,
    MAX_REPLY_LENGTH,
    MESSAGE_CENTER_HTML,
    PRIORITY_CRITICAL,
    PRIORITY_TODAY,
    ReplyPayload,
    StaffScope,
    _age_minutes,
    _deliver_message_notifications,
    _message_payload,
    present_message,
    queue_bucket,
    require_staff_scope,
)
from app.db.session import get_db
from app.domain.cases.assignment_policy import automatic_assignment_required
from app.domain.messages.message_service import MessageService
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.user import User

router = APIRouter(tags=["guided-message-center"])

_TERMINAL_CASE_STATUSES = frozenset({"M1_CLOSED", "M2_CLOSED", "ARCHIVED"})
_M2_CURRENT_RESPONSIBILITY_STATUSES = frozenset(
    {
        "SLOT_RESERVED",
        "PAYMENT_PENDING",
        "BOOKED",
        "DONE",
        "CLIENT_NO_SHOW",
        "LAWYER_NO_SHOW",
    }
)


@dataclass(frozen=True)
class CaseResponsibility:
    mode: str
    lawyer_id: int | None
    lawyer_name: str | None
    consultation_id: int | None
    consultation_status: str | None
    assignment_required: bool
    responsibility_pending: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "lawyer_id": self.lawyer_id,
            "lawyer_name": self.lawyer_name,
            "consultation_id": self.consultation_id,
            "consultation_status": self.consultation_status,
            "assignment_required": self.assignment_required,
            "responsibility_pending": self.responsibility_pending,
        }


def _is_terminal(case: Case) -> bool:
    return str(case.status) in _TERMINAL_CASE_STATUSES


def _scope_is_broad(scope: StaffScope) -> bool:
    return scope.lawyer_id is None


async def _responsibilities(
    db: AsyncSession,
    cases: list[Case],
) -> dict[int, CaseResponsibility]:
    if not cases:
        return {}

    m2_cases = [case for case in cases if str(case.route or "") == "M2"]
    m2_case_ids = [int(case.id) for case in m2_cases]
    latest_consultation: dict[int, Consultation] = {}
    if m2_case_ids:
        consultations = list(
            (
                await db.execute(
                    select(Consultation)
                    .where(Consultation.case_id.in_(m2_case_ids))
                    .order_by(
                        Consultation.case_id.asc(),
                        Consultation.created_at.desc(),
                        Consultation.id.desc(),
                    )
                )
            ).scalars().all()
        )
        for consultation in consultations:
            latest_consultation.setdefault(int(consultation.case_id), consultation)

    candidate_lawyer_ids: set[int] = {
        int(case.assigned_lawyer_id)
        for case in cases
        if case.assigned_lawyer_id is not None
    }
    for case in m2_cases:
        consultation = latest_consultation.get(int(case.id))
        if consultation is None or consultation.lawyer_id is None:
            continue
        status = str(consultation.status or "")
        if status in _M2_CURRENT_RESPONSIBILITY_STATUSES or _is_terminal(case):
            candidate_lawyer_ids.add(int(consultation.lawyer_id))

    lawyer_names: dict[int, str] = {}
    if candidate_lawyer_ids:
        lawyers = list(
            (
                await db.execute(
                    select(Lawyer).where(Lawyer.id.in_(candidate_lawyer_ids))
                )
            ).scalars().all()
        )
        lawyer_names = {int(item.id): item.full_name for item in lawyers}

    result: dict[int, CaseResponsibility] = {}
    for case in cases:
        if str(case.route or "") == "M2":
            consultation = latest_consultation.get(int(case.id))
            consultation_status = (
                str(consultation.status) if consultation is not None else None
            )
            current_lawyer_id: int | None = None
            if (
                consultation is not None
                and consultation.lawyer_id is not None
                and (
                    consultation_status in _M2_CURRENT_RESPONSIBILITY_STATUSES
                    or _is_terminal(case)
                )
            ):
                current_lawyer_id = int(consultation.lawyer_id)
            result[int(case.id)] = CaseResponsibility(
                mode="consultation_slot",
                lawyer_id=current_lawyer_id,
                lawyer_name=lawyer_names.get(current_lawyer_id),
                consultation_id=(
                    int(consultation.id) if consultation is not None else None
                ),
                consultation_status=consultation_status,
                assignment_required=False,
                responsibility_pending=current_lawyer_id is None,
            )
            continue

        assigned_lawyer_id = (
            int(case.assigned_lawyer_id)
            if case.assigned_lawyer_id is not None
            else None
        )
        assignment_required = bool(
            assigned_lawyer_id is None
            and automatic_assignment_required(case.status)
            and not _is_terminal(case)
        )
        result[int(case.id)] = CaseResponsibility(
            mode="case_assignment",
            lawyer_id=assigned_lawyer_id,
            lawyer_name=lawyer_names.get(assigned_lawyer_id),
            consultation_id=None,
            consultation_status=None,
            assignment_required=assignment_required,
            responsibility_pending=assignment_required,
        )
    return result


def _allows_case(scope: StaffScope, responsibility: CaseResponsibility) -> bool:
    return _scope_is_broad(scope) or (
        responsibility.lawyer_id is not None
        and responsibility.lawyer_id == scope.lawyer_id
    )


def _reply_allowed(
    scope: StaffScope,
    case: Case,
    responsibility: CaseResponsibility,
) -> bool:
    if _is_terminal(case):
        return False
    if not _allows_case(scope, responsibility):
        return False
    if not _scope_is_broad(scope):
        return responsibility.lawyer_id == scope.lawyer_id
    # For an actionable M1 case, attribution must be fixed before legal advice is
    # sent. M2 before slot selection remains a support conversation: broad staff
    # may answer logistics without manufacturing an M1-style case assignment.
    return not responsibility.assignment_required


def _responsibility_label(
    case: Case,
    responsibility: CaseResponsibility,
) -> str:
    if responsibility.lawyer_name:
        return responsibility.lawyer_name
    if responsibility.mode == "consultation_slot":
        return "Определится после выбора времени"
    if responsibility.assignment_required:
        return "Не назначен"
    if _is_terminal(case):
        return "Архив дела"
    return "Команда сопровождения"


def _case_payload(
    case: Case,
    *,
    client: User | None,
    responsibility: CaseResponsibility,
    scope: StaffScope,
) -> dict[str, object]:
    return {
        "id": case.id,
        "number": case.case_number,
        "status": case.status,
        "route": case.route,
        "lawyer_id": responsibility.lawyer_id,
        # Compatibility: in the message center this flag means an assignment is
        # actually required, not simply that Case.assigned_lawyer_id is null.
        "unassigned": responsibility.assignment_required,
        "responsibility": responsibility.as_dict(),
        "responsibility_label": _responsibility_label(case, responsibility),
        "reply_allowed": _reply_allowed(scope, case, responsibility),
        "read_only": _is_terminal(case),
        "client_name": client.full_name if client else None,
        "client_username": client.telegram_username if client else None,
    }


def _inject_message_center_patch(html: str) -> str:
    marker = "</body>"
    if html.count(marker) != 1:
        raise RuntimeError(
            "Message center template contract changed: </body> marker is not unique"
        )
    return html.replace(marker, _MESSAGE_CENTER_ROUTE_PATCH + marker, 1)


_MESSAGE_CENTER_ROUTE_PATCH = r"""
<script>
(function(){
  const originalRenderContext=renderContext;
  renderContext=function(c){
    if(!c?.responsibility){originalRenderContext(c);return}
    const caseId=Number(c.id),documents=localHref('/admin/workdesk/cases/'+caseId+'/action/documents');
    const label=c.route==='M2'?'Юрист консультации':'Ответственный';
    contextGrid.innerHTML=`<div class="cell"><span>Дело</span>${esc(c.number||caseId)}</div><div class="cell"><span>Клиент</span>${esc(c.client_name||'Клиент')}</div><div class="cell"><span>Статус</span>${esc(c.status||'—')}</div><div class="cell"><span>${esc(label)}</span>${esc(c.responsibility_label||'—')}</div>`;
    contextLinks.innerHTML=`<a class="button secondary" href="${localHref('/operator')}">Рабочий стол</a><a class="button secondary" href="${documents}">Документы дела</a>`;
  };
  const originalRenderActionState=renderActionState;
  renderActionState=function(d){
    if(!d?.case||typeof d.case.reply_allowed==='undefined'){originalRenderActionState(d);return}
    const messages=d.messages||[],latest=messages.length?messages[messages.length-1]:null,waiting=latest?.sender_type==='client';
    if(d.case.read_only){
      currentState.innerHTML='<span class="badge green">Дело завершено</span> Переписка сохранена в архиве.';
      nextStepTitle.textContent='Только просмотр';
      nextStepText.textContent='Новые сообщения из закрытого дела не отправляются. Откройте рабочий стол или архив для дальнейшей проверки.';
      nextStepActions.innerHTML='<a class="button secondary" href="/operator">Рабочий стол</a>';
      replyBox.classList.add('hidden');return;
    }
    if(!d.case.reply_allowed){
      if(d.case.route==='M2'){
        currentState.innerHTML='<span class="badge amber">Юрист консультации ещё не определён</span> Ответственный появится после выбора времени.';
        nextStepTitle.textContent='Не создавать случайное назначение';
        nextStepText.textContent='Продолжите клиентский сценарий выбора времени. Для консультации ответственный определяется слотом, а не общей очередью М1.';
        nextStepActions.innerHTML='<a class="button secondary" href="/operator">Рабочий стол</a>';
      }else{
        currentState.innerHTML='<span class="badge red">Требуется ответственный юрист</span> Для этого этапа М1 сначала нужно назначение.';
        nextStepTitle.textContent='Назначить ответственного';
        nextStepText.textContent='Откройте рабочий стол, назначьте юриста и затем вернитесь в диалог.';
        nextStepActions.innerHTML='<a class="button" href="/admin/workdesk/ui">Открыть рабочий стол</a>';
      }
      replyBox.classList.add('hidden');return;
    }
    if(waiting){
      currentState.innerHTML='<span class="badge amber">Клиент ждёт ответа</span> Последнее сообщение пришло от клиента.';
      nextStepTitle.textContent='Ответить клиенту';
      nextStepText.textContent=d.case.route==='M2'?'Ответ будет сохранён в деле и отправлен текущим юристом консультации или командой поддержки.':'Проверьте контекст дела и отправьте один завершённый ответ.';
      nextStepActions.innerHTML='<button onclick="showReplyBox()">Перейти к ответу</button>';
      replyBox.classList.remove('hidden');return;
    }
    currentState.innerHTML='<span class="badge green">Ответ команды последний</span> Сейчас очередь за клиентом.';
    nextStepTitle.textContent='Ожидать ответ клиента';
    nextStepText.textContent='Дополнительного изменения статуса не требуется. При необходимости можно отправить уточнение.';
    nextStepActions.innerHTML='<button class="ghost" onclick="showReplyBox()">Написать дополнительное сообщение</button>';
    replyBox.classList.add('hidden');
  };
})();
</script>
"""


@router.get("/message-center/status")
async def guided_message_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    messages = list(
        (
            await db.execute(
                select(Message)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1000)
            )
        ).scalars().all()
    )

    by_case: dict[int, list[Message]] = defaultdict(list)
    for message in messages:
        by_case[int(message.case_id)].append(message)
    if not by_case:
        return {
            "conversation_count": 0,
            "unread_count": 0,
            "waiting_count": 0,
            "critical_count": 0,
            "today_count": 0,
            "unassigned_count": 0,
            "overdue_count": 0,
            "items": [],
        }

    cases = list(
        (
            await db.execute(select(Case).where(Case.id.in_(list(by_case))))
        ).scalars().all()
    )
    responsibilities = await _responsibilities(db, cases)
    active_cases = [case for case in cases if not _is_terminal(case)]
    client_ids = list({int(case.client_id) for case in active_cases})
    clients = (
        list(
            (
                await db.execute(select(User).where(User.id.in_(client_ids)))
            ).scalars().all()
        )
        if client_ids
        else []
    )
    clients_by_id = {int(client.id): client for client in clients}

    items: list[dict[str, object]] = []
    total_unread = 0
    waiting_count = 0
    critical_count = 0
    today_count = 0
    unassigned_count = 0
    overdue_count = 0
    for case in active_cases:
        responsibility = responsibilities[int(case.id)]
        if not _allows_case(scope, responsibility):
            continue
        case_messages_list = by_case.get(int(case.id), [])
        if not case_messages_list:
            continue
        client = clients_by_id.get(int(case.client_id))
        latest = case_messages_list[0]
        latest_view = present_message(latest.text, sender_type=latest.sender_type)
        unread_count = sum(
            1
            for message in case_messages_list
            if message.sender_type == "client" and not message.is_read
        )
        waiting_for_reply = latest.sender_type == "client"
        age_minutes = _age_minutes(latest.created_at)
        overdue = bool(
            waiting_for_reply
            and age_minutes is not None
            and age_minutes >= 240
        )
        critical = bool(
            waiting_for_reply and latest_view.priority == PRIORITY_CRITICAL
        )
        today = bool(
            waiting_for_reply and latest_view.priority == PRIORITY_TODAY
        )
        assignment_required = responsibility.assignment_required
        bucket = queue_bucket(
            waiting_for_reply=waiting_for_reply,
            overdue=overdue,
            priority=latest_view.priority,
        )
        needs_attention = bool(
            waiting_for_reply
            and (overdue or critical or today or assignment_required)
        )

        total_unread += unread_count
        waiting_count += int(waiting_for_reply)
        critical_count += int(critical)
        today_count += int(today)
        unassigned_count += int(waiting_for_reply and assignment_required)
        overdue_count += int(overdue)
        items.append(
            {
                "case_id": case.id,
                "case_number": case.case_number,
                "case_status": case.status,
                "route": case.route,
                "lawyer_id": responsibility.lawyer_id,
                "unassigned": assignment_required,
                "responsibility": responsibility.as_dict(),
                "responsibility_label": _responsibility_label(
                    case, responsibility
                ),
                "reply_allowed": _reply_allowed(scope, case, responsibility),
                "client_name": client.full_name if client else None,
                "client_username": client.telegram_username if client else None,
                "latest_message_id": latest.id,
                "latest_sender_type": latest.sender_type,
                "latest_text": latest.text[:1000],
                "latest_preview": latest_view.body[:1000],
                "latest_created_at": (
                    latest.created_at.isoformat() if latest.created_at else None
                ),
                "age_minutes": age_minutes,
                "unread_count": unread_count,
                "waiting_for_reply": waiting_for_reply,
                "overdue": overdue,
                "critical": critical,
                "today": today,
                "needs_attention": needs_attention,
                "category": latest_view.category,
                "urgency": latest_view.urgency,
                "priority": latest_view.priority,
                "priority_label": latest_view.priority_label,
                "priority_note": (
                    CLIENT_URGENCY_NOTE if latest_view.structured else None
                ),
                "queue_bucket": bucket,
                "message_count": len(case_messages_list),
            }
        )

    groups: dict[int, list[dict[str, object]]] = defaultdict(list)
    for item in items:
        groups[int(item["queue_bucket"])].append(item)
    ordered: list[dict[str, object]] = []
    for bucket in range(5):
        ordered.extend(
            sorted(
                groups.get(bucket, []),
                key=lambda item: (
                    bool(item["unassigned"]),
                    str(item["latest_created_at"] or ""),
                ),
                reverse=True,
            )
        )
    return {
        "conversation_count": len(ordered),
        "unread_count": total_unread,
        "waiting_count": waiting_count,
        "critical_count": critical_count,
        "today_count": today_count,
        "unassigned_count": unassigned_count,
        "overdue_count": overdue_count,
        "priority_note": CLIENT_URGENCY_NOTE,
        "items": ordered,
    }


@router.get("/message-center/cases/{case_id}/messages")
async def guided_case_messages(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    try:
        case = await db.get(Case, case_id)
        if case is None:
            raise HTTPException(status_code=404, detail="Дело не найдено")
        responsibility = (await _responsibilities(db, [case]))[int(case.id)]
        if not _allows_case(scope, responsibility):
            raise HTTPException(
                status_code=403,
                detail="Дело не относится к текущему юристу",
            )
        client = await db.get(User, case.client_id)
        service = MessageService(db)
        messages = await service.list_case_messages(case_id)
        latest_message_id = messages[-1].id if messages else None
        # Reading a closed case is allowed for audit/history, but no legal or
        # financial mutation is exposed from the archive view.
        await service.mark_client_messages_read(case_id)
        await db.commit()
        return {
            "case": _case_payload(
                case,
                client=client,
                responsibility=responsibility,
                scope=scope,
            ),
            "latest_message_id": latest_message_id,
            "messages": [_message_payload(message) for message in messages],
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post("/message-center/cases/{case_id}/reply")
async def guided_reply_to_client(
    case_id: int,
    payload: ReplyPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_staff_scope(request, db, x_admin_token)
    text = payload.text.strip()
    if len(text) < 2:
        raise HTTPException(status_code=400, detail="Введите текст ответа")
    if len(text) > MAX_REPLY_LENGTH:
        raise HTTPException(
            status_code=400,
            detail=f"Ответ не должен превышать {MAX_REPLY_LENGTH} символов",
        )

    service = MessageService(db)
    try:
        case = await service.lock_case(case_id)
        responsibility = (await _responsibilities(db, [case]))[int(case.id)]
        if not _allows_case(scope, responsibility):
            raise HTTPException(
                status_code=403,
                detail="Дело не относится к текущему юристу",
            )
        if _is_terminal(case):
            raise HTTPException(
                status_code=409,
                detail="Дело завершено. Переписка доступна только для просмотра.",
            )
        if not _reply_allowed(scope, case, responsibility):
            if responsibility.assignment_required:
                detail = (
                    "Перед ответом назначьте ответственного юриста в рабочем столе."
                )
            else:
                detail = "Ответ сейчас недоступен для этой роли или этапа."
            raise HTTPException(status_code=409, detail=detail)

        latest_message_id = await service.latest_message_id(case_id)
        if latest_message_id != payload.expected_last_message_id:
            raise HTTPException(
                status_code=409,
                detail=(
                    "В диалоге появились новые сообщения. Обновите переписку "
                    "перед отправкой ответа."
                ),
            )

        if _scope_is_broad(scope):
            if payload.lawyer_id is not None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Администратор отправляет сообщение от имени команды, "
                        "а не под чужой учётной записью юриста."
                    ),
                )
            message_lawyer_id = None
        else:
            message_lawyer_id = scope.lawyer_id

        created = await service.create_lawyer_message(
            case=case,
            lawyer_id=message_lawyer_id,
            text=text,
        )
        await service.mark_client_messages_read(case_id)
        notifications = await NotificationEngine(db).emit(
            event_code="STAFF_MESSAGE_REPLIED",
            case_id=case.id,
            user_id=case.client_id,
            payload={
                "case_number": case.case_number,
                "text": text,
            },
            dedupe_key=f"case:{case.id}:message:{created.id}:staff-reply",
        )
        notification_ids = tuple(
            int(item.id) for item in notifications if item.id is not None
        )
        message_id = int(created.id)
        created_at = created.created_at.isoformat() if created.created_at else None
        await db.commit()
        delivery = await _deliver_message_notifications(db, notification_ids)
        return {
            "ok": True,
            "message_id": message_id,
            "created_at": created_at,
            "case_id": case.id,
            "latest_message_id": message_id,
            "delivery": delivery,
            "responsibility": responsibility.as_dict(),
        }
    except HTTPException:
        await db.rollback()
        raise
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.get("/message-center/ui", response_class=HTMLResponse)
async def guided_message_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    try:
        await require_staff_scope(request, db, x_admin_token)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse(url="/login", status_code=303)
        raise
    return HTMLResponse(_inject_message_center_patch(MESSAGE_CENTER_HTML))
