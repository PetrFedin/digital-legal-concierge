from __future__ import annotations

import logging

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.keyboards import one
from app.bot.screens.consultation_reservation import (
    ReservationScreenError,
    _load_context,
    open_reserved_slot,
)
from app.domain.consultations.booking_service import ConsultationBookingService
from app.domain.consultations.client_schedule_service import (
    ClientConsultationConflictError,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.consultation_statuses import ConsultationStatus


router = Router()
logger = logging.getLogger(__name__)


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def reserve_selected_slot(callback: CallbackQuery, db):
    try:
        slot_id = int(callback.data.split(":", 1)[1])
        if slot_id <= 0:
            raise ValueError

        await SlotService(db).release_expired_holds()
        user, case, consultation = await _load_context(callback, db)
        status = ConsultationStatus(consultation.status)
        if status not in {
            ConsultationStatus.SLOT_PENDING,
            ConsultationStatus.SLOT_RESERVED,
        }:
            raise ActiveConsultationConflictError(
                "Текущий этап консультации не допускает выбор времени."
            )

        await ConsultationBookingService(db).reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
            actor_type="client",
            source="telegram",
        )
        await db.commit()
    except (TypeError, ValueError):
        await db.rollback()
        await callback.answer(
            "Некорректный вариант времени. Откройте расписание заново.",
            show_alert=True,
        )
        return
    except (
        ReservationScreenError,
        ConsultationNotFoundError,
        ConsultationSlotError,
        ActiveConsultationConflictError,
        ClientConsultationConflictError,
    ) as exc:
        await db.rollback()
        await callback.message.edit_text(
            f"⚠️ {str(exc)}\n\nВыберите другой свободный вариант.",
            reply_markup=one(
                ("📅 Обновить расписание", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while reserving consultation slot")
        await callback.message.edit_text(
            "Не удалось удержать выбранное время. Данные не потеряны.",
            reply_markup=one(
                ("📅 Выбрать другое время", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    # Render from freshly committed database state. The card will re-check the
    # hold owner, expiry and payment protection before offering any action.
    await open_reserved_slot(callback, db)
