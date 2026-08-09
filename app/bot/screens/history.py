from __future__ import annotations

from datetime import datetime

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_activity import CaseActivityService
from app.domain.cases.client_case_scope import (
    active_or_latest_completed_m1_case_for_user,
)

router = Router()

HISTORY_PAGE_SIZE = 7
HISTORY_CALLBACK_PREFIX = "case_history_before:"
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


def _history_cursor(callback_data: str | None) -> int | None:
    value = str(callback_data or "")
    if not value.startswith(HISTORY_CALLBACK_PREFIX):
        return None
    try:
        cursor = int(value.split(":", 1)[1])
    except (TypeError, ValueError):
        return None
    return cursor if cursor > 0 else None


def _format_datetime(value: str | None) -> str:
    if not value:
        return "Дата не указана"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return "Дата не указана"
    return parsed.strftime("%d.%m.%Y · %H:%M")


def _format_timeline(page: dict[str, object], *, completed: bool = False) -> str:
    items = list(page.get("items") or [])
    heading = "🕘 История завершённого дела" if completed else "🕘 История дела"
    if not items:
        return (
            f"{heading}\n\n"
            "Пока нет клиентских событий. Технические операции и внутренние "
            "проверки здесь не показываются."
        )

    blocks = [
        heading,
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
    cursor: int | None,
    completed: bool = False,
):
    buttons: list[tuple[str, str]] = []
    next_before_id = page.get("next_before_id")
    if page.get("has_more") and next_before_id:
        buttons.append(
            (
                "⬇️ Более ранние события",
                f"{HISTORY_CALLBACK_PREFIX}{int(next_before_id)}",
            )
        )
    if cursor is not None:
        buttons.append(("⬆️ К последним событиям", "case_history_open"))
    if completed:
        buttons.extend(
            [
                ("💳 Оплаты по делу", "payments_open"),
                ("📁 Итог дела", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
    else:
        buttons.extend(
            [
                ("✉️ Задать вопрос по делу", "message_create"),
                ("📁 Моё дело", "my_case_open"),
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
    cursor: int | None,
) -> None:
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case, completed = await active_or_latest_completed_m1_case_for_user(
        db,
        case_service=ctx.case_service,
        user_id=user.id,
    )
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

    try:
        page = await CaseActivityService(db).page(
            case_id=case.id,
            audience="client",
            before_id=cursor,
            limit=HISTORY_PAGE_SIZE,
        )
    except Exception:
        await db.rollback()
        retry_callback = (
            f"{HISTORY_CALLBACK_PREFIX}{cursor}"
            if cursor is not None
            else "case_history_open"
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
        else:
            error_buttons.extend(
                [
                    ("✉️ Задать вопрос по делу", "message_create"),
                    ("📁 Моё дело", "my_case_open"),
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
        _format_timeline(page, completed=completed),
        reply_markup=_history_buttons(
            page,
            cursor=cursor,
            completed=completed,
        ),
    )


@router.callback_query(lambda c: c.data == "case_history_open")
async def case_history(callback: CallbackQuery, db):
    await callback.answer("Загружаем последние события…")
    await _render_history(callback, db, cursor=None)


@router.callback_query(lambda c: str(c.data or "").startswith(HISTORY_CALLBACK_PREFIX))
async def case_history_before(callback: CallbackQuery, db):
    cursor = _history_cursor(callback.data)
    if cursor is None:
        await callback.answer("Ссылка на страницу устарела.", show_alert=True)
        await _render_history(callback, db, cursor=None)
        return
    await callback.answer("Загружаем более ранние события…")
    await _render_history(callback, db, cursor=cursor)
