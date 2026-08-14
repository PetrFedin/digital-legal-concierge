from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

_BOOKING_ENTRY_CALLBACKS = {
    "consult_booking_start",
    "consult_slot_open",
}
_BOUND_OTHER_FLOWS = (
    "consult_reschedule",
    "consult_cancel",
)
_PROVENANCE_KEYS = (
    "consult_booking_case_id",
    "consult_booking_id",
    "consult_booking_message_id",
)


def _looks_like_initial_booking_callback(value: str) -> bool:
    data = str(value or "")
    if not data or data in _BOOKING_ENTRY_CALLBACKS:
        return False
    if data.startswith(_BOUND_OTHER_FLOWS):
        return False
    if not data.startswith("consult_"):
        return False
    lowered = data.lower()
    return any(token in lowered for token in ("slot", "day", "time"))


def _looks_like_slot_choice(value: str) -> bool:
    data = str(value or "").lower()
    return "slot" in data and any(char.isdigit() for char in data)


async def _current_context(event: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(event)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or str(case.route or "").upper() != "M2":
        return user, None, None
    consultation = (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == int(case.id))
            .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            .limit(1)
        )
    ).scalars().first()
    return user, case, consultation


async def _recover(event: CallbackQuery, state, text: str) -> None:
    if state is not None:
        try:
            await state.update_data(
                consult_booking_case_id=None,
                consult_booking_id=None,
                consult_booking_message_id=None,
            )
        except Exception:
            logger.warning("Не удалось очистить provenance выбора консультации")
    markup = one(
        ("📅 Открыть актуальный выбор времени", "consult_booking_start"),
        ("📁 Моё дело", "my_case_open"),
        ("✉️ Написать команде", "message_create"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        await event.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            try:
                await event.message.answer(text, reply_markup=markup)
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                logger.warning("Не удалось показать recovery выбора времени")
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не показал recovery выбора времени")
    try:
        await event.answer("Старый календарь не изменил запись.")
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        pass


class ConsultationBookingProvenanceMiddleware:
    """Keep historical raw slot keyboards from acting on a newer M2 case.

    The existing cancellation/reschedule flows already carry consultation/slot
    identifiers and are excluded. Initial calendar screens historically use raw
    slot/day callbacks, so the middleware binds them to the current M2 case,
    latest consultation and the Telegram message that rendered the calendar.
    """

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        value = str(event.data or "")
        state = data.get("state")
        db = data.get("db")

        if value in _BOOKING_ENTRY_CALLBACKS:
            result = await handler(event, data)
            if db is None or state is None:
                return result
            try:
                _user, case, consultation = await _current_context(event, db)
                if case is not None and consultation is not None:
                    await state.update_data(
                        consult_booking_case_id=int(case.id),
                        consult_booking_id=int(consultation.id),
                        consult_booking_message_id=int(event.message.message_id),
                    )
            except Exception:
                logger.exception("Не удалось привязать экран выбора времени к M2-контексту")
            return result

        if not _looks_like_initial_booking_callback(value):
            return await handler(event, data)

        if db is None or state is None:
            await _recover(
                event,
                state,
                "Не удалось подтвердить, к какому обращению относится этот календарь. Время не выбиралось.",
            )
            return None

        try:
            snapshot = await state.get_data()
            expected_case_id = int(snapshot.get("consult_booking_case_id") or 0)
            expected_consultation_id = int(snapshot.get("consult_booking_id") or 0)
            expected_message_id = int(snapshot.get("consult_booking_message_id") or 0)
            _user, case, consultation = await _current_context(event, db)
        except Exception:
            logger.exception("Не удалось проверить provenance выбора времени")
            await db.rollback()
            await _recover(
                event,
                state,
                "Не удалось безопасно проверить этот календарь. Время не выбиралось.",
            )
            return None

        current_message_id = int(event.message.message_id)
        if (
            expected_case_id <= 0
            or expected_consultation_id <= 0
            or expected_message_id <= 0
            or case is None
            or consultation is None
            or int(case.id) != expected_case_id
            or int(consultation.id) != expected_consultation_id
            or current_message_id != expected_message_id
        ):
            await db.rollback()
            await _recover(
                event,
                state,
                "ℹ️ Этот календарь относится к более раннему экрану или другому обращению. Старый слот не бронировался. Откройте актуальный выбор времени.",
            )
            return None

        result = await handler(event, data)
        if _looks_like_slot_choice(value):
            # A slot click is a one-shot mutation boundary. The underlying slot
            # service still performs its own availability/hold checks; clearing
            # this snapshot prevents a rapid second click from reusing the same
            # historical calendar after the first business action completed.
            try:
                await state.update_data(
                    consult_booking_case_id=None,
                    consult_booking_id=None,
                    consult_booking_message_id=None,
                )
            except Exception:
                logger.warning("Не удалось очистить использованный slot provenance")
        return result


__all__ = ["ConsultationBookingProvenanceMiddleware"]
