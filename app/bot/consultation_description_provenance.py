from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_intake import ConsultationIntakeService
from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

_DESCRIPTION_ENTRY_CALLBACKS = {
    "consult_subject_start",
    "consult_description_start",
    "consult_question_start",
}


async def _current_context(event, db):
    ctx = BotContextService(db)
    if isinstance(event, CallbackQuery):
        user = await ctx.get_user_from_callback(event)
    else:
        user = await ctx.get_user_from_message(event)
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


async def _recover(event, state, text: str) -> None:
    if state is not None:
        try:
            await state.clear()
        except Exception:
            logger.warning("Не удалось очистить FSM после смены консультации")
    markup = one(
        ("📝 Открыть актуальный вопрос", "consult_subject_start"),
        ("📁 Моё дело", "my_case_open"),
        ("✉️ Написать команде", "message_create"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=markup)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    await event.message.answer(text, reply_markup=markup)
            try:
                await event.answer("Старый экран не изменил консультацию.")
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
        else:
            await event.answer(text, reply_markup=markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не показал recovery ввода вопроса")


class ConsultationDescriptionProvenanceMiddleware:
    """Bind M2 description input to its exact Case and Consultation.

    If no Case is selected, the first explicit Telegram entry click is also the
    source-idempotent creation operation for a new M2 matter. If another Case is
    already selected, this middleware never creates a parallel matter on its
    own: the canonical route policy decides whether that selected Case may enter
    or resume M2.
    """

    async def __call__(self, handler, event, data):
        state = data.get("state")
        db = data.get("db")

        if isinstance(event, CallbackQuery) and str(event.data or "") in _DESCRIPTION_ENTRY_CALLBACKS:
            if db is not None:
                try:
                    ctx = BotContextService(db)
                    user = await ctx.get_user_from_callback(event)
                    selected = await ctx.case_service.get_active_case_for_user(user.id)
                    if selected is None:
                        # A duplicate delivery of this exact callback uses the
                        # same operation key and resolves to the same Case.
                        await ConsultationIntakeService(db).get_or_create_context(
                            user,
                            operation_key=f"telegram_callback:{event.id}",
                        )
                except Exception:
                    # Keep creation and the underlying screen in one transaction.
                    # The handler/recovery path will roll back; never leave a
                    # half-created M2 Case solely because provenance ran first.
                    logger.exception("Не удалось подготовить идемпотентный M2 context")
                    await db.rollback()
                    await _recover(
                        event,
                        state,
                        "Не удалось безопасно открыть консультационное обращение. Новое дело не создано; повторите действие.",
                    )
                    return None

            result = await handler(event, data)
            if db is None or state is None:
                return result
            try:
                _user, case, consultation = await _current_context(event, db)
                if case is None or consultation is None:
                    # The route handler may have refused M2 because the selected
                    # matter belongs to another route. Never attach fake provenance.
                    return result
                await state.update_data(
                    consult_description_case_id=int(case.id),
                    consult_description_id=int(consultation.id),
                )
            except Exception:
                logger.exception("Не удалось зафиксировать provenance ввода вопроса")
                await db.rollback()
                await _recover(
                    event,
                    state,
                    "Форма вопроса не была привязана к обращению. Для безопасности откройте её заново.",
                )
                return None
            return result

        if not isinstance(event, Message) or state is None:
            return await handler(event, data)

        try:
            snapshot = await state.get_data()
        except Exception:
            return await handler(event, data)

        raw_case_id = snapshot.get("consult_description_case_id")
        raw_consultation_id = snapshot.get("consult_description_id")
        if raw_case_id in (None, "") and raw_consultation_id in (None, ""):
            return await handler(event, data)

        if db is None:
            await _recover(
                event,
                state,
                "Не удалось подтвердить, к какому обращению относится этот текст. Сообщение не сохранено как вопрос консультации.",
            )
            return None

        try:
            expected_case_id = int(raw_case_id or 0)
            expected_consultation_id = int(raw_consultation_id or 0)
            _user, case, consultation = await _current_context(event, db)
        except Exception:
            logger.exception("Не удалось проверить provenance текста консультации")
            await db.rollback()
            await _recover(
                event,
                state,
                "Не удалось безопасно проверить консультацию. Текст не сохранён как вопрос.",
            )
            return None

        if (
            expected_case_id <= 0
            or expected_consultation_id <= 0
            or case is None
            or consultation is None
            or int(case.id) != expected_case_id
            or int(consultation.id) != expected_consultation_id
        ):
            await db.rollback()
            await _recover(
                event,
                state,
                "ℹ️ Пока вы вводили вопрос, выбранное консультационное обращение изменилось. Текст не был записан в другое дело. Откройте актуальную форму и отправьте вопрос туда.",
            )
            return None

        result = await handler(event, data)
        try:
            current_state = await state.get_state()
            if current_state is not None:
                await state.update_data(
                    consult_description_case_id=None,
                    consult_description_id=None,
                )
        except Exception:
            logger.warning("Не удалось очистить использованный provenance вопроса")
        return result


__all__ = ["ConsultationDescriptionProvenanceMiddleware"]
