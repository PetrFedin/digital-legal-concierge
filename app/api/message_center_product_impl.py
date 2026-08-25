"""Single runtime owner for the existing staff Message Center surface.

The mature responsibility-aware implementation historically shadowed the base
Message Center by being mounted first. Staff/client correspondence is part of
the legal Case record, so public behavior must not depend on FastAPI include
order. This router owns the already-shipped endpoints and keeps presentation
snapshots on the safe side of transaction boundaries; no new messaging route or
business capability is introduced.
"""

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.guided_message_center import (
    _allows_case,
    _case_payload,
    _is_terminal,
    _reply_allowed,
    _responsibilities,
    _scope_is_broad,
    guided_message_center_status,
    guided_message_center_ui,
)
from app.api.message_center import (
    MAX_REPLY_LENGTH,
    ReplyPayload,
    StaffScope,
    _deliver_message_notifications,
    _message_payload,
    require_staff_scope,
)
from app.config import settings
from app.db.session import get_db
from app.domain.messages.message_service import MessageService
from app.domain.notifications.notification_engine import NotificationEngine
from app.models.case import Case
from app.models.message import Message
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, ROLE_SUPERADMIN
from app.security.lawyer_access import require_lawyer_actor

router = APIRouter(tags=["message-center-product"])


async def require_product_staff_scope(
    request: Request,
    db: AsyncSession,
    header_token: str | None = None,
) -> StaffScope:
    """Apply the current product-role contract to Message Center access.

    ``operator`` is an auxiliary account label, not an independent product
    authority. Historical accounts must therefore not gain broad correspondence
    access merely because the compatibility scope recognises ROLE_OPERATOR.
    Likewise, a lawyer carrying the auxiliary operator label must remain scoped
    to the exact lawyer responsibility instead of becoming broad staff access.
    """

    scope = await require_staff_scope(request, db, header_token)
    if scope.roles.intersection({ROLE_ADMIN, ROLE_SUPERADMIN}):
        return scope
    if ROLE_LAWYER not in scope.roles:
        raise HTTPException(
            status_code=403,
            detail=(
                "Роль «Оператор» является дополнительной и не даёт самостоятельного "
                "доступа к переписке. Требуется базовая роль администратора или юриста."
            ),
        )
    if scope.lawyer_id is not None:
        return scope

    token = header_token or request.cookies.get(settings.admin_session_cookie)
    lawyer_actor = await require_lawyer_actor(db, token)
    return StaffScope(
        payload=scope.payload,
        roles=scope.roles,
        lawyer_id=int(lawyer_actor.lawyer.id),
    )


@router.get("/message-center/status", name="message_center_status")
async def message_center_status(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    # The guided projection uses the same compatibility data model; run the
    # product-role guard first so an auxiliary operator token cannot reach it.
    await require_product_staff_scope(request, db, x_admin_token)
    return await guided_message_center_status(request, db, x_admin_token)


@router.get(
    "/message-center/cases/{case_id}/messages",
    name="case_messages",
)
async def case_messages(
    case_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_product_staff_scope(request, db, x_admin_token)
    try:
        case = await db.get(Case, int(case_id))
        if case is None:
            raise HTTPException(status_code=404, detail="Дело не найдено")
        responsibility = (await _responsibilities(db, [case]))[int(case.id)]
        if not _allows_case(scope, responsibility):
            raise HTTPException(
                status_code=403,
                detail="Дело не относится к текущему юристу",
            )
        client = await db.get(User, int(case.client_id))
        service = MessageService(db)
        messages = await service.list_case_messages(int(case_id))
        latest_message_id = int(messages[-1].id) if messages else None

        # Opening the staff thread is the explicit read action. Update the read
        # flags first, then freeze the complete response while ORM state is still
        # attached to the active transaction.
        await service.mark_client_messages_read(int(case_id))
        response = {
            "case": _case_payload(
                case,
                client=client,
                responsibility=responsibility,
                scope=scope,
            ),
            "latest_message_id": latest_message_id,
            "messages": [_message_payload(message) for message in messages],
        }
        await db.commit()
        return response
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.post(
    "/message-center/cases/{case_id}/reply",
    name="reply_to_client",
)
async def reply_to_client(
    case_id: int,
    payload: ReplyPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_product_staff_scope(request, db, x_admin_token)
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
        case = await service.lock_case(int(case_id))
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
            detail = (
                "Перед ответом назначьте ответственного юриста в рабочем столе."
                if responsibility.assignment_required
                else "Ответ сейчас недоступен для этой роли или этапа."
            )
            raise HTTPException(status_code=409, detail=detail)

        latest_message_id = await service.latest_message_id(int(case_id))
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
        await service.mark_client_messages_read(int(case_id))
        notifications = await NotificationEngine(db).emit(
            event_code="STAFF_MESSAGE_REPLIED",
            case_id=int(case.id),
            user_id=int(case.client_id),
            payload={
                "case_number": case.case_number,
                "text": text,
            },
            dedupe_key=f"case:{int(case.id)}:message:{int(created.id)}:staff-reply",
        )
        notification_ids = tuple(
            int(item.id) for item in notifications if item.id is not None
        )

        # Correspondence and its outbox record must become durable before the
        # network send. Freeze every ORM-derived field before that commit.
        response = {
            "ok": True,
            "message_id": int(created.id),
            "created_at": (
                created.created_at.isoformat() if created.created_at else None
            ),
            "case_id": int(case.id),
            "latest_message_id": int(created.id),
            "responsibility": responsibility.as_dict(),
        }
        await db.commit()
        delivery = await _deliver_message_notifications(db, notification_ids)
        response["delivery"] = delivery
        return response
    except HTTPException:
        await db.rollback()
        raise
    except LookupError as error:
        await db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception:
        await db.rollback()
        raise


@router.post("/message-center/{message_id}/read", name="mark_message_read")
async def mark_message_read(
    message_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    scope = await require_product_staff_scope(request, db, x_admin_token)
    try:
        message = await db.get(Message, int(message_id))
        if message is None:
            raise HTTPException(status_code=404, detail="Сообщение не найдено")
        case = await db.get(Case, int(message.case_id))
        if case is None:
            raise HTTPException(status_code=404, detail="Дело не найдено")
        responsibility = (await _responsibilities(db, [case]))[int(case.id)]
        if not _allows_case(scope, responsibility):
            raise HTTPException(
                status_code=403,
                detail="Дело не относится к текущему юристу",
            )
        message.is_read = True
        response = {
            "ok": True,
            "message_id": int(message.id),
            "case_id": int(case.id),
            "is_read": True,
        }
        await db.commit()
        return response
    except HTTPException:
        await db.rollback()
        raise
    except Exception:
        await db.rollback()
        raise


@router.get("/message-center/ui", name="message_center_ui")
async def message_center_ui(
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_admin_token: str | None = Header(default=None),
):
    await require_product_staff_scope(request, db, x_admin_token)
    return await guided_message_center_ui(request, db, x_admin_token)


__all__ = ["router"]
