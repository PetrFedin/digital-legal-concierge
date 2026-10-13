"""Owner-scoped, read-only Telegram archive for terminal M1/M2 matters.

Every callback carries the target case_id and is authorized afresh. Generic
mutating current-case callbacks must never be linked from archived views.
"""
from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.client_case_view import format_updated_at, load_client_case_view
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens.documents import _document_block
from app.bot.screens.history import _format_timeline
from app.bot.screens.payments import payment_summary_line
from app.domain.cases.case_activity import CaseActivityService
from app.domain.documents.document_service import DocumentService
from app.domain.messages.message_service import MessageService
from app.domain.payments.payment_service import PaymentService
from app.models.case import Case

router = Router()

ARCHIVE_STATUSES = frozenset({"M1_CLOSED", "M2_CLOSED", "ARCHIVED"})
DOCS_PAGE_SIZE = 6
PAYMENTS_PAGE_SIZE = 6
HISTORY_PAGE_SIZE = 7
MESSAGE_PAGE_SIZE = 5
MAX_TELEGRAM_TEXT = 3800

_ARCHIVE_PREFIXES = ("cases_archive:", "archive_docs:", "archive_payments:", "archive_history:", "archive_messages:")


def _number(value: str, *, allow_zero: bool = False) -> int | None:
    """Avoid signed, massive, whitespace or non-ASCII callback integers."""
    if not value.isascii() or not value.isdecimal() or len(value) > 18:
        return None
    candidate = int(value)
    if candidate < 0 or (candidate == 0 and not allow_zero):
        return None
    return candidate


def parse_archive_callback(data: str | None) -> tuple[str, int, int] | None:
    parts = str(data or "").split(":")
    if len(parts) == 2 and parts[0] == "cases_archive":
        case_id = _number(parts[1])
        return ("case", case_id, 0) if case_id is not None else None
    if len(parts) == 3 and parts[0] in {
        "archive_docs", "archive_payments", "archive_history", "archive_messages"
    }:
        case_id = _number(parts[1])
        position = _number(parts[2], allow_zero=True)
        if case_id is None or position is None:
            return None
        if parts[0] != "archive_history" and position > 10000:
            return None
        return (parts[0], case_id, position)
    return None


