from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.client_case_view import (
    CLIENT_ACTIONS,
    ClientAction,
    client_action_for,
    format_consultation_time,
    format_updated_at,
    load_client_case_view,
    money,
    next_action_text,
    progress_bar,
    route_label,
)
from app.bot.consultation_result import latest_terminal_client_consultation
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.payments.mode import payments_disabled

router = Router()


TERMINAL_CONSULTATION_SUMMARIES = frozenset(
    {
        "Консультация проведена",
        "Клиент не подключился",
        "Юрист не подключился",
        "Консультация отменена",
        "Консультация закрыта",
        "Консультация перенесена",
    }
)


async def _safe_edit(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
    unchanged_notice: str = "Статус дела пока не изменился.",
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer(unchanged_notice)


async def _active_case_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


def _document_detail(view) -> str:
    text = view.documents.summary
    if view.documents.archived_count:
        text += f" · в истории {view.documents.archived_count}"
    return text


def _case_buttons(view) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    if view.unread_team_messages:
        buttons.append(
            (
                f"💬 Прочитать новые ответы ({view.unread_team_messages})",
                "message_history",
            )
        )

    has_consultation_result = (
        view.consultation_summary in TERMINAL_CONSULTATION_SUMMARIES
    )
    if has_consultation_result:
        buttons.append(("👨‍⚖ Итог консультации", "consultation_result_open"))

    if view.action and not (
        has_consultation_result
        and view.action.callback == "consultation_booked_open"
    ):
        buttons.append(
            (
                f"▶️ {view.action.label}",
                f"next_action:v2:{view.case_id}:{view.action_key}",
            )
        )
    elif not has_consultation_result:
        buttons.append(("🔄 Обновить статус", "my_case_open"))

    if not view.action or view.action.callback not in {
        "documents_open",
        "doc_finish_upload",
    }:
        buttons.append(("📄 Документы", "documents_open"))
    else:
        buttons.append(("📋 Все документы", "documents_open"))

    if not payments_disabled():
        buttons.append(("💳 Оплаты", "payments_open"))
    buttons.extend(
        [
            ("🕘 История дела", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return buttons


async def _render_case(callback: CallbackQuery, db, *, notice: str | None = None):
    _, user, case = await _active_case_context(callback, db)
    if not case:
        latest_result = await latest_terminal_client_consultation(
            db,
            client_id=user.id,
        )
        text = "📁 Активных дел сейчас нет.\n\nВыберите, с чего начать:"
        buttons: list[tuple[str, str]] = []
        if latest_result:
            text = (
                "📁 Активных дел сейчас нет.\n\n"
                "Итог последней консультации сохранён и доступен отдельно."
            )
            buttons.append(
                ("👨‍⚖ Открыть итог консультации", "consultation_result_open")
            )
        buttons.extend(
            [
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Записаться на консультацию", "calc_to_m2"),
                ("🏠 Главная", "nav_home"),
            ]
        )
        if notice:
            text = f"{notice}\n\n{text}"
        await _safe_edit(
            callback,
            text,
            reply_markup=one(*buttons),
        )
        return

    view = await load_client_case_view(db, case)
    lines: list[str] = []
    if notice:
        lines.extend([notice, ""])
    lines.extend(
        [
            "📁 Моё дело",
            f"№ {view.case_number} · {view.route_label}",
            "",
            "Текущий этап",
            view.status_label,
            progress_bar(view.progress_percent),
            "",
            "Ваш следующий шаг",
            view.next_action,
        ]
    )
    if view.unread_team_messages:
        lines.extend(
            [
                "",
                f"💬 Новые ответы команды: {view.unread_team_messages}",
                "Ответы ещё не отмечены прочитанными. Откройте переписку, чтобы увидеть их полностью.",
            ]
        )
    if view.documents.blocker:
        lines.extend(["", f"⚠️ Что мешает продолжить: {view.documents.blocker}"])

    lines.extend(
        [
            "",
            "Готовность",
            f"🧮 Расчёт: {view.calculation_summary}",
            f"📄 Документы: {_document_detail(view)}",
        ]
    )
    if view.route == "M2" or view.consultation_summary != "Не назначена":
        lines.append(f"🗓 Консультация: {view.consultation_summary}")
    if view.payments_summary:
        lines.append(f"💳 Оплаты: {view.payments_summary}")
    lines.extend(["", f"Обновлено: {format_updated_at(view.updated_at)}"])

    await _safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=one(*_case_buttons(view)),
    )


@router.callback_query(lambda c: c.data == "my_case_open")
async def my_case(callback: CallbackQuery, db):
    await _render_case(callback, db)


@router.callback_query(lambda c: c.data.startswith("next_action:"))
async def next_action(callback: CallbackQuery, db):
    parts = str(callback.data or "").split(":")
    requested_case_id: int | None = None
    requested_status: str | None = None
    requested_action_key: str | None = None

    if len(parts) >= 4 and parts[1] == "v2":
        try:
            requested_case_id = int(parts[2])
        except ValueError:
            requested_case_id = None
        requested_action_key = parts[3]
    elif len(parts) >= 3:
        # Compatibility with messages that stored case id and raw case status.
        try:
            requested_case_id = int(parts[1])
        except ValueError:
            requested_case_id = None
        requested_status = parts[2]
    elif len(parts) == 2:
        # Compatibility with the oldest messages that stored only status.
        requested_status = parts[1]

    _, _, case = await _active_case_context(callback, db)
    if not case:
        await _render_case(
            callback,
            db,
            notice="Это дело уже завершено или больше не активно.",
        )
        return

    view = await load_client_case_view(db, case)
    if requested_case_id is not None and requested_case_id != case.id:
        await _render_case(
            callback,
            db,
            notice="Вы открыли кнопку от другого дела. Показано актуальное состояние.",
        )
        return
    if requested_action_key and requested_action_key != view.action_key:
        await _render_case(
            callback,
            db,
            notice=(
                "Данные дела или документов уже изменились, либо в переписке "
                "появился новый ответ. Показан актуальный следующий шаг."
            ),
        )
        return
    if requested_status and requested_status != view.case_status:
        await _render_case(
            callback,
            db,
            notice="Статус дела уже изменился. Показан актуальный следующий шаг.",
        )
        return

    action = view.action
    if not action:
        await _render_case(
            callback,
            db,
            notice="Сейчас действие от вас не требуется.",
        )
        return

    await _safe_edit(
        callback,
        f"▶️ {action.label}\n\n{action.description}",
        reply_markup=one(
            (action.label, action.callback),
            ("↩️ Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        unchanged_notice="Это действие уже открыто.",
    )