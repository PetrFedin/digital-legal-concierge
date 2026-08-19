from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_activity import CaseActivityService
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    latest_completed_case_for_user,
)

router = Router()

HISTORY_PAGE_SIZE = 7
HISTORY_CALLBACK_PREFIX = "case_history_before:"
HISTORY_OPEN_PREFIX = "case_history_open:v2:"
_COMPLETED_STATUS_VALUES = {str(value) for value in CLIENT_COMPLETED_CASE_STATUSES}
CATEGORY_ICONS = {
    "case": "📁",
    "calculation": "🧮",
    "documents": "📄",
    "consultation": "📅",
    "messages": "💬",
    "payments": "💳",
    "contract": "📝",
    "legal_stage": "⚖️",
    "sla": "⏱",
}


def _history_target(callback_data: str | None) -> tuple[int | None, int | None, bool]:
    """Return (case_id, cursor, legacy_unbound)."""

    value = str(callback_data or "")
    if value == "case_history_open":
        return None, None, True
    if value.startswith(HISTORY_OPEN_PREFIX):
        raw_case_id = value[len(HISTORY_OPEN_PREFIX) :]
        if not raw_case_id or ":" in raw_case_id:
            raise ValueError("invalid history open callback")
        case_id = int(raw_case_id)
        if case_id <= 0:
            raise ValueError("invalid case id")
        return case_id, None, False
    if value.startswith("case_history_before:v2:"):
        parts = value.split(":")
        if len(parts) != 4:
            raise ValueError("invalid history page callback")
        case_id = int(parts[2])
        cursor = int(parts[3])
        if case_id <= 0 or cursor <= 0:
            raise ValueError("invalid history page target")
        return case_id, cursor, False
    if value.startswith(HISTORY_CALLBACK_PREFIX):
        cursor = int(value.split(":", 1)[1])
        if cursor <= 0:
            raise ValueError("invalid history cursor")
        return None, cursor, True
    raise ValueError("unknown history callback")


def _message_mentions_case(callback: CallbackQuery, case_number: str) -> bool:
    message = getattr(callback, "message", None)
    text = str(getattr(message, "text", "") or getattr(message, "caption", "") or "")
    return bool(case_number and case_number in text)


def _format_datetime(value: str | None) -> str:
    if not value:
        return "Дата не указана"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Дата не указана"
    return parsed.strftime("%d.%m.%Y · %H:%M")


def _format_timeline(
    page: dict[str, object],
    *,
    case_number: str,
    completed: bool = False,
) -> str:
    items = list(page.get("items") or [])
    heading = "🕘 История завершённого дела" if completed else "🕘 История дела"
    if not items:
        return (
            f"{heading}\n"
            f"Обращение № {case_number}\n\n"
            "Пока нет клиентских событий. Технические операции и внутренние "
            "проверки здесь не показываются."
        )

    blocks = [
        heading,
        f"Обращение № {case_number}",
        "",
        (
            "Дело завершено. Ниже сохранены значимые события в режиме просмотра:"
            if completed
            else "Последние значимые изменения по вашему делу:"
        ),
    ]
    for item in items:
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        icon = CATEGORY_ICONS.get(str(item.get("category") or "case"), "📁")
        lines = [
            f"{icon} {_format_datetime(item.get('occurred_at'))}",
            title,
        ]
        detail = str(item.get("detail") or "").strip()
        if detail:
            lines.append(detail)
        blocks.extend(["", "\n".join(lines)])
    return "\n".join(blocks)


