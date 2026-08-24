from __future__ import annotations

import logging

from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one

logger = logging.getLogger(__name__)

_MESSAGE_ENTRY_CALLBACKS = {
    "message_create",
}
_MESSAGE_NEW_TARGET_CALLBACKS = frozenset(
    {
        "message_new_request",
        "message_retarget_new_confirm",
        "message_retarget_new",
    }
)
_MESSAGE_FLOW_CALLBACKS = frozenset(
    {
        "message_back_category",
        "message_back_urgency",
        "message_edit_text",
        "message_review_return",
        "message_submit",
    }
)
_MESSAGE_FLOW_PREFIXES = (
    "msg_cat_",
    "msg_urgency_",
)


def _is_message_flow_callback(value: str | None) -> bool:
    data = str(value or "")
    return data in _MESSAGE_FLOW_CALLBACKS or data.startswith(_MESSAGE_FLOW_PREFIXES)


async def _current_scope(event, db):
    ctx = BotContextService(db)
    if isinstance(event, CallbackQuery):
        user = await ctx.get_user_from_callback(event)
    else:
        user = await ctx.get_user_from_message(event)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    active_cases = await ctx.case_service.get_active_cases_for_user(user.id)
    return user, case, active_cases


async def _safe_present(event, text: str, *, reply_markup, notice: str) -> None:
    try:
        if isinstance(event, CallbackQuery):
            try:
                await event.message.edit_text(text, reply_markup=reply_markup)
            except TelegramBadRequest as error:
                if "message is not modified" not in str(error).lower():
                    await event.message.answer(text, reply_markup=reply_markup)
            try:
                await event.answer(notice)
            except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
                pass
        else:
            await event.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не показал recovery клиентского сообщения")


