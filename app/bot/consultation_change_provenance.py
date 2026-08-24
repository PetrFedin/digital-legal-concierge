from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import callback_matches_action, resolve_case_callback_scope
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.consultation_statuses import ConsultationStatus

logger = logging.getLogger(__name__)

_RESCHEDULE_ENTRY = "consult_reschedule"
_RESCHEDULE_PREFIXES = (
    "consult_reschedule_date:",
    "consult_reschedule_slot:",
)
_RESCHEDULE_CONFIRM_PREFIX = "consult_reschedule_confirm:"
_PROVENANCE_KEYS = (
    "consult_reschedule_case_id",
    "consult_reschedule_id",
    "consult_reschedule_message_id",
)


def _parse_confirmation(value: str) -> tuple[int, int, int] | None:
    if not value.startswith(_RESCHEDULE_CONFIRM_PREFIX):
        return None
    try:
        consultation_raw, old_slot_raw, new_slot_raw = value[
            len(_RESCHEDULE_CONFIRM_PREFIX) :
        ].split(":", 2)
        consultation_id = int(consultation_raw)
        old_slot_id = int(old_slot_raw)
        new_slot_id = int(new_slot_raw)
    except (TypeError, ValueError):
        return None
    if consultation_id <= 0 or old_slot_id < 0 or new_slot_id <= 0:
        return None
    return consultation_id, old_slot_id, new_slot_id


async def _current_booked_context(event: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(event)
    case = await ctx.case_service.get_active_case_for_user(int(user.id))
    if case is None or str(case.route or "").upper() != "M2":
        return user, None, None
    consultation = await ConsultationService(db).get_current_for_case(int(case.id))
    if consultation is None or str(consultation.status) != ConsultationStatus.BOOKED.value:
        return user, case, None
    return user, case, consultation


async def _clear(state) -> None:
    if state is None:
        return
    try:
        await state.update_data(
            consult_reschedule_case_id=None,
            consult_reschedule_id=None,
            consult_reschedule_message_id=None,
        )
    except Exception:
        logger.warning("Не удалось очистить provenance переноса консультации")


async def _store_current(event: CallbackQuery, state, db) -> bool:
    if state is None or db is None:
        return False
    try:
        _user, case, consultation = await _current_booked_context(event, db)
        if case is None or consultation is None:
            return False
        await state.update_data(
            consult_reschedule_case_id=int(case.id),
            consult_reschedule_id=int(consultation.id),
            consult_reschedule_message_id=int(event.message.message_id),
        )
        return True
    except Exception:
        logger.warning("Не удалось сохранить provenance переноса", exc_info=True)
        return False


async def _recover(event: CallbackQuery, state, text: str) -> None:
    await _clear(state)
    markup = one(
        ("👨‍⚖ Открыть текущую запись", "consultation_booked_open"),
        ("📁 Выбрать обращение", "my_cases_open"),
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
                logger.warning("Не удалось показать recovery переноса консультации")
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не показал recovery переноса консультации")
    try:
        await event.answer("Старый экран переноса ничего не изменил.")
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        pass


class ConsultationChangeProvenanceMiddleware:
    """Bind the multi-step reschedule calendar to one Case/Consultation/message.

    The final reschedule confirmation already carries the exact consultation,
    old slot and new slot, but historical intermediate date/slot callbacks are
    raw. Without provenance, an old Case A calendar pressed after switching to
    Case B could be reinterpreted as a new reschedule draft for B. This
    middleware prevents that cross-Case UX leak while retaining the existing
    domain confirmation contract.
    """

    async def __call__(self, handler, event, data):
        if not isinstance(event, CallbackQuery):
            return await handler(event, data)

        value = str(event.data or "")
        state = data.get("state")
        db = data.get("db")

        if callback_matches_action(value, _RESCHEDULE_ENTRY):
            if db is None:
                await _recover(
                    event,
                    state,
                    "Не удалось подтвердить обращение для переноса. Текущая запись не изменена.",
                )
                return None
            try:
                scope = await resolve_case_callback_scope(
                    event,
                    db,
                    action=_RESCHEDULE_ENTRY,
                    allow_legacy_message_case_context=True,
                )
            except Exception:
                logger.exception("Не удалось проверить Case-контекст переноса")
                await db.rollback()
                await _recover(
                    event,
                    state,
                    "Не удалось безопасно проверить обращение для переноса. Текущая запись не изменена.",
                )
                return None
            if scope is None:
                await _clear(state)
                return None

            result = await handler(event, data)
            if not await _store_current(event, state, db):
                await _clear(state)
            return result

        if value.startswith(_RESCHEDULE_CONFIRM_PREFIX):
            expected = _parse_confirmation(value)
            result = await handler(event, data)
            if expected is None or db is None or state is None:
                await _clear(state)
                return result

            expected_consultation_id, expected_old_slot_id, _new_slot_id = expected
            try:
                _user, case, consultation = await _current_booked_context(event, db)
                retry_calendar_rendered = bool(
                    case is not None
                    and consultation is not None
                    and int(consultation.id) == expected_consultation_id
                    and int(consultation.slot_id or 0) == expected_old_slot_id
                )
            except Exception:
                retry_calendar_rendered = False

            if retry_calendar_rendered:
                # The domain action can lose a race for the requested new slot
                # and render a fresh reschedule calendar in the same Telegram
                # message. Keep provenance for that new calendar. A successful
                # reschedule changes slot_id, so the old calendar is retired.
                await _store_current(event, state, db)
            else:
                await _clear(state)
            return result

        if not value.startswith(_RESCHEDULE_PREFIXES):
            return await handler(event, data)

        if db is None or state is None:
            await _recover(
                event,
                state,
                "Не удалось подтвердить, к какой консультации относится этот экран переноса. Ничего не изменено.",
            )
            return None

        try:
            snapshot = await state.get_data()
            expected_case_id = int(snapshot.get("consult_reschedule_case_id") or 0)
            expected_consultation_id = int(snapshot.get("consult_reschedule_id") or 0)
            expected_message_id = int(snapshot.get("consult_reschedule_message_id") or 0)
            _user, case, consultation = await _current_booked_context(event, db)
        except Exception:
            logger.exception("Не удалось проверить provenance переноса")
            await db.rollback()
            await _recover(
                event,
                state,
                "Не удалось безопасно проверить этот экран переноса. Текущая запись не изменена.",
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
                "ℹ️ Этот экран переноса относится к более ранней записи или другому обращению. Старый слот не менялся. Откройте текущую консультацию и начните перенос заново.",
            )
            return None

        return await handler(event, data)


__all__ = ["ConsultationChangeProvenanceMiddleware"]
