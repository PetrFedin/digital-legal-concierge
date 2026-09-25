from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bind_payment_case_action
from app.bot.client_case_view import (
    _payments_summary,
    format_updated_at,
    load_client_case_view,
    progress_bar,
    route_label,
)
from app.bot.consultation_result import latest_terminal_client_consultation
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.payment_presentation import offline_m1_payment_presentation
from app.domain.cases.client_case_scope import latest_completed_m1_case_for_user
from app.domain.payments.payment_service import PaymentService

router = Router()


TERMINAL_CONSULTATION_PREFIXES = (
    "Консультация проведена",
    "Клиент не подключился",
    "Юрист не подключился",
    "Запись отменена",
    "Консультация закрыта",
    "Запись перенесена",
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


def _has_consultation_result(view) -> bool:
    summary = str(getattr(view, "consultation_summary", "") or "")
    return summary.startswith(TERMINAL_CONSULTATION_PREFIXES)


async def _payment_summary(db, case_id: int) -> str:
    """Compatibility adapter to the single shared client payment projection."""

    payments = await PaymentService(db).list_case_payments(case_id)
    return _payments_summary(payments)


def _case_buttons(
    view,
    *,
    has_multiple_active_cases: bool = False,
) -> list[tuple[str, str]]:
    buttons: list[tuple[str, str]] = []
    has_consultation_result = _has_consultation_result(view)

    # Exactly one projected primary action comes first. Unread messages are a
    # visible secondary signal; they do not silently replace the process action
    # shown in the projection.
    if has_consultation_result:
        buttons.append(("👨‍⚖ Итог консультации", "consultation_result_open"))
    else:
        offline_payment = offline_m1_payment_presentation(view)
        if offline_payment:
            buttons.append((offline_payment.button_label, offline_payment.callback))
        elif view.action:
            if view.action.callback == "my_case_open":
                buttons.append(("🔄 Обновить статус", "my_case_open"))
            else:
                buttons.append(
                    (
                        f"▶️ {view.action.label}",
                        f"next_action:v2:{view.case_id}:{view.action_key}",
                    )
                )
        else:
            buttons.append(("🔄 Обновить статус", "my_case_open"))

    if view.unread_team_messages and not (
        view.action and view.action.callback == "message_history"
    ):
        buttons.append(
            (
                f"💬 Прочитать новые ответы ({view.unread_team_messages})",
                "message_history",
            )
        )

    if not view.action or view.action.callback not in {
        "documents_open",
        "doc_finish_upload",
    }:
        buttons.append(("📄 Документы", "documents_open"))
    else:
        buttons.append(("📋 Все документы", "documents_open"))

    # Payment provider availability controls creation, not access to financial
    # history. Keep the Payments cabinet visible even in disabled/offline mode.
    if not (view.action and view.action.callback == "payments_open"):
        buttons.append(("💳 Оплаты", "payments_open"))
    if not (view.action and view.action.callback == "case_history_open"):
        buttons.append(("🕘 История дела", "case_history_open"))
    if str(getattr(view, "calculation_summary", "") or "").strip():
        buttons.append(
            (
                "🔎 Основания и детализация расчёта",
                f"calc_details:v2:{int(view.case_id)}",
            )
        )
    if not (view.action and view.action.callback == "contact_lawyer"):
        buttons.append(("💬 Связаться с юристом", "contact_lawyer"))
    if has_multiple_active_cases:
        buttons.append(("📁 Выбрать другое обращение", "my_cases_open"))
    buttons.append(("🏠 Главная", "nav_home"))
    return buttons


def _case_selector(active_cases) -> tuple[str, list[tuple[str, str]]]:
    """Build the compact canonical selector when active context is ambiguous."""

    lines = [
        "📁 МОИ ОБРАЩЕНИЯ",
        "",
        "У вас несколько активных обращений. Выберите нужное — документы, оплаты, переписка и дальнейшие действия будут относиться именно к нему.",
        "",
    ]
    buttons: list[tuple[str, str]] = []
    for case in active_cases:
        service = route_label(case.route)
        lines.append(f"• {case.case_number} · {service}")
        buttons.append(
            (
                f"📁 {case.case_number} · {service}",
                f"my_case_select:v2:{int(case.id)}",
            )
        )
    buttons.extend(
        [
            ("🧮 Новый расчёт / новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    return "\n".join(lines), buttons


async def _render_completed_case(
    callback: CallbackQuery,
    db,
    *,
    case,
    notice: str | None = None,
) -> None:
    view = await load_client_case_view(db, case)
    payment_summary = view.payments_summary or "Платежей по обращению нет"
    is_m2 = str(view.route or "") == "M2"
    has_consultation_result = _has_consultation_result(view)

    lines: list[str] = []
    if notice:
        lines.extend([f"ℹ️ {notice}", ""])

    if is_m2:
        lines.extend(
            [
                "📁 ИТОГ КОНСУЛЬТАЦИИ",
                f"№ {view.case_number}",
                view.route_label,
                "",
                "СЕЙЧАС",
                "✅ Консультационный маршрут завершён",
                progress_bar(100),
                "",
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
                "Действий по этому обращению больше не требуется. Итог консультации и материалы сохранены в архиве.",
                "",
                "АРХИВ ОБРАЩЕНИЯ",
                f"🗓 Консультация: {view.consultation_summary}",
                f"📄 Документы: {_document_detail(view)}",
            ]
        )
    else:
        lines.extend(
            [
                "📁 ИТОГ ДЕЛА",
                f"№ {view.case_number}",
                view.route_label,
                "",
                "СЕЙЧАС",
                "✅ Дело завершено",
                progress_bar(100),
                "",
                "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
                "Действий по этому делу больше не требуется. Финальный платёж подтверждён, финансовый этап завершён и дело закрыто.",
                "",
                "АРХИВ ДЕЛА",
                f"📄 Документы: {_document_detail(view)}",
            ]
        )

    lines.extend(
        [
            f"💳 Оплаты: {payment_summary}",
            "",
            f"Закрыто / обновлено: {format_updated_at(view.updated_at)}",
            "Документы, история и платежи остаются доступны только для просмотра. Новое обращение создаётся отдельно.",
        ]
    )

    buttons: list[tuple[str, str]] = []
    if is_m2 and has_consultation_result:
        buttons.append(("👨‍⚖ Итог консультации", "consultation_result_open"))
    if str(getattr(view, "calculation_summary", "") or "").strip():
        buttons.append(
            (
                "🔎 Основания и детализация расчёта",
                f"calc_details:v2:{int(view.case_id)}",
            )
        )
    buttons.extend(
        [
            ("📄 Документы обращения", "documents_open"),
            ("💳 Оплаты по обращению", "payments_open"),
            ("🕘 История обращения", "case_history_open"),
            ("🧮 Новое обращение", "calc_start"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=one(*buttons),
        unchanged_notice="Итог обращения уже актуален.",
    )


async def _render_case(callback: CallbackQuery, db, *, notice: str | None = None):
    ctx, user, case = await _active_case_context(callback, db)
    if not case:
        active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
        if len(active_cases) > 1:
            text, buttons = _case_selector(active_cases)
            if notice:
                text = f"ℹ️ {notice}\n\n{text}"
            await _safe_edit(
                callback,
                text,
                reply_markup=one(*buttons),
                unchanged_notice="Список активных обращений уже актуален.",
            )
            return

        completed = await latest_completed_m1_case_for_user(db, user_id=user.id)
        if completed:
            await _render_completed_case(
                callback,
                db,
                case=completed,
                notice=notice,
            )
            return

        latest_result = await latest_terminal_client_consultation(
            db,
            client_id=user.id,
        )
        text = (
            "📁 Активного дела сейчас нет.\n\n"
            "Начните с предварительного расчёта или обратитесь к юридической команде."
        )
        buttons: list[tuple[str, str]] = []
        if latest_result:
            text = (
                "📁 Активного дела сейчас нет.\n\n"
                "Итог последней консультации сохранён. Его можно открыть отдельно или начать новое обращение."
            )
            buttons.append(
                ("👨‍⚖ Открыть итог консультации", "consultation_result_open")
            )
        buttons.extend(
            [
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Связаться с юристом", "contact_lawyer"),
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

    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))
    has_multiple_active_cases = len(active_cases) > 1
    view = await load_client_case_view(db, case)
    payment_summary = view.payments_summary or "Платежей по обращению нет"
    has_consultation_result = _has_consultation_result(view)
    stale_booking_action = bool(
        has_consultation_result
        and view.action
        and view.action.callback == "consultation_booked_open"
    )
    offline_payment = offline_m1_payment_presentation(view)
    shown_next_action = (
        "Откройте итог консультации — там показан актуальный следующий шаг."
        if stale_booking_action
        else offline_payment.next_action
        if offline_payment
        else view.next_action
    )

    lines: list[str] = []
    if notice:
        lines.extend([f"ℹ️ {notice}", ""])
    lines.extend(
        [
            "📁 МОЁ ДЕЛО",
            f"№ {view.case_number}",
            f"{view.route_label}",
            "",
            "СЕЙЧАС",
            f"{view.status_label}",
            progress_bar(view.progress_percent),
            view.now_text,
            "",
            "ТРЕБУЕТСЯ ОТ ВАС",
            view.client_requirement,
        ]
    )
    if view.blocker:
        lines.extend(
            [
                "",
                "⚠️ ЧТО МЕШАЕТ ПРОДОЛЖИТЬ",
                view.blocker,
            ]
        )
    lines.extend(
        [
            "",
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ",
            shown_next_action,
        ]
    )
    if view.unread_team_messages:
        lines.extend(
            [
                "",
                f"💬 Новые ответы команды: {view.unread_team_messages}",
                "Новые ответы доступны в переписке и не меняют процессный этап сами по себе.",
            ]
        )

    lines.extend(
        [
            "",
            "СВОДКА",
            f"🧮 Расчёт: {view.calculation_summary}",
            f"📄 Документы: {_document_detail(view)}",
        ]
    )
    if view.route == "M2" or view.consultation_summary != "Не назначена":
        lines.append(f"🗓 Консультация: {view.consultation_summary}")
    lines.extend(
        [
            f"💳 Оплаты: {payment_summary}",
            f"🕘 История: {view.history_summary}",
            "",
            f"Обновлено: {format_updated_at(view.updated_at)}",
            (
                "У вас несколько активных обращений. Номер выше определяет контекст документов, оплат, истории и переписки."
                if has_multiple_active_cases
                else "Первая кнопка ниже — самое актуальное безопасное действие."
            ),
        ]
    )

    await _safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=one(
            *_case_buttons(
                view,
                has_multiple_active_cases=has_multiple_active_cases,
            )
        ),
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
            notice=(
                "Эта кнопка относится к другому обращению. Действие не выполнено. "
                "Выберите нужное дело явно, затем откройте его актуальный следующий шаг."
            ),
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

    if offline_m1_payment_presentation(view):
        await _render_case(
            callback,
            db,
            notice=(
                "Онлайн-оплата сейчас отключена. Новый платёж не создавался: "
                "показан статус уже открытого финансового этапа."
            ),
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
            (
                action.label,
                bind_payment_case_action(action.callback, view.case_id),
            ),
            ("↩️ Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
        unchanged_notice="Это действие уже открыто.",
    )