async def _recover_without_draft(
    event,
    state,
    *,
    current_case,
    active_count: int,
    text: str,
) -> None:
    if state is not None:
        try:
            await state.clear()
        except Exception:
            logger.warning("Не удалось очистить FSM переписки после смены дела")

    buttons: list[tuple[str, str]] = []
    if current_case is not None:
        buttons.append(("✉️ Написать по актуальному делу", "message_create"))
    if active_count > 1:
        buttons.append(("📁 Выбрать обращение", "my_cases_open"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _safe_present(
        event,
        text,
        reply_markup=one(*buttons),
        notice="Старый экран не изменил переписку.",
    )


async def _render_preserved_draft(
    event,
    state,
    *,
    current_case,
    active_count: int,
    reason: str,
) -> None:
    """Show an exact, explicit recovery choice without retargeting the draft."""

    from app.bot.screens import messages

    data = await state.get_data()
    draft = str(data.get("draft_text") or "").strip()
    if not draft:
        await _recover_without_draft(
            event,
            state,
            current_case=current_case,
            active_count=active_count,
            text=reason,
        )
        return

    await state.set_state(messages.MessageStates.confirming_message)
    await state.update_data(
        client_message_recovery_case_id=(
            int(current_case.id) if current_case is not None else None
        )
    )
    data = await state.get_data()

    origin_number = str(data.get("case_number") or "").strip()
    origin = f"делу {origin_number}" if origin_number else "новому обращению"
    if current_case is not None:
        current = f"сейчас выбрано дело {current_case.case_number}"
    elif active_count > 1:
        current = "сейчас активны несколько обращений и ни одно не выбрано"
    else:
        current = "сейчас активное дело не выбрано"

    buttons: list[tuple[str, str]] = []
    if current_case is not None:
        buttons.append(
            (
                f"➡️ Перенести черновик в {current_case.case_number}",
                f"message_retarget_current:v2:{int(current_case.id)}",
            )
        )
    elif active_count == 0:
        buttons.append(("🆕 Подготовить новое обращение", "message_retarget_new_confirm"))
    if active_count > 1:
        buttons.append(("📁 Выбрать обращение", "my_cases_open"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("✖️ Отменить черновик", "message_discard_confirm"),
        ]
    )

    await _safe_present(
        event,
        "⚠️ Контекст вопроса изменился.\n\n"
        f"{reason}\n\n"
        f"Сохранённый черновик относится к {origin}; {current}. "
        "Он НЕ отправлен и не будет автоматически перенесён в другое дело. "
        "Сначала выберите точный контекст отдельной кнопкой, затем ещё раз подтвердите отправку.\n\n"
        + messages._draft_review_text(data),
        reply_markup=one(*buttons),
        notice="Черновик сохранён и не отправлен.",
    )


async def _preserve_text_after_target_change(
    event: Message,
    state,
    *,
    current_case,
    active_count: int,
    reason: str,
) -> None:
    """Keep just-entered text as a draft without retargeting it implicitly."""

    from app.bot.screens import messages

    text = str(event.text or "").strip()
    if not text:
        await _recover_without_draft(
            event,
            state,
            current_case=current_case,
            active_count=active_count,
            text=reason + " Пустой текст не сохранён и не отправлен.",
        )
        return

    await state.update_data(
        draft_text=text,
        source_message_id=int(event.message_id),
        client_message_recovery_case_id=(
            int(current_case.id) if current_case is not None else None
        ),
    )
    await state.set_state(messages.MessageStates.confirming_message)
    await _render_preserved_draft(
        event,
        state,
        current_case=current_case,
        active_count=active_count,
        reason=reason,
    )


async def _validate_snapshot(event, state, db) -> bool:
    if state is None or db is None:
        await _recover_without_draft(
            event,
            state,
            current_case=None,
            active_count=0,
            text=(
                "Не удалось подтвердить, к какому обращению относится этот шаг переписки. "
                "Ничего не отправлено."
            ),
        )
        return False

    try:
        snapshot = await state.get_data()
        raw_case_id = snapshot.get("client_message_case_id")
        if raw_case_id in (None, ""):
            raw_case_id = snapshot.get("case_id")
        expected_case_id = int(raw_case_id or 0)
        new_request = bool(snapshot.get("new_request_confirmed"))
        _user, current_case, active_cases = await _current_scope(event, db)
    except Exception:
        logger.exception("Не удалось проверить provenance клиентского сообщения")
        await db.rollback()
        await _recover_without_draft(
            event,
            state,
            current_case=None,
            active_count=0,
            text="Не удалось безопасно проверить контекст вопроса. Ничего не отправлено.",
        )
        return False

    valid = True
    reason = ""
    if expected_case_id > 0:
        valid = current_case is not None and int(current_case.id) == expected_case_id
        reason = "Выбранное дело изменилось после открытия формы вопроса."
    elif new_request:
        valid = current_case is None and len(active_cases) == 0
        reason = "Пока готовился новый запрос, появилось или стало доступно активное обращение."
    else:
        # This is not an active message draft managed by this middleware.
        return True

    if valid:
        return True

    await db.rollback()
    if isinstance(event, Message):
        await _preserve_text_after_target_change(
            event,
            state,
            current_case=current_case,
            active_count=len(active_cases),
            reason=reason,
        )
    else:
        await _render_preserved_draft(
            event,
            state,
            current_case=current_case,
            active_count=len(active_cases),
            reason=reason,
        )
    return False


async def _clear_provenance_if_flow_finished(state) -> None:
    if state is None:
        return
    try:
        current_state = await state.get_state()
        if current_state is None:
            await state.update_data(
                client_message_case_id=None,
                client_message_recovery_case_id=None,
            )
    except Exception:
        logger.warning("Не удалось очистить завершённый provenance переписки")


class ClientMessageProvenanceMiddleware:
    """Bind the complete client-message draft workflow to one exact Case.

    A message form opened for Case A keeps that provenance through category,
    urgency, text editing and final confirmation. Switching the Telegram cabinet
    to Case B never silently retargets the draft. Recovery preserves entered text
    and requires an explicit exact-case retarget before the normal submit handler
    can write anything.
    """

    async def __call__(self, handler, event, data):
        state = data.get("state")
        db = data.get("db")
        callback_data = str(event.data or "") if isinstance(event, CallbackQuery) else ""

        if isinstance(event, CallbackQuery) and callback_data in _MESSAGE_ENTRY_CALLBACKS:
            if db is not None:
                try:
                    _user, current_case, active_cases = await _current_scope(event, db)
                except Exception:
                    logger.exception("Не удалось определить Case перед открытием переписки")
                    await db.rollback()
                    await _recover_without_draft(
                        event,
                        state,
                        current_case=None,
                        active_count=0,
                        text="Не удалось безопасно открыть вопрос. Другое обращение не изменено.",
                    )
                    return None
                if current_case is None and len(active_cases) > 1:
                    snapshot = await state.get_data() if state is not None else {}
                    if str(snapshot.get("draft_text") or "").strip():
                        await _render_preserved_draft(
                            event,
                            state,
                            current_case=None,
                            active_count=len(active_cases),
                            reason="Перед новым сообщением нужно выбрать точное обращение.",
                        )
                    else:
                        await _recover_without_draft(
                            event,
                            state,
                            current_case=None,
                            active_count=len(active_cases),
                            text=(
                                "✉️ У вас несколько активных обращений. "
                                "Перед сообщением выберите точное дело — новый запрос автоматически не создаётся."
                            ),
                        )
                    return None

            result = await handler(event, data)
            if db is None or state is None:
                return result
            try:
                _user, case, _active_cases = await _current_scope(event, db)
                if case is not None:
                    await state.update_data(client_message_case_id=int(case.id))
            except Exception:
                logger.exception("Не удалось привязать черновик сообщения к делу")
            return result

        if isinstance(event, CallbackQuery) and callback_data in _MESSAGE_NEW_TARGET_CALLBACKS:
            if db is not None:
                try:
                    _user, current_case, active_cases = await _current_scope(event, db)
                except Exception:
                    logger.exception("Не удалось проверить новый target переписки")
                    await db.rollback()
                    return None

                blocks_new_target = (
                    callback_data in {"message_retarget_new_confirm", "message_retarget_new"}
                    and len(active_cases) > 0
                ) or (
                    callback_data == "message_new_request"
                    and current_case is None
                    and len(active_cases) > 1
                )
                if blocks_new_target:
                    await db.rollback()
                    await _render_preserved_draft(
                        event,
                        state,
                        current_case=current_case,
                        active_count=len(active_cases),
                        reason=(
                            "Новое обращение не подготовлено: сначала выберите уже существующее активное дело."
                        ),
                    )
                    return None
            return await handler(event, data)

        if isinstance(event, CallbackQuery) and _is_message_flow_callback(callback_data):
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

        raw_case_id = snapshot.get("client_message_case_id")
        if raw_case_id in (None, ""):
            raw_case_id = snapshot.get("case_id")
        if raw_case_id in (None, "") and not bool(snapshot.get("new_request_confirmed")):
            return await handler(event, data)

        if not await _validate_snapshot(event, state, db):
            return None

        result = await handler(event, data)
        # Text capture normally moves waiting_message -> confirming_message.
        # Case provenance must survive that transition and is removed only when
        # the whole draft workflow actually ends.
        await _clear_provenance_if_flow_finished(state)
        return result


__all__ = ["ClientMessageProvenanceMiddleware"]
