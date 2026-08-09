from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.documents.document_workflow import normalize_document_status
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.document import Document

router = Router()
logger = logging.getLogger(__name__)


CONSULTATION_STATUS_LABELS = {
    ConsultationStatus.DESCRIPTION_PENDING: "Нужно описать вопрос",
    ConsultationStatus.DOCUMENTS_OPTIONAL: "Можно добавить документы",
    ConsultationStatus.SLOT_PENDING: "Нужно выбрать время",
    ConsultationStatus.SLOT_RESERVED: "Время временно зарезервировано",
    ConsultationStatus.PAYMENT_PENDING: "Ожидается подтверждение записи",
    ConsultationStatus.BOOKED: "Консультация подтверждена",
    ConsultationStatus.DONE: "Консультация проведена",
    ConsultationStatus.CLIENT_NO_SHOW: "Клиент не подключился",
    ConsultationStatus.LAWYER_NO_SHOW: "Юрист не подключился",
    ConsultationStatus.CANCELLED: "Консультация отменена",
    ConsultationStatus.RESCHEDULED: "Консультация перенесена",
    ConsultationStatus.CLOSED: "Консультация закрыта",
}

TERMINAL_STATUSES = {
    ConsultationStatus.DONE,
    ConsultationStatus.CLIENT_NO_SHOW,
    ConsultationStatus.LAWYER_NO_SHOW,
    ConsultationStatus.CANCELLED,
    ConsultationStatus.RESCHEDULED,
    ConsultationStatus.CLOSED,
}


def normalized_consultation_status(value) -> ConsultationStatus | None:
    try:
        return ConsultationStatus(str(value))
    except (TypeError, ValueError):
        return None


def consultation_status_label(value) -> str:
    status = normalized_consultation_status(value)
    if status is None:
        return "Статус уточняется"
    return CONSULTATION_STATUS_LABELS.get(status, "Статус уточняется")


def consultation_primary_action(
    *,
    status: ConsultationStatus | None,
    description_ready: bool,
    active_document_count: int,
) -> tuple[tuple[str, str], str]:
    if not description_ready:
        return (
            ("▶️ Описать вопрос", "consult_subject_start"),
            "Опишите вопрос и подтвердите текст. После этого станет доступен выбор времени.",
        )

    if status in TERMINAL_STATUSES:
        return (
            ("👨‍⚖ Открыть итог консультации", "consultation_result_open"),
            "Консультация завершена. Откройте итог юриста и актуальное продолжение.",
        )

    if status == ConsultationStatus.PAYMENT_PENDING:
        if payments_disabled():
            return (
                ("▶️ Завершить подтверждение", "consult_pay"),
                "Подтвердите выбранное время. Онлайн-оплата для этого маршрута сейчас не требуется.",
            )
        return (
            ("💳 Оплатить и подтвердить", "consult_pay"),
            "Подтвердите выбранное время оплатой до окончания резерва.",
        )

    if status == ConsultationStatus.BOOKED:
        if active_document_count:
            return (
                ("📄 Проверить документы", "documents_open"),
                "Консультация назначена. Проверьте материалы и дождитесь времени встречи.",
            )
        return (
            ("📄 Добавить документы", "documents_open"),
            "Консультация назначена. Если есть договоры, переписка или другие материалы — добавьте их до встречи.",
        )

    return (
        ("▶️ Выбрать дату и время", "consult_booking_start"),
        "Вопрос сохранён. Выберите свободную дату и время консультации.",
    )


def _append_unique(buttons: list[tuple[str, str]], button: tuple[str, str]) -> None:
    if all(existing[1] != button[1] for existing in buttons):
        buttons.append(button)


def _format_datetime(value) -> str:
    if not value:
        return "ещё не выбраны"
    try:
        return value.strftime("%d.%m.%Y %H:%M")
    except (AttributeError, ValueError):
        return "уточняются"


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            try:
                await callback.answer("Экран уже актуален.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
            return
        logger.warning("Не удалось обновить action-center консультации: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram временно не обновил action-center консультации: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.exception("Не удалось показать action-center консультации")
        try:
            await callback.answer(
                "Не удалось обновить экран. Откройте «Моё дело» и повторите действие.",
                show_alert=True,
            )
        except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
            pass


@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def consultation_action_center(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)

    if not case:
        await db.commit()
        await _safe_edit(
            callback,
            "👨‍⚖ КОНСУЛЬТАЦИЯ\n\n"
            "Активная консультация не найдена. Начните с описания ситуации или вернитесь на главную.",
            reply_markup=one(
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(case.route or "") != RouteCode.M2.value:
        await db.commit()
        await _safe_edit(
            callback,
            "👨‍⚖ КОНСУЛЬТАЦИЯ\n\n"
            "У вас уже есть активное дело по другому маршруту. Отдельную M2-консультацию сейчас не создаю, чтобы не разделять историю и документы.\n\n"
            "Продолжите текущее дело или напишите юридической команде.",
            reply_markup=one(
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await db.commit()
        await _safe_edit(
            callback,
            "👨‍⚖ КОНСУЛЬТАЦИЯ\n\n"
            "Текущая запись не найдена. Сохранённое дело остаётся доступно — откройте актуальный следующий шаг или начните консультацию заново.",
            reply_markup=one(
                ("📝 Начать консультацию", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    documents = list(
        (
            await db.execute(
                select(Document)
                .where(Document.case_id == case.id)
                .order_by(Document.created_at.desc(), Document.id.desc())
            )
        ).scalars().all()
    )
    active_documents = [
        item for item in documents if normalize_document_status(item.status) != "ARCHIVED"
    ]
    await db.commit()

    description_ready = consultation_description_ready(consultation)
    status = normalized_consultation_status(consultation.status)
    primary, next_step = consultation_primary_action(
        status=status,
        description_ready=description_ready,
        active_document_count=len(active_documents),
    )

    buttons: list[tuple[str, str]] = [primary]
    if description_ready:
        _append_unique(buttons, ("📝 Изменить вопрос", "consult_subject_start"))
    _append_unique(buttons, ("📄 Документы", "documents_open"))
    _append_unique(buttons, ("✉️ Задать вопрос команде", "message_create"))
    if status == ConsultationStatus.BOOKED:
        _append_unique(buttons, ("🔄 Перенести консультацию", "consult_reschedule"))
        _append_unique(buttons, ("Отменить консультацию", "consult_cancel"))
    _append_unique(buttons, ("📁 Моё дело", "my_case_open"))
    _append_unique(buttons, ("🏠 Главная", "nav_home"))

    document_summary = (
        f"добавлено {len(active_documents)}"
        if active_documents
        else "не добавлены · необязательно"
    )
    confirmation_summary = (
        "онлайн-оплата не требуется"
        if payments_disabled()
        else (
            "подтверждена"
            if status == ConsultationStatus.BOOKED
            else "зависит от текущего этапа"
        )
    )

    await _safe_edit(
        callback,
        "👨‍⚖ КОНСУЛЬТАЦИЯ\n\n"
        "СЕЙЧАС\n"
        f"{consultation_status_label(consultation.status)}\n"
        f"Дата и время: {_format_datetime(consultation.scheduled_at)}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        f"{next_step}\n\n"
        "ПОДГОТОВКА\n"
        f"📝 Вопрос: {'сохранён' if description_ready else 'нужно описать'}\n"
        f"📄 Документы: {document_summary}\n"
        f"💳 Подтверждение: {confirmation_summary}\n\n"
        "Первая кнопка ниже — самое актуальное безопасное действие. Перенос и отмена доступны отдельно и не меняют запись без подтверждения.",
        reply_markup=one(*buttons),
    )
