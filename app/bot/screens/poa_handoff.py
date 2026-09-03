from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import DocumentUploadStates
from app.domain.cases.case_history import add_case_history_event
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog

router = Router()
logger = logging.getLogger(__name__)
_ACTION = "CLIENT_POA_READY_REPORTED"


async def _show(
    callback: CallbackQuery,
    text: str,
    *,
    case_id: int | None = None,
) -> None:
    upload_callback = (
        bound_case_callback("poa_upload_document", int(case_id))
        if case_id is not None
        else "poa_upload_document"
    )
    message_callback = (
        bound_case_callback("message_create", int(case_id))
        if case_id is not None
        else "message_create"
    )
    markup = one(
        ("📎 Загрузить доверенность", upload_callback),
        ("📄 Все документы", "documents_open"),
        ("📁 Моё дело", "my_case_open"),
        ("✉️ Задать вопрос команде", message_callback),
        ("🏠 Главная", "nav_home"),
    )
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        logger.warning("Не удалось обновить экран передачи доверенности: %s", error)
        await callback.message.answer(text, reply_markup=markup)
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram недоступен после сохранения сигнала о доверенности")


@router.callback_query(lambda c: callback_matches_action(c.data, "poa_upload_document"))
async def upload_poa_document(callback: CallbackQuery, state: FSMContext, db):
    """Arm a POA upload with exact Case provenance.

    The former dedicated entry stored only ``document_type``. The shared file
    middleware requires ``document_case_id`` before it will even inspect a file,
    so the apparent one-step POA upload was a dead end. Fresh and legacy buttons
    now resolve an exact active Case first and the FSM stores both values.
    """

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="poa_upload_document",
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        await state.clear()
        return
    case = scope.case
    if case is None or str(case.status) != CaseStatus.M1_POWER_OF_ATTORNEY.value:
        await state.clear()
        await db.rollback()
        await _show(
            callback,
            "Загрузка доверенности из этого шага уже недоступна: этап дела изменился. Откройте «Моё дело» и проверьте актуальное действие.",
            case_id=int(case.id) if case is not None else None,
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    await db.rollback()
    await state.clear()
    await state.update_data(
        document_case_id=case_id,
        document_type="POWER_OF_ATTORNEY",
    )
    await state.set_state(DocumentUploadStates.waiting_file)
    await callback.message.edit_text(
        "📎 ЗАГРУЗИТЬ ДОВЕРЕННОСТЬ\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Тип документа уже выбран и привязан именно к этому обращению.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Прикрепите скан или фото доверенности в PDF, DOCX, JPG или PNG. Перед сохранением файл и Case-контекст будут проверены повторно.\n\n"
        "Сам факт загрузки не подтверждает получение оригинала юристом и не запускает претензию.",
        reply_markup=one(
            ("📄 Все документы", "documents_open"),
            ("Отменить действие", "nav_cancel"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "poa_done"))
async def report_poa_ready(callback: CallbackQuery, db):
    """Notify staff without allowing a client click to prove legal document receipt."""

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="poa_done",
        allow_legacy_message_case_context=True,
    )
    if scope is None:
        return
    user = scope.user
    case = scope.case
    if not case:
        await _show(
            callback,
            "Активное дело не найдено. Никакой юридический статус не изменён. Откройте «Моё дело», чтобы увидеть актуальное состояние.",
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    status = CaseStatus(str(case.status))
    if status != CaseStatus.M1_POWER_OF_ATTORNEY:
        await db.rollback()
        await _show(
            callback,
            "Этап доверенности уже изменился. Повторное подтверждение не требуется — откройте актуальное состояние дела.",
            case_id=case_id,
        )
        return

    existing = (
        await db.execute(
            select(AuditLog.id)
            .where(AuditLog.entity_type == "case")
            .where(AuditLog.entity_id == case_id)
            .where(AuditLog.action == _ACTION)
            .limit(1)
        )
    ).scalar_one_or_none()

    try:
        if existing is None:
            await add_case_history_event(
                db,
                actor_type="client",
                actor_id=user.id,
                case_id=case_id,
                action=_ACTION,
                new_value={"status": str(case.status), "client_reported_ready": True},
                comment=(
                    "Клиент сообщил, что доверенность оформлена/готова к передаче. "
                    "Фактическое получение должен отдельно подтвердить назначенный юрист."
                ),
            )
            await NotificationEngine(db).emit(
                event_code="M1_POA_READY_REPORTED",
                case_id=case_id,
                payload={"case_number": case_number},
                dedupe_key=f"case:{case_id}:poa-ready-reported",
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить сигнал клиента о готовности доверенности")
        await _show(
            callback,
            "Не удалось передать сообщение команде. Статус дела не изменён. Повторите действие позже или задайте вопрос команде.",
            case_id=case_id,
        )
        return

    await _show(
        callback,
        "✅ СООБЩЕНИЕ ПЕРЕДАНО КОМАНДЕ\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Команда получила ваш сигнал о готовности доверенности. Юридический статус дела этим нажатием не изменён.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Если нужно передать скан или подтверждающий файл, загрузите доверенность отдельной кнопкой ниже. Фактическое получение и проверку документа отдельно фиксирует юрист.",
        case_id=case_id,
    )


__all__ = ["router"]