def _history_buttons(
    page: dict[str, object],
    *,
    case_id: int,
    cursor: int | None,
    completed: bool = False,
    selected_same_case: bool = True,
):
    buttons: list[tuple[str, str]] = []
    next_before_id = page.get("next_before_id")
    if page.get("has_more") and next_before_id:
        buttons.append(
            (
                "⬇️ Более ранние события",
                f"case_history_before:v2:{case_id}:{int(next_before_id)}",
            )
        )
    if cursor is not None:
        buttons.append(
            ("⬆️ К последним событиям", f"case_history_open:v2:{case_id}")
        )
    if completed:
        buttons.extend(
            [
                ("📁 Итог дела", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
    elif selected_same_case:
        buttons.extend(
            [
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
    else:
        buttons.extend(
            [
                (
                    "📁 Переключиться на это обращение",
                    f"my_case_select:v2:{case_id}",
                ),
                ("🏠 Главная", "nav_home"),
            ]
        )
    return one(*buttons)


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("История пока не изменилась.")


async def _render_history(
    callback: CallbackQuery,
    db,
    *,
    expected_case_id: int | None,
    cursor: int | None,
    legacy_unbound: bool,
) -> None:
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    selected_case = await ctx.case_service.get_active_case_for_user(int(user.id))

    case = None
    completed = False
    if expected_case_id is not None:
        case = await ctx.case_service.get_case_for_user(
            user_id=int(user.id),
            case_id=expected_case_id,
        )
        if case is not None:
            completed = str(case.status) in _COMPLETED_STATUS_VALUES
    else:
        case = selected_case
        if case is None:
            case = await latest_completed_case_for_user(db, user_id=user.id)
            completed = case is not None
        elif legacy_unbound and cursor is not None and len(active_cases) > 1:
            selected_number = str(case.case_number)
            if not _message_mentions_case(callback, selected_number):
                await _safe_edit(
                    callback,
                    "Эта старая кнопка страницы истории не содержит номер обращения, а у вас несколько активных дел. "
                    "Чтобы не показать события другого дела, выберите обращение явно.",
                    reply_markup=one(
                        ("📁 Выбрать обращение", "my_cases_open"),
                        ("🏠 Главная", "nav_home"),
                    ),
                )
                return

    if not case:
        await _safe_edit(
            callback,
            "🕘 История дела\n\nАктивного или завершённого дела нет. Создайте обращение или вернитесь на главную.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    selected_case_id = int(selected_case.id) if selected_case is not None else None
    selected_same_case = selected_case_id == case_id
    try:
        page = await CaseActivityService(db).page(
            case_id=case_id,
            audience="client",
            before_id=cursor,
            limit=HISTORY_PAGE_SIZE,
        )
    except Exception:
        await db.rollback()
        retry_callback = (
            f"case_history_before:v2:{case_id}:{cursor}"
            if cursor is not None
            else f"case_history_open:v2:{case_id}"
        )
        error_buttons: list[tuple[str, str]] = [
            ("🔄 Повторить", retry_callback),
        ]
        if completed:
            error_buttons.extend(
                [
                    ("📁 Итог дела", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ]
            )
        elif selected_same_case:
            error_buttons.extend(
                [
                    ("✉️ Задать вопрос по делу", "message_create"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ]
            )
        else:
            error_buttons.extend(
                [
                    (
                        "📁 Переключиться на это обращение",
                        f"my_case_select:v2:{case_id}",
                    ),
                    ("🏠 Главная", "nav_home"),
                ]
            )
        await _safe_edit(
            callback,
            "⚠️ Не удалось загрузить историю. Данные дела сохранены.\n\n"
            "Повторите запрос или вернитесь к карточке дела.",
            reply_markup=one(*error_buttons),
        )
        return

    await _safe_edit(
        callback,
        _format_timeline(
            page,
            case_number=case_number,
            completed=completed,
        ),
        reply_markup=_history_buttons(
            page,
            case_id=case_id,
            cursor=cursor,
            completed=completed,
            selected_same_case=selected_same_case,
        ),
    )


@router.callback_query(
    lambda c: c.data == "case_history_open"
    or str(c.data or "").startswith(HISTORY_OPEN_PREFIX)
)
async def case_history(callback: CallbackQuery, db):
    try:
        expected_case_id, cursor, legacy_unbound = _history_target(callback.data)
    except (TypeError, ValueError):
        await callback.answer("Ссылка на историю устарела.", show_alert=True)
        await _safe_edit(
            callback,
            "Эта ссылка на историю больше не актуальна. Данные дела не изменены.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.answer("Загружаем последние события…")
    await _render_history(
        callback,
        db,
        expected_case_id=expected_case_id,
        cursor=cursor,
        legacy_unbound=legacy_unbound,
    )


@router.callback_query(lambda c: str(c.data or "").startswith(HISTORY_CALLBACK_PREFIX))
async def case_history_before(callback: CallbackQuery, db):
    try:
        expected_case_id, cursor, legacy_unbound = _history_target(callback.data)
    except (TypeError, ValueError):
        await callback.answer("Ссылка на страницу устарела.", show_alert=True)
        await _safe_edit(
            callback,
            "Эта ссылка на страницу истории больше не актуальна. Данные дела не изменены.",
            reply_markup=one(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.answer("Загружаем более ранние события…")
    await _render_history(
        callback,
        db,
        expected_case_id=expected_case_id,
        cursor=cursor,
        legacy_unbound=legacy_unbound,
    )
