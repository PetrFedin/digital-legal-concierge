from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_history import add_case_history_event
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.statuses.case_statuses import CaseStatus
from app.models.audit_log import AuditLog

router = Router()
logger = logging.getLogger(__name__)
_ACTION = "CLIENT_POA_READY_REPORTED"


async def _show(callback: CallbackQuery, text: str) -> None:
    markup = one(
        ("📄 Приложить документ", "documents_open"),
        ("📁 Моё дело", "my_case_open"),
        ("✉️ Задать вопрос команде", "message_create"),
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


@router.callback_query(lambda c: c.data == "poa_done")
async def report_poa_ready(callback: CallbackQuery, db):
    """Notify staff without allowing a client click to prove legal document receipt."""

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await _show(
            callback,
            "Активное дело не найдено. Никакой юридический статус не изменён. "
            "Откройте «Моё дело», чтобы увидеть актуальное состояние.",
        )
        return

    status = CaseStatus(str(case.status))
    if status != CaseStatus.M1_POWER_OF_ATTORNEY:
        await _show(
            callback,
            "Этап доверенности уже изменился. Повторное подтверждение не требуется — "
            "откройте актуальное состояние дела.",
        )
        return

    existing = (
        await db.execute(
            select(AuditLog.id)
            .where(AuditLog.entity_type == "case")
            .where(AuditLog.entity_id == case.id)
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
                case_id=case.id,
                action=_ACTION,
                new_value={"status": str(case.status), "client_reported_ready": True},
                comment=(
                    "Клиент сообщил, что доверенность оформлена/готова к передаче. "
                    "Фактическое получение должен отдельно подтвердить назначенный юрист."
                ),
            )
            await NotificationEngine(db).emit(
                event_code="M1_POA_READY_REPORTED",
                case_id=case.id,
                payload={"case_number": case.case_number},
                dedupe_key=f"case:{case.id}:poa-ready-reported",
            )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить сигнал клиента о готовности доверенности")
        await _show(
            callback,
            "Не удалось передать сообщение команде. Статус дела не изменён. "
            "Повторите действие позже или задайте вопрос команде.",
        )
        return

    await _show(
        callback,
        "✅ Команда получила ваше сообщение о доверенности.\n\n"
        "Важно: это не подтверждает получение документа юристом и не запускает претензию. "
        "Статус изменится только после фактической проверки/получения доверенности назначенным юристом.\n\n"
        "Если нужно передать скан или подтверждающий файл, приложите его через «Документы».",
    )
