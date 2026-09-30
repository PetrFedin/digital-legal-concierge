from __future__ import annotations

import re

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import messages
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_case_for_user,
)
from app.domain.messages.message_service import MessageService
from app.presentation_time import format_business_datetime

router = Router()

_COMPLETED_STATUS_VALUES = {str(value) for value in CLIENT_COMPLETED_CASE_STATUSES}
_CASE_NUMBER_PATTERN = re.compile(r"\bDLC-\d{4}-\d{6}\b")


def _parse_history_target(data: str | None) -> tuple[int | None, int, bool]:
    """Return (case_id, page, legacy_unbound)."""

    value = str(data or "")
    if value == "message_history":
        return None, 0, True
    if value.startswith("message_history:v2:"):
        parts = value.split(":")
        if len(parts) != 4:
            raise ValueError("invalid message history callback")
        case_id = int(parts[2])
        page = max(0, int(parts[3]))
        if case_id <= 0:
            raise ValueError("invalid case id")
        return case_id, page, False
    if value.startswith("message_history:"):
        # Historical pagination callback: it carries only a page number.
        page = max(0, int(value.rsplit(":", 1)[1]))
        return None, page, True
    raise ValueError("unknown message history callback")


def _message_text(callback: CallbackQuery) -> str:
    message = getattr(callback, "message", None)
    return str(getattr(message, "text", "") or getattr(message, "caption", "") or "")


def _message_mentions_case(callback: CallbackQuery, case_number: str) -> bool:
    return bool(case_number and case_number in _message_text(callback))


def _visible_case_numbers(callback: CallbackQuery) -> frozenset[str]:
    return frozenset(_CASE_NUMBER_PATTERN.findall(_message_text(callback)))


def _legacy_context_conflicts(callback: CallbackQuery, selected_case) -> bool:
    numbers = _visible_case_numbers(callback)
    if not numbers:
        return False
    selected_number = str(
        getattr(selected_case, "case_number", "") if selected_case is not None else ""
    ).strip()
    return not selected_number or selected_number not in numbers


def _with_case_heading(text: str, case_number: str) -> str:
    first, separator, rest = str(text or "").partition("\n")
    if not separator:
        return f"{first}\nОбращение № {case_number}"
    return f"{first}\nОбращение № {case_number}\n{rest}"


def _format_dialog(
    dialog,
    requested_page: int,
    *,
    read_only: bool,
) -> tuple[str, int, int]:
    """Format message history in the single configured client-facing timezone."""

    page_messages, page, total_pages = messages._history_slice(dialog, requested_page)
    heading = "💬 Переписка завершённого дела" if read_only else "💬 Переписка по делу"
    if not page_messages:
        detail = (
            "Сообщений в архиве нет."
            if read_only
            else "Сообщений пока нет. Вы можете отправить первый вопрос команде."
        )
        return f"{heading}\n\n{detail}", page, total_pages

    lines = [
        heading,
        f"Страница {page + 1} из {total_pages}. Первая страница — самые новые сообщения.",
    ]
    for item in page_messages:
        author = "Вы" if item.sender_type == "client" else "Команда"
        created_at = format_business_datetime(item.created_at)
        body = messages._truncate(item.text, messages.HISTORY_ITEM_TEXT_LIMIT)
        lines.append(f"{author} · {created_at}\n{body}")
    return (
        messages._truncate("\n\n".join(lines), messages.HISTORY_TEXT_LIMIT),
        page,
        total_pages,
    )


def _history_keyboard(
    *,
    case_id: int,
    page: int,
    total_pages: int,
    read_only: bool,
    selected_same_case: bool,
):
    buttons: list[tuple[str, str]] = []
    if page < total_pages - 1:
        buttons.append(
            (
                "⬅️ Более ранние",
                f"message_history:v2:{case_id}:{page + 1}",
            )
        )
    if page > 0:
        buttons.append(
            (
                "Более новые ➡️",
                f"message_history:v2:{case_id}:{page - 1}",
            )
        )
    if not read_only and selected_same_case:
        buttons.append(("✉️ Написать сообщение", "message_create"))
    elif not read_only:
        buttons.append(
            (
                "📁 Переключиться на это обращение",
                f"my_case_select:v2:{case_id}",
            )
        )
    buttons.append(("🔄 Обновить", f"message_history:v2:{case_id}:{page}"))
    if read_only:
        # Never leave exact archive A through a raw callback that may resolve to
        # the currently selected active Case B.
        buttons.extend(
            [
                ("🕘 История этого обращения", f"case_history_open:v2:{case_id}"),
                ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
                ("📁 Активное дело", "my_case_open"),
            ]
        )
    else:
        buttons.append(("📁 Моё дело", "my_case_open"))
    buttons.append(("🏠 Главная", "nav_home"))
    return one(*buttons)


