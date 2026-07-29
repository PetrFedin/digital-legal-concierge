from __future__ import annotations

from datetime import datetime
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.cancellation_request_service import (
    ConsultationCancellationRequestService,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
)
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


router = Router()
logger = logging.getLogger(__name__)


def _format_date(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y") if value is not None else "уточняется"


def _format_time(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value is not None else "уточняется"


async def _load_paid_consultation(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ConsultationNotFoundError("Доступ клиента ограничен.")

    cases = await ctx.case_service.list_active_cases_for_user(
        user.id,
        route=RouteCode.M2,
    )
    if len(cases) != 1:
        raise ActiveConsultationConflictError(
            "Не удалось однозначно определить консультационное дело."
        )
    case = cases[0]
    consultation = (
        await db.execute(
            select(Consultation)
            .options(
                joinedload(Consultation.lawyer),
                joinedload(Consultation.slot).joinedload(ConsultationSlot.lawyer),
            )
            .where(
                Consultation.case_id == case.id,
                Consultation.status.in_(
                    ConsultationCancellationRequestService.ALLOWED_STATUSES
                ),
            )
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
        )
    ).unique().scalars().all()
    if len(consultation) != 1:
        raise ActiveConsultationConflictError(
            "Для дела не определена единственная оплаченная консультация."
        )
    return user, case, consultation[0]


async def _show_error(callback: CallbackQuery, text: str) -> None:
    await callback.message.edit_text(
        f"⚠️ {text}",
        reply_markup=one(
            ("📋 Детали консультации", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel")
async def request_cancel_confirmation(callback: CallbackQuery, db):
    try:
        _, _, consultation = await _load_paid_consultation(callback, db)
        slot = consultation.slot
        if slot is None or slot.status != "booked":
            raise ActiveConsultationConflictError(
                "Подтверждённое время консультации не найдено."
            )
        lawyer = consultation.lawyer or slot.lawyer
        lawyer_name = lawyer.full_name if lawyer is not None else "уточняется"
        await db.commit()
    except (ConsultationNotFoundError, ActiveConsultationConflictError) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening cancellation request")
        await _show_error(callback, "Не удалось открыть запрос отмены.")
        return

    await callback.message.edit_text(
        "Запросить отмену консультации?\n\n"
        f"Дата: {_format_date(slot.starts_at)}\n"
        f"Время: {_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}\n"
        f"Юрист: {lawyer_name}\n\n"
        "Консультация оплачена. Поэтому запись не будет освобождена "
        "автоматически: менеджер сначала проверит условия отмены и возможного "
        "возврата. До решения текущее время сохраняется за вами.",
        reply_markup=one(
            ("✅ Отправить запрос", "consult_cancel_confirm"),
            ("⬅ Оставить запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel_confirm")
async def confirm_cancellation_request(callback: CallbackQuery, db):
    try:
        user, case, consultation = await _load_paid_consultation(callback, db)
        await ConsultationCancellationRequestService(db).request(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_id=user.id,
            source="telegram",
            reason="Клиент подтвердил запрос отмены в Telegram",
        )
        await db.commit()
    except (ConsultationNotFoundError, ActiveConsultationConflictError) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while recording cancellation request")
        await _show_error(
            callback,
            "Не удалось сохранить запрос. Текущая запись не изменена.",
        )
        return

    await callback.message.edit_text(
        "✅ Запрос отмены передан менеджеру\n\n"
        "Текущее время и оплата сохранены до проверки условий отмены и "
        "возможного возврата. Повторно отправлять запрос не нужно.",
        reply_markup=one(
            ("📋 Текущая запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )
