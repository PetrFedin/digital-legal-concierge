from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.client_case_view import (
    load_client_case_view,
    progress_bar,
    route_label,
)
from app.bot.consultation_result import (
    clip_client_result,
    consultation_result_view,
    latest_terminal_client_consultation,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import documents, my_case, payments
from app.domain.cases.client_case_scope import (
    CLIENT_COMPLETED_CASE_STATUSES,
    completed_cases_for_user,
)
from app.domain.documents.document_service import DocumentService
from app.domain.payments.payment_service import PaymentService
from app.presentation_time import format_business_datetime

router = Router()

_COMPLETED_VALUES = {str(status) for status in CLIENT_COMPLETED_CASE_STATUSES}
_ARCHIVE_DOCUMENT_PAGE_SIZE = 8
_ARCHIVE_BUTTON_INSTALLED = False


def install_archive_button() -> None:
    """Expose archive from every active My Case card as secondary navigation."""

    global _ARCHIVE_BUTTON_INSTALLED
    if _ARCHIVE_BUTTON_INSTALLED:
        return
    original = my_case._case_buttons

    def with_archive(view, **kwargs):
        items = list(original(view, **kwargs))
        callbacks = [str(callback) for _, callback in items]
        if "my_case_archive_open" not in callbacks:
            button = ("🗄 Архив обращений", "my_case_archive_open")
            try:
                history_index = callbacks.index("case_history_open")
            except ValueError:
                try:
                    home_index = callbacks.index("nav_home")
                except ValueError:
                    items.append(button)
                else:
                    items.insert(home_index, button)
            else:
                items.insert(history_index + 1, button)
        return items

    my_case._case_buttons = with_archive
    _ARCHIVE_BUTTON_INSTALLED = True


def _archive_case_label(case) -> str:
    return f"{case.case_number} · {route_label(case.route)}"


def _archive_closed_at(case) -> str:
    value = getattr(case, "closed_at", None) or getattr(case, "updated_at", None)
    return format_business_datetime(value) if value else "дата завершения уточняется"


async def _completed_cases(event, db):
    ctx = BotContextService(db)
    if isinstance(event, CallbackQuery):
        user = await ctx.get_user_from_callback(event)
    else:
        user = await ctx.get_user_from_message(event)
    cases = await completed_cases_for_user(db, user_id=int(user.id))
    return ctx, user, cases


async def _owned_completed_case(callback: CallbackQuery, db, case_id: int):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_case_for_user(
        user_id=int(user.id),
        case_id=int(case_id),
    )
    if case is None or str(case.status) not in _COMPLETED_VALUES:
        return ctx, user, None
    return ctx, user, case


def _archive_selector(cases) -> tuple[str, list[tuple[str, str]]]:
    if not cases:
        return (
            "🗄 АРХИВ ОБРАЩЕНИЙ\n\n"
            "Завершённых обращений пока нет. Активные дела остаются в разделе «Моё дело».",
            [
                ("📁 Моё дело", "my_case_open"),
                ("🧮 Новое обращение", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ],
        )

    lines = [
        "🗄 АРХИВ ОБРАЩЕНИЙ",
        "",
        "СЕЙЧАС",
        f"Завершённых обращений: {len(cases)}.",
        "",
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
        "Выберите точное обращение. Просмотр архива не меняет выбранное активное дело и не запускает никаких действий.",
        "",
    ]
    buttons: list[tuple[str, str]] = []
    for case in cases:
        lines.append(f"• {_archive_case_label(case)} · {_archive_closed_at(case)}")
        buttons.append(
            (
                f"🗄 {case.case_number} · {route_label(case.route)}",
                f"my_case_archive:v2:{int(case.id)}",
            )
        )
    buttons.extend(
        [
            ("📁 Активное дело", "my_case_open"),
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return "\n".join(lines), buttons


async def _archive_case_projection(db, *, user_id: int, case):
    view = await load_client_case_view(db, case)
    payments_list = await PaymentService(db).list_case_payments(int(case.id))
    latest_result = await latest_terminal_client_consultation(
        db,
        client_id=int(user_id),
        case_id=int(case.id),
    )
    payment_text = (
        "Платежей по обращению нет"
        if not payments_list
        else f"Платежей в истории: {len(payments_list)}"
    )
    result_text = ""
    has_result = latest_result is not None
    if latest_result is not None:
        _result_case, consultation = latest_result
        result_view = consultation_result_view(consultation)
        result_text = result_view.title if result_view else "Итог консультации сохранён"
    return view, payment_text, has_result, result_text


def _archive_case_buttons(case_id: int, *, has_result: bool) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    if has_result:
        buttons.append(
            (
                "👨‍⚖ Итог консультации",
                f"client_archive_result:v2:{case_id}",
            )
        )
    buttons.extend(
        [
            ("📄 Документы", f"client_archive_documents:v2:{case_id}:0"),
            ("💳 Оплаты", f"client_archive_payments:v2:{case_id}"),
            ("💬 Переписка", f"message_history:v2:{case_id}:0"),
            ("🕘 История дела", f"case_history_open:v2:{case_id}"),
            ("🗄 Другие завершённые", "my_case_archive_open"),
            ("📁 Активное дело", "my_case_open"),
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return buttons


async def _archive_case_text_buttons(db, *, user_id: int, case):
    view, payment_text, has_result, result_text = await _archive_case_projection(
        db,
        user_id=user_id,
        case=case,
    )
    lines = [
        "🗄 АРХИВ ОБРАЩЕНИЯ",
        f"№ {view.case_number}",
        view.route_label,
        "",
        "СЕЙЧАС",
        "Обращение завершено. Архив доступен только для просмотра.",
        progress_bar(100),
        "",
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
        "Если нужно проверить материалы, откройте точный раздел этого архива. Для нового вопроса создайте отдельное обращение.",
        "",
        "АРХИВНЫЕ МАТЕРИАЛЫ",
        f"📄 Документы: {view.documents.summary}",
        f"💳 Оплаты: {payment_text}",
    ]
    if result_text:
        lines.append(f"👨‍⚖ Консультация: {result_text}")
    lines.extend(
        [
            f"🗓 Завершено: {_archive_closed_at(case)}",
            "",
            "Просмотр этого архива не меняет активное обращение и не повторяет старые платежи, загрузки или юридические действия.",
        ]
    )
    return "\n".join(lines), _archive_case_buttons(int(case.id), has_result=has_result)


async def _show_archive_selector_callback(callback: CallbackQuery, db) -> None:
    _ctx, _user, cases = await _completed_cases(callback, db)
    text, buttons = _archive_selector(cases)
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "my_case_open")
async def route_my_case_or_archive(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if active_cases:
        await my_case._render_case(callback, db)
        return

    completed = await completed_cases_for_user(db, user_id=int(user.id))
    if len(completed) > 1:
        text, buttons = _archive_selector(completed)
        await callback.message.edit_text(text, reply_markup=one(*buttons))
        return
    if len(completed) == 1:
        text, buttons = await _archive_case_text_buttons(
            db,
            user_id=int(user.id),
            case=completed[0],
        )
        await callback.message.edit_text(text, reply_markup=one(*buttons))
        return
    await my_case._render_case(callback, db)


@router.message(lambda m: m.text in {"📁 Мое дело", "📁 Моё дело"})
async def route_reply_my_case_or_archive(message: Message, state: FSMContext, db):
    # Preserve the canonical unsent-draft protection before changing navigation.
    from app.bot.screens import common, reply_menu_direct

    if await common._guard_message_draft(message, state):
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    if active_cases:
        await reply_menu_direct.direct_reply_my_case(message, state, db)
        return

    completed = await completed_cases_for_user(db, user_id=int(user.id))
    if len(completed) > 1:
        await state.clear()
        text, buttons = _archive_selector(completed)
        await message.answer(text, reply_markup=one(*buttons))
        return
    if len(completed) == 1:
        await state.clear()
        text, buttons = await _archive_case_text_buttons(
            db,
            user_id=int(user.id),
            case=completed[0],
        )
        await message.answer(text, reply_markup=one(*buttons))
        return
    await reply_menu_direct.direct_reply_my_case(message, state, db)


@router.callback_query(lambda c: c.data == "my_case_archive_open")
async def open_client_archive(callback: CallbackQuery, db):
    await _show_archive_selector_callback(callback, db)


@router.callback_query(lambda c: str(c.data or "").startswith("my_case_archive:v2:"))
async def open_exact_archive_case(callback: CallbackQuery, db):
    try:
        case_id = int(str(callback.data).split(":", 2)[2])
    except (TypeError, ValueError):
        case_id = 0
    if case_id <= 0:
        await callback.message.edit_text(
            "Ссылка на архив повреждена. Никакое дело не изменено.",
            reply_markup=one(
                ("🗄 Архив обращений", "my_case_archive_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    _ctx, user, case = await _owned_completed_case(callback, db, case_id)
    if case is None:
        await callback.message.edit_text(
            "Это завершённое обращение не найдено или недоступно вашей учётной записи.",
            reply_markup=one(
                ("🗄 Обновить архив", "my_case_archive_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    text, buttons = await _archive_case_text_buttons(
        db,
        user_id=int(user.id),
        case=case,
    )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


def _archive_document_target(value: str) -> tuple[int, int]:
    parts = value.split(":")
    if len(parts) != 4 or parts[0] != "client_archive_documents" or parts[1] != "v2":
        raise ValueError("invalid archive document callback")
    case_id = int(parts[2])
    page = max(0, int(parts[3]))
    if case_id <= 0:
        raise ValueError("invalid archive case id")
    return case_id, page


@router.callback_query(
    lambda c: str(c.data or "").startswith("client_archive_documents:v2:")
)
async def open_exact_archive_documents(callback: CallbackQuery, db):
    try:
        case_id, requested_page = _archive_document_target(str(callback.data))
    except (TypeError, ValueError):
        await callback.message.edit_text(
            "Ссылка на архив документов устарела. Ничего не изменено.",
            reply_markup=one(("🗄 Архив обращений", "my_case_archive_open")),
        )
        return
    _ctx, _user, case = await _owned_completed_case(callback, db, case_id)
    if case is None:
        await callback.message.edit_text(
            "Архив документов этого обращения недоступен.",
            reply_markup=one(("🗄 Архив обращений", "my_case_archive_open")),
        )
        return

    all_documents = await DocumentService(db).list_case_documents(case_id)
    current = documents._active_documents(all_documents)
    previous = documents._archived_documents(all_documents)
    rows = [(item, False) for item in current] + [(item, True) for item in previous]
    total_pages = max(1, (len(rows) + _ARCHIVE_DOCUMENT_PAGE_SIZE - 1) // _ARCHIVE_DOCUMENT_PAGE_SIZE)
    page = min(requested_page, total_pages - 1)
    start = page * _ARCHIVE_DOCUMENT_PAGE_SIZE
    page_rows = rows[start : start + _ARCHIVE_DOCUMENT_PAGE_SIZE]

    lines = [
        "📄 ДОКУМЕНТЫ · АРХИВ",
        f"Обращение № {case.case_number}",
        "",
        "СЕЙЧАС",
        f"Файлов в архиве: {len(rows)} · актуальных на момент закрытия: {len(current)} · предыдущих версий: {len(previous)}.",
        "",
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
        "Просмотрите нужный файл/версию. Загрузка, замена и повторная передача юристу для закрытого дела недоступны.",
    ]
    if page_rows:
        lines.extend(["", "ФАЙЛЫ"])
        for item, is_history in page_rows:
            prefix = "Предыдущая версия" if is_history else "Версия на момент закрытия"
            lines.append(f"{prefix}\n{documents._document_block(item, history=is_history)}")
    else:
        lines.extend(["", "Документов в этом обращении нет."])
    if total_pages > 1:
        lines.extend(["", f"Страница {page + 1} из {total_pages}."])

    buttons: list[tuple[str, str]] = []
    if page > 0:
        buttons.append(
            ("⬅️ Предыдущая страница", f"client_archive_documents:v2:{case_id}:{page - 1}")
        )
    if page + 1 < total_pages:
        buttons.append(
            ("Следующая страница ➡️", f"client_archive_documents:v2:{case_id}:{page + 1}")
        )
    buttons.extend(
        [
            ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
            ("🗄 Другие завершённые", "my_case_archive_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=one(*buttons))


@router.callback_query(
    lambda c: str(c.data or "").startswith("client_archive_payments:v2:")
)
async def open_exact_archive_payments(callback: CallbackQuery, db):
    try:
        case_id = int(str(callback.data).split(":", 2)[2])
    except (TypeError, ValueError):
        case_id = 0
    _ctx, _user, case = await _owned_completed_case(callback, db, case_id)
    if case is None:
        await callback.message.edit_text(
            "Архив оплат этого обращения недоступен.",
            reply_markup=one(("🗄 Архив обращений", "my_case_archive_open")),
        )
        return

    rows = await PaymentService(db).list_case_payments(case_id)
    text = (
        "💳 ОПЛАТЫ · АРХИВ\n"
        f"Обращение № {case.case_number}\n\n"
        "СЕЙЧАС\n"
        "Дело завершено. Финансовые записи доступны только для просмотра.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "При необходимости откройте конкретную запись платежа; старые ссылки и тестовые подтверждения для закрытого дела не выполняют действий.\n\n"
        + (
            "Платежей по этому обращению нет."
            if not rows
            else "\n\n".join(payments.payment_summary_line(item) for item in rows)
        )
    )
    buttons = [
        (payments.payment_action_label(item), f"pay_open:{int(item.id)}")
        for item in rows
    ]
    buttons.extend(
        [
            ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
            ("🕘 История дела", f"case_history_open:v2:{case_id}"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: str(c.data or "").startswith("client_archive_result:v2:")
)
async def open_exact_archive_consultation_result(callback: CallbackQuery, db):
    try:
        case_id = int(str(callback.data).split(":", 2)[2])
    except (TypeError, ValueError):
        case_id = 0
    _ctx, user, case = await _owned_completed_case(callback, db, case_id)
    if case is None:
        await callback.message.edit_text(
            "Итог консультации этого обращения недоступен.",
            reply_markup=one(("🗄 Архив обращений", "my_case_archive_open")),
        )
        return

    latest = await latest_terminal_client_consultation(
        db,
        client_id=int(user.id),
        case_id=case_id,
    )
    if latest is None:
        await callback.message.edit_text(
            "👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ\n"
            f"Обращение № {case.case_number}\n\n"
            "Завершённый итог для этого точного обращения не найден. Результат другого дела не подставляется.",
            reply_markup=one(
                ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
                ("🗄 Другие завершённые", "my_case_archive_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    _result_case, consultation = latest
    result_view = consultation_result_view(consultation)
    lines = [
        "👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ · АРХИВ",
        f"Обращение № {case.case_number}",
        "",
        "СЕЙЧАС",
        result_view.title if result_view else "Консультация завершена",
    ]
    if result_view:
        lines.append(result_view.status_text)
    if consultation.scheduled_at:
        lines.extend(["", f"🗓 Встреча: {format_business_datetime(consultation.scheduled_at)}"])
    if result_view and result_view.show_lawyer_result:
        result = clip_client_result(consultation.lawyer_result)
        lines.extend(
            [
                "",
                "РЕЗУЛЬТАТ ЮРИСТА",
                result or "Отдельный текст результата не сохранён; детали остаются в материалах обращения.",
            ]
        )
    lines.extend(
        [
            "",
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
            "Действий по закрытому обращению больше не требуется. При необходимости откройте точные архивные материалы ниже.",
        ]
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=one(
            ("📄 Документы", f"client_archive_documents:v2:{case_id}:0"),
            ("💳 Оплаты", f"client_archive_payments:v2:{case_id}"),
            ("💬 Переписка", f"message_history:v2:{case_id}:0"),
            ("🕘 История", f"case_history_open:v2:{case_id}"),
            ("🗄 Архив обращения", f"my_case_archive:v2:{case_id}"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["install_archive_button", "router"]