def _history_error_keyboard(
    *,
    case_id: int,
    page: int,
    read_only: bool,
    selected_same_case: bool,
):
    buttons: list[tuple[str, str]] = [
        ("🔄 Повторить", f"message_history:v2:{case_id}:{max(0, page)}"),
    ]
    if read_only:
        buttons.extend(
            [
                ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
                ("📁 Активное дело", "my_case_open"),
            ]
        )
    elif selected_same_case:
        buttons.append(("📁 Моё дело", "my_case_open"))
    else:
        buttons.append(
            ("📁 Переключиться на это обращение", f"my_case_select:v2:{case_id}")
        )
    buttons.append(("🏠 Главная", "nav_home"))
    return one(*buttons)


async def _guard_reply_draft(message: Message, state: FSMContext) -> bool:
    """Preserve an unsent question when an old reply-keyboard button is pressed."""

    data = await state.get_data()
    if not str(data.get("draft_text") or "").strip():
        return False
    await state.set_state(messages.MessageStates.confirming_message)
    await message.answer(
        "📝 У вас уже есть неотправленный черновик. Он не удалён.\n\n"
        + messages._draft_review_text(data),
        reply_markup=messages._review_markup(),
    )
    return True


async def _load_reply_history_context(message: Message, db):
    """Resolve an unambiguous Case for a historical reply-menu navigation."""

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    selected_case = await ctx.case_service.get_selected_case_for_user(
        int(user.id),
        include_terminal=False,
    )
    if selected_case is None and len(active_cases) > 1:
        return user, None, None, active_cases, False

    case = selected_case or (active_cases[0] if active_cases else None)
    read_only = False
    if case is None:
        case = await latest_completed_case_for_user(db, user_id=int(user.id))
        read_only = case is not None

    selected_same_case = bool(
        case is not None
        and not read_only
        and (
            (selected_case is not None and int(selected_case.id) == int(case.id))
            or (selected_case is None and len(active_cases) == 1)
        )
    )
    return user, case, selected_case, active_cases, selected_same_case


