from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.case_callback_scope import callback_matches_action, resolve_case_callback_scope
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_intake import ConsultationIntakeService
from app.models.consultation import Consultation

logger = logging.getLogger(__name__)

_DESCRIPTION_ENTRY_CALLBACKS = (
    "consult_subject_start",
    "consult_description_start",
    "consult_question_start",
)
_DESCRIPTION_FLOW_CALLBACKS = frozenset(
    {
        "consult_subject_new",
        "consult_description_review",
        "consult_description_edit",
        "consult_description_discard_confirm",
        "consult_description_discard",
        "consult_description_confirm",
    }
)
_DESCRIPTION_FLOW_PREFIXES = ("consult_subject_case:",)


def _description_entry_action(value: str | None) -> str | None:
    for action in _DESCRIPTION_ENTRY_CALLBACKS:
        if callback_matches_action(value, action):
            return action
    return None


def _is_description_flow_callback(value: str | None) -> bool:
    data = str(value or "")
    return data in _DESCRIPTION_FLOW_CALLBACKS or data.startswith(_DESCRIPTION_FLOW_PREFIXES)


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
        ("📁 Выбрать обращение", "my_cases_open"),
        ("📁 Моё дело", "my_case_open"),
        ("💬 Юридическая помощь", "contact_lawyer"),
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


async def _validate_snapshot(event, state, db) -> bool:
    if state is None or db is None:
        await _recover(
            event,
            state,
            "Не удалось подтвердить, к какому обращению относится этот шаг вопроса. Ничего не сохранено.",
        )
        return False
    try:
        snapshot = await state.get_data()
        expected_case_id = int(snapshot.get("consult_description_case_id") or 0)
        expected_consultation_id = int(snapshot.get("consult_description_id") or 0)
        _user, case, consultation = await _current_context(event, db)
    except Exception:
        logger.exception("Не удалось проверить provenance формы вопроса")
        await db.rollback()
        await _recover(
            event,
            state,
            "Не удалось безопасно проверить форму вопроса. Ничего не сохранено.",
        )
        return False

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
            "ℹ️ Этот шаг вопроса относится к другому или более раннему обращению. Черновик не записан в выбранное сейчас дело. Откройте нужное обращение и начните форму заново.",
        )
        return False
    return True


async def _clear_provenance_if_flow_finished(state) -> None:
    if state is None:
        return
    try:
        current_state = await state.get_state()
        if current_state is None:
            await state.update_data(
                consult_description_case_id=None,
                consult_description_id=None,
            )
    except Exception:
        logger.warning("Не удалось очистить завершённый provenance вопроса")


class ConsultationDescriptionProvenanceMiddleware:
    """Bind the entire M2 question workflow to one Case and Consultation.

    If no Case is selected, the first explicit entry click is the source-
    idempotent creation operation for a new M2 matter. If a Case already exists,
    the entry itself is Case-scoped before the form opens. Every intermediate
    callback and every text message then re-validates the FSM snapshot, so a
    stale Case A form cannot edit or confirm a question in selected Case B.
    """

    async def __call__(self, handler, event, data):
        state = data.get("state")
        db = data.get("db")
        entry_action = (
            _description_entry_action(event.data)
            if isinstance(event, CallbackQuery)
            else None
        )

        if isinstance(event, CallbackQuery) and entry_action is not None:
            if db is not None:
                try:
                    scope = await resolve_case_callback_scope(
                        event,
                        db,
                        action=entry_action,
                        allow_legacy_message_case_context=True,
                    )
                    if scope is None:
                        return None
                    user = scope.user
                    if scope.case is None:
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
                    logger.exception("Не удалось подготовить точный M2 context")
                    await db.rollback()
                    await _recover(
                        event,
                        state,
                        "Не удалось безопасно открыть вопрос для выбранного обращения. Новое дело не создано и другое обращение не изменено.",
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
                    "Форма вопроса не была привязана к обращению. Для безопасности откройте нужное дело и начните вопрос заново.",
                )
                return None
            return result

        if isinstance(event, CallbackQuery) and _is_description_flow_callback(event.data):
            if not await _validate_snapshot(event, state, db):
                return None
            result = await handler(event, data)
            await _clear_provenance_if_flow_finished(state)
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

        if not await _validate_snapshot(event, state, db):
            return None

        result = await handler(event, data)
        # Text capture normally advances from waiting_description to review.
        # Provenance must survive that transition and be cleared only when the
        # whole description workflow has actually ended.
        await _clear_provenance_if_flow_finished(state)
        return result


__all__ = ["ConsultationDescriptionProvenanceMiddleware"]
