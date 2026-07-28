from __future__ import annotations

from datetime import datetime
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.consultation_booking_callbacks import ConsultationBookingCallbacks
from app.bot.consultation_booking_renderer import (
    render_date_slots,
    render_dates,
    render_empty,
    render_lawyer_card,
    render_lawyers,
    render_nearest,
    render_selection_modes,
)
from app.bot.context import BotContextService
from app.domain.consultations.slot_selection_service import (
    ConsultationSlotSelectionError,
    ConsultationSlotSelectionService,
)
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation


router = Router()
logger = logging.getLogger(__name__)


async def _load_selection(
    callback: CallbackQuery,
    db,
    *,
    lawyer_reference: int | None = None,
):
    # Expired holds must not hide slots until the next scheduler tick. Reuse
    # the canonical idempotent cleanup before every server-side refresh.
    await SlotService(db).release_expired_holds()

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ConsultationSlotSelectionError(
            "Выбор времени для клиента недоступен."
        )
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if (
        case is None
        or case.client_id != user.id
        or case.route != RouteCode.M2.value
        or case.status != CaseStatus.M2_SLOT_PENDING.value
    ):
        raise ConsultationSlotSelectionError(
            "Консультация недоступна для выбора времени."
        )
    consultations = list(
        (
            await db.execute(
                select(Consultation)
                .where(
                    Consultation.case_id == case.id,
                    Consultation.status == ConsultationStatus.SLOT_PENDING.value,
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if len(consultations) != 1:
        raise ConsultationSlotSelectionError(
            "Для дела не определена единственная активная консультация."
        )
    consultation = consultations[0]
    selection = await ConsultationSlotSelectionService(db).build_selection(
        client_id=user.id,
        case_id=case.id,
        consultation_id=consultation.id,
        lawyer_reference=lawyer_reference,
    )
    return selection


async def _show_error(callback: CallbackQuery, db, message: str) -> None:
    await db.rollback()
    text, markup = render_empty(message)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data in {"consult_slot_open", "consult_booking_start"}
)
async def open_selection_modes(callback: CallbackQuery, db):
    try:
        selection = await _load_selection(callback, db)
        await db.commit()
    except ConsultationSlotSelectionError:
        await _show_error(
            callback,
            db,
            "Выбор времени сейчас недоступен. Откройте актуальный шаг в «Моём деле».",
        )
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation selection modes")
        await _show_error(
            callback,
            db,
            "Не удалось обновить расписание. Ваши данные не потеряны.",
        )
        return

    text, markup = (
        render_selection_modes(selection)
        if selection.slots
        else render_empty("Сейчас нет доступного времени.")
    )
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data.startswith(
        f"{ConsultationBookingCallbacks.PREFIX}:mode:"
    )
)
async def choose_mode(callback: CallbackQuery, db):
    mode = callback.data.rsplit(":", 1)[-1]
    try:
        selection = await _load_selection(callback, db)
        await db.commit()
    except ConsultationSlotSelectionError:
        await _show_error(callback, db, "Расписание больше недоступно.")
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation selection mode")
        await _show_error(callback, db, "Не удалось обновить расписание.")
        return

    if mode == "nearest":
        text, markup = render_nearest(selection.slots)
    elif mode == "lawyer":
        text, markup = render_lawyers(selection.lawyers)
    elif mode == "assigned":
        lawyer = next(
            (
                item
                for item in selection.lawyers
                if item.reference == selection.assigned_lawyer_reference
            ),
            None,
        )
        if lawyer is None:
            text, markup = render_empty(
                "У назначенного юриста пока нет доступного времени."
            )
        else:
            text, markup = render_lawyer_card(lawyer)
    else:
        await callback.answer(
            "Этот вариант выбора устарел. Откройте расписание заново.",
            show_alert=True,
        )
        text, markup = render_selection_modes(selection)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data.startswith(
        f"{ConsultationBookingCallbacks.PREFIX}:lawyer:"
    )
)
async def choose_lawyer(callback: CallbackQuery, db):
    try:
        lawyer_reference = int(callback.data.rsplit(":", 1)[-1])
        if lawyer_reference <= 0:
            raise ValueError
        selection = await _load_selection(
            callback,
            db,
            lawyer_reference=lawyer_reference,
        )
        lawyer = next(
            item
            for item in selection.lawyers
            if item.reference == lawyer_reference
        )
        await db.commit()
    except (
        ConsultationSlotSelectionError,
        StopIteration,
        TypeError,
        ValueError,
    ):
        await _show_error(callback, db, "Выбранный юрист сейчас недоступен.")
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation lawyer card")
        await _show_error(callback, db, "Не удалось открыть карточку юриста.")
        return
    text, markup = render_lawyer_card(lawyer)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data.startswith(
        f"{ConsultationBookingCallbacks.PREFIX}:lawyer_dates:"
    )
)
async def choose_lawyer_dates(callback: CallbackQuery, db):
    try:
        lawyer_reference = int(callback.data.rsplit(":", 1)[-1])
        if lawyer_reference <= 0:
            raise ValueError
        selection = await _load_selection(
            callback,
            db,
            lawyer_reference=lawyer_reference,
        )
        await db.commit()
    except (ConsultationSlotSelectionError, TypeError, ValueError):
        await _show_error(callback, db, "Время этого юриста сейчас недоступно.")
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation lawyer dates")
        await _show_error(callback, db, "Не удалось открыть даты юриста.")
        return
    text, markup = render_dates(
        selection.dates,
        page=0,
        lawyer_reference=lawyer_reference,
    )
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data.startswith(
        f"{ConsultationBookingCallbacks.PREFIX}:dates:"
    )
)
async def choose_dates(callback: CallbackQuery, db):
    try:
        _, _, raw_page, raw_lawyer = callback.data.split(":", 3)
        page = max(0, min(int(raw_page), 100))
        lawyer_reference = int(raw_lawyer) or None
        selection = await _load_selection(
            callback,
            db,
            lawyer_reference=lawyer_reference,
        )
        await db.commit()
    except (ConsultationSlotSelectionError, TypeError, ValueError):
        await _show_error(callback, db, "Выбранные даты сейчас недоступны.")
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation dates")
        await _show_error(callback, db, "Не удалось открыть даты.")
        return
    text, markup = render_dates(
        selection.dates,
        page=page,
        lawyer_reference=lawyer_reference,
    )
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(
    lambda callback: callback.data.startswith(
        f"{ConsultationBookingCallbacks.PREFIX}:date:"
    )
)
async def choose_date(callback: CallbackQuery, db):
    try:
        _, _, raw_date, raw_lawyer = callback.data.split(":", 3)
        selected_date = datetime.strptime(raw_date, "%Y%m%d").date()
        lawyer_reference = int(raw_lawyer) or None
        selection = await _load_selection(
            callback,
            db,
            lawyer_reference=lawyer_reference,
        )
        if selected_date not in {item.value for item in selection.dates}:
            raise ConsultationSlotSelectionError(
                "Выбранная дата больше недоступна."
            )
        await db.commit()
    except (ConsultationSlotSelectionError, TypeError, ValueError):
        await _show_error(callback, db, "Выбранная дата больше недоступна.")
        return
    except Exception:
        logger.exception("Unexpected error while opening consultation date slots")
        await _show_error(callback, db, "Не удалось открыть свободное время.")
        return
    text, markup = render_date_slots(
        selected_date,
        selection.slots,
        lawyer_reference=lawyer_reference,
    )
    await callback.message.edit_text(text, reply_markup=markup)