async def present_reply_message_history(
    message: Message,
    db,
    state: FSMContext,
) -> None:
    """Own historical `💬 Переписка` reply navigation with the v2 Case contract."""

    if await _guard_reply_draft(message, state):
        return
    await state.clear()

    _user, case, _selected_case, active_cases, selected_same_case = (
        await _load_reply_history_context(message, db)
    )
    if case is None and len(active_cases) > 1:
        await db.rollback()
        await message.answer(
            "💬 ПЕРЕПИСКА\n\n"
            "У вас несколько активных обращений. Старая кнопка не содержит номер дела, поэтому переписка не открыта автоматически. Выберите обращение явно.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if case is None:
        await db.rollback()
        await message.answer(
            "💬 История переписки появится после создания обращения.\n\n"
            "Начните с предварительного расчёта или откройте связь с юридической командой.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "preview_calc_start"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    read_only = str(case.status) in _COMPLETED_STATUS_VALUES
    service = MessageService(db)
    try:
        dialog = await service.list_case_messages(case_id, limit=100)
        text, page, total_pages = _format_dialog(dialog, 0, read_only=read_only)
        text = _with_case_heading(text, case_number)
        page_messages, _, _ = messages._history_slice(dialog, page)
        visible_team_ids = tuple(
            int(item.id)
            for item in page_messages
            if item.sender_type == "lawyer"
        )
        markup = _history_keyboard(
            case_id=case_id,
            page=page,
            total_pages=total_pages,
            read_only=read_only,
            selected_same_case=selected_same_case,
        )
        await db.rollback()
    except Exception:
        await db.rollback()
        await message.answer(
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=_history_error_keyboard(
                case_id=case_id,
                page=0,
                read_only=read_only,
                selected_same_case=selected_same_case,
            ),
        )
        return

    await message.answer(text, reply_markup=markup)
    if visible_team_ids and not read_only and selected_same_case:
        try:
            await service.mark_lawyer_messages_read(
                case_id,
                message_ids=visible_team_ids,
            )
            await db.commit()
        except Exception:
            await db.rollback()


async def present_message_history(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Render case-bound message history without touching expired ORM objects.

    Pagination callbacks carry the exact Case id. Historical raw callbacks are
    accepted only when their context is unambiguous. A trusted old bot message
    that visibly names another Case always fails closed before read tracking.
    """

    if await messages._guard_existing_draft(callback, state):
        return

    try:
        expected_case_id, requested_page, legacy_unbound = _parse_history_target(
            callback.data
        )
    except (TypeError, ValueError):
        await messages._safe_edit(
            callback,
            "Эта ссылка на переписку устарела. Данные не изменены.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    selected_case = await ctx.case_service.get_active_case_for_user(int(user.id))

    read_only = False
    case = None
    if expected_case_id is not None:
        case = await ctx.case_service.get_case_for_user(
            user_id=int(user.id),
            case_id=expected_case_id,
        )
        if case is not None:
            read_only = str(case.status) in _COMPLETED_STATUS_VALUES
    else:
        case = selected_case
        if case is None:
            case = await latest_completed_case_for_user(db, user_id=user.id)
            read_only = case is not None
        elif legacy_unbound:
            if _legacy_context_conflicts(callback, case):
                await messages._safe_edit(
                    callback,
                    "Эта старая кнопка переписки относится к другому обращению, чем выбрано сейчас. "
                    "Чтобы не показать и не отметить прочитанными сообщения другого дела, выберите обращение явно.",
                    reply_markup=one(
                        ("📁 Выбрать обращение", "my_cases_open"),
                        ("📁 Моё дело", "my_case_open"),
                        ("🏠 Главная", "nav_home"),
                    ),
                )
                return
            if len(active_cases) > 1 and not _message_mentions_case(
                callback,
                str(case.case_number),
            ):
                await messages._safe_edit(
                    callback,
                    "Эта старая кнопка переписки не содержит подтверждённый номер обращения, а у вас несколько активных дел. "
                    "Переписка не открыта автоматически — выберите дело явно.",
                    reply_markup=one(
                        ("📁 Выбрать обращение", "my_cases_open"),
                        ("🏠 Главная", "nav_home"),
                    ),
                )
                return

    if case is None:
        await messages._safe_edit(
            callback,
            "💬 История переписки появится после создания обращения.\n\n"
            "Начните с предварительного расчёта или откройте связь с юридической командой.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "preview_calc_start"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    selected_case_id = int(selected_case.id) if selected_case is not None else None
    selected_same_case = selected_case_id == case_id
    service = MessageService(db)
    try:
        dialog = await service.list_case_messages(case_id, limit=100)
        text, page, total_pages = _format_dialog(
            dialog,
            requested_page,
            read_only=read_only,
        )
        text = _with_case_heading(text, case_number)
        page_messages, _, _ = messages._history_slice(dialog, page)
        visible_team_ids = tuple(
            int(item.id)
            for item in page_messages
            if item.sender_type == "lawyer"
        )
        # Release the read transaction before Telegram network I/O. Every Case
        # scalar needed below has already been snapshotted.
        await db.rollback()
    except Exception:
        await db.rollback()
        await messages._safe_edit(
            callback,
            "Не удалось загрузить переписку. Данные не изменены.",
            reply_markup=_history_error_keyboard(
                case_id=case_id,
                page=requested_page,
                read_only=read_only,
                selected_same_case=selected_same_case,
            ),
        )
        return

    markup = _history_keyboard(
        case_id=case_id,
        page=page,
        total_pages=total_pages,
        read_only=read_only,
        selected_same_case=selected_same_case,
    )
    try:
        changed = await messages._safe_edit(callback, text, reply_markup=markup)
        if changed:
            await callback.answer()
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)
        await callback.answer("Переписка открыта новым сообщением.")

    # Read tracking is a mutation. An exact stale page for another active Case
    # may be displayed safely, but it must not change that Case until the client
    # explicitly selects it as the cabinet context.
    if visible_team_ids and not read_only and selected_same_case:
        try:
            await service.mark_lawyer_messages_read(
                case_id,
                message_ids=visible_team_ids,
            )
            await db.commit()
        except Exception:
            await db.rollback()


@router.callback_query(
    lambda c: bool(c.data)
    and (c.data == "message_history" or c.data.startswith("message_history:"))
)
async def message_history_guard(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Own every message-history callback before the legacy messages router."""

    await present_message_history(callback, db, state)


@router.message(lambda m: m.text == "💬 Переписка")
async def historical_reply_message_history_guard(
    message: Message,
    db,
    state: FSMContext,
) -> None:
    """Own historical reply-keyboard history before reply_menu_direct.router."""

    await present_reply_message_history(message, db, state)


__all__ = ["router", "present_message_history", "present_reply_message_history"]