def _archive_buttons(case_id: int):
    return one(
        ("📄 Документы и версии", f"archive_docs:{case_id}:0"),
        ("💳 История оплат", f"archive_payments:{case_id}:0"),
        ("🕘 История событий", f"archive_history:{case_id}:0"),
        ("💬 Переписка", f"archive_messages:{case_id}:0"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    )


def _back_buttons(case_id: int, *, more: tuple[str, str] | None = None):
    buttons = []
    if more:
        buttons.append(more)
    buttons.extend([
        ("🔒 Карточка закрытого дела", f"cases_archive:{case_id}"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    ])
    return one(*buttons)


async def _safe_edit(callback: CallbackQuery, message: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(message[:MAX_TELEGRAM_TEXT], reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Данные архива не изменились.")


async def _load_owned_archived_case(callback: CallbackQuery, db, case_id: int):
    """Fail closed: a terminal, owned Case is required for every archive read."""
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    return await ctx.case_service.get_terminal_case_for_user(
        user_id=user.id, case_id=case_id
    )


async def _render_case(callback: CallbackQuery, db, case: Case) -> None:
    view = await load_client_case_view(db, case)
    lines = [
        f"🔒 Архивное дело № {view.case_number}",
        f"Маршрут: {view.route_label}",
        f"Статус: {view.status_label}",
        "",
        "Дело завершено. Изменение этапов, документов и оплат недоступно.",
        "",
        f"🧮 Расчёт: {view.calculation_summary}",
        f"📄 Документы: {view.documents.summary}",
    ]
    if view.consultation_summary != "Не назначена":
        lines.append(f"🗓 Консультация: {view.consultation_summary}")
    if view.enforcement_summary:
        lines.append(f"⚖️ Исполнение: {view.enforcement_summary}")
    if view.closure_reason:
        lines.append(f"✅ Основание закрытия: {view.closure_reason}")
    lines.extend([
        f"Обновлено: {format_updated_at(view.updated_at)}",
        "",
        "Выберите раздел для просмотра сохранённых данных:",
    ])
    await _safe_edit(callback, "\n".join(lines), reply_markup=_archive_buttons(case.id))


def _page(rows: list, page: int, size: int):
    total_pages = max(1, (len(rows) + size - 1) // size)
    bounded = min(page, total_pages - 1)
    start = bounded * size
    return rows[start : start + size], bounded, total_pages


async def _render_documents(callback: CallbackQuery, db, case: Case, page: int):
    documents = await DocumentService(db).list_case_documents(case.id)
    rows, current_page, pages = _page(documents, page, DOCS_PAGE_SIZE)
    text = (
        f"📄 Документы закрытого дела № {case.case_number}\n"
        "Просмотр без возможности загрузки новой версии или изменения статуса.\n\n"
        + ("\n\n".join(_document_block(d, history=str(d.status) == "ARCHIVED") for d in rows)
           if rows else "Документов нет.")
        + f"\n\nСтраница {current_page + 1} из {pages}."
    )
    buttons = []
    if current_page:
        buttons.append(("⬅️ Новые документы", f"archive_docs:{case.id}:{current_page - 1}"))
    if current_page + 1 < pages:
        buttons.append(("Старые документы ➡️", f"archive_docs:{case.id}:{current_page + 1}"))
    buttons.extend([
        ("🔒 Карточка закрытого дела", f"cases_archive:{case.id}"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    ])
    await _safe_edit(callback, text, reply_markup=one(*buttons))


async def _render_payments(callback: CallbackQuery, db, case: Case, page: int):
    payments = await PaymentService(db).list_case_payments(case.id)
    rows, current_page, pages = _page(payments, page, PAYMENTS_PAGE_SIZE)
    text = (
        f"💳 История оплат дела № {case.case_number}\n"
        "Только сведения о ранее созданных платежах; повторная оплата недоступна.\n\n"
        + ("\n\n".join(payment_summary_line(p) for p in rows)
           if rows else "Платежей по делу нет.")
        + f"\n\nСтраница {current_page + 1} из {pages}."
    )
    buttons = []
    if current_page:
        buttons.append(("⬅️ Более новые", f"archive_payments:{case.id}:{current_page - 1}"))
    if current_page + 1 < pages:
        buttons.append(("Более ранние ➡️", f"archive_payments:{case.id}:{current_page + 1}"))
    buttons.extend([
        ("🔒 Карточка закрытого дела", f"cases_archive:{case.id}"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    ])
    await _safe_edit(callback, text, reply_markup=one(*buttons))


async def _render_history(callback: CallbackQuery, db, case: Case, cursor: int):
    timeline = await CaseActivityService(db).page(
        case_id=case.id,
        audience="client",
        before_id=cursor or None,
        limit=HISTORY_PAGE_SIZE,
    )
    text = (
        f"🔒 Архив № {case.case_number}\n\n"
        + _format_timeline(timeline)
    )
    buttons = []
    if cursor:
        buttons.append(("⬆️ К последним событиям", f"archive_history:{case.id}:0"))
    if timeline.get("has_more") and timeline.get("next_before_id"):
        buttons.append((
            "⬇️ Более ранние события",
            f"archive_history:{case.id}:{int(timeline['next_before_id'])}",
        ))
    buttons.extend([
        ("🔒 Карточка закрытого дела", f"cases_archive:{case.id}"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    ])
    await _safe_edit(callback, text, reply_markup=one(*buttons))


async def _render_messages(callback: CallbackQuery, db, case: Case, page: int):
    rows, current_page, pages = await MessageService(db).list_case_messages_page(
        case.id, page=page, page_size=MESSAGE_PAGE_SIZE
    )
    blocks = []
    for message in rows:
        role = "Вы" if message.sender_type == "client" else "Команда"
        timestamp = (
            message.created_at.strftime("%d.%m.%Y %H:%M")
            if message.created_at else "дата не указана"
        )
        body = " ".join(str(message.text or "").split())
        if len(body) > 540:
            body = body[:539].rstrip() + "…"
        blocks.append(f"{role} · {timestamp}\\n{body}")
    text = (
        f"💬 Архив переписки № {case.case_number}\\n"
        "Сохранённые сообщения. Новая отправка из архива недоступна.\\n\\n"
        + ("\\n\\n".join(blocks) if blocks else "Сообщений пока нет.")
        + f"\\n\\nСтраница {current_page + 1} из {pages}."
    )
    buttons = []
    if current_page:
        buttons.append(("⬅️ Более новые", f"archive_messages:{case.id}:{current_page - 1}"))
    if current_page + 1 < pages:
        buttons.append(("Более ранние ➡️", f"archive_messages:{case.id}:{current_page + 1}"))
    buttons.extend([
        ("🔒 Карточка закрытого дела", f"cases_archive:{case.id}"),
        ("📂 Все обращения", "cases_list"),
        ("🏠 Главная", "nav_home"),
    ])
    await _safe_edit(callback, text, reply_markup=one(*buttons))


@router.callback_query(lambda c: str(c.data or "").startswith(_ARCHIVE_PREFIXES))
async def open_readonly_archive(callback: CallbackQuery, db):
    parsed = parse_archive_callback(callback.data)
    if parsed is None:
        await _safe_edit(
            callback,
            "Ссылка на архивное дело недействительна. Откройте список обращений.",
            reply_markup=one(("📂 Все обращения", "cases_list"), ("🏠 Главная", "nav_home")),
        )
        return
    screen, case_id, position = parsed
    case = await _load_owned_archived_case(callback, db, case_id)
    if case is None:
        await _safe_edit(
            callback,
            "Архивное дело недоступно. Откройте список своих обращений.",
            reply_markup=one(("📂 Все обращения", "cases_list"), ("🏠 Главная", "nav_home")),
        )
        return
    try:
        if screen == "case":
            await _render_case(callback, db, case)
        elif screen == "archive_docs":
            await _render_documents(callback, db, case, position)
        elif screen == "archive_payments":
            await _render_payments(callback, db, case, position)
        elif screen == "archive_history":
            await _render_history(callback, db, case, position)
        elif screen == "archive_messages":
            await _render_messages(callback, db, case, position)
    except TelegramBadRequest:
        raise
    except Exception:
        await db.rollback()
        await _safe_edit(
            callback,
            "⚠️ Архив пока не открылся. Данные дела не изменены. Повторите попытку.",
            reply_markup=_back_buttons(case.id, more=(
                "🔄 Повторить", str(callback.data)
            )),
        )
