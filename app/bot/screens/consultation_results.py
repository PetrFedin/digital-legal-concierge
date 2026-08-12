from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.consultation_result import (
    clip_client_result,
    consultation_result_view,
    is_terminal_consultation,
    latest_case_consultation,
    latest_terminal_client_consultation,
    prepare_follow_up_consultation,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus

router = Router()
logger = logging.getLogger(__name__)

CLOSED_CASE_STATUSES = frozenset(
    {
        CaseStatus.M1_CLOSED,
        CaseStatus.M2_CLOSED,
        CaseStatus.ARCHIVED,
    }
)


def _case_status(case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


def _case_is_closed(case) -> bool:
    return _case_status(case) in CLOSED_CASE_STATUSES


def _progress_line(case, consultation) -> str:
    """Compact, client-facing progress summary with no fabricated business state."""
    if _case_is_closed(case):
        return "АРХИВ · дело закрыто"
    status = _case_status(case)
    if status == CaseStatus.M2_TO_M1:
        return "ПЕРЕДАНО В M1 · дальнейшие шаги идут в основном деле"
    if status in {CaseStatus.M2_CONSULTATION_DONE, CaseStatus.M2_CLOSED} or is_terminal_consultation(consultation):
        return "КОНСУЛЬТАЦИЯ ЗАВЕРШЕНА · результат сохранён"
    return "КОНСУЛЬТАЦИЯ · результат сохранён"


async def _callback_notice(
    callback: CallbackQuery,
    text: str,
    *,
    show_alert: bool = False,
) -> None:
    try:
        await callback.answer(text, show_alert=show_alert)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось подтвердить callback итогов консультации.")


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            await _callback_notice(callback, "Экран уже актуален.")
            return
        logger.warning("Не удалось обновить итог консультации: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram временно не обновил итог консультации: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError):
        logger.exception("Не удалось показать итог консультации новым сообщением")
        await _callback_notice(
            callback,
            "Не удалось обновить экран. Итог сохранён; откройте «Моё дело» и повторите действие.",
            show_alert=True,
        )
        return
    await _callback_notice(callback, "Итог открыт новым сообщением.")


async def _active_terminal_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_case = await ctx.case_service.get_active_case_for_user(user.id)
    if not active_case:
        return None
    consultation = await latest_case_consultation(db, case_id=active_case.id)
    if not is_terminal_consultation(consultation):
        return None
    return active_case, consultation


class TerminalBookedOpenFilter(Filter):
    """Intercept old booking buttons only when the consultation has finished."""

    async def __call__(self, callback: CallbackQuery, db) -> bool | dict[str, object]:
        if callback.data != "consultation_booked_open":
            return False

        active = await _active_terminal_context(callback, db)
        if active:
            case, consultation = active
            return {
                "result_case": case,
                "result_consultation": consultation,
            }

        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        active_case = await ctx.case_service.get_active_case_for_user(user.id)
        if active_case:
            return False

        latest = await latest_terminal_client_consultation(db, client_id=user.id)
        if latest:
            case, consultation = latest
            return {
                "result_case": case,
                "result_consultation": consultation,
            }
        return False


class TerminalContactLawyerFilter(Filter):
    """Replace stale consultation continuation with messaging after an outcome."""

    async def __call__(self, callback: CallbackQuery, db) -> bool | dict[str, object]:
        if callback.data != "contact_lawyer":
            return False
        active = await _active_terminal_context(callback, db)
        if not active:
            return False
        case, consultation = active
        return {
            "result_case": case,
            "result_consultation": consultation,
        }


def _format_scheduled_at(consultation) -> str | None:
    if not consultation.scheduled_at:
        return None
    try:
        return consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")
    except (AttributeError, ValueError):
        return None


def _result_buttons(view, *, case) -> list[tuple[str, str]]:
    if _case_is_closed(case):
        # A closed consultation is read-only, not a dead end. Keep the exact
        # archive surfaces reachable without offering stale booking/messaging
        # mutations on the old case.
        return [
            ("📁 Архив обращения", "my_case_open"),
            ("📄 Документы", "documents_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("🏠 Главная", "nav_home"),
        ]

    buttons: list[tuple[str, str]] = [
        (view.primary_label, view.primary_callback),
    ]
    if view.primary_callback != "my_case_open":
        buttons.append(("📁 Моё дело", "my_case_open"))
    if view.primary_callback != "message_create":
        buttons.append(("✉️ Написать команде", "message_create"))
    if view.primary_callback != "nav_home":
        buttons.append(("🏠 Главная", "nav_home"))
    return buttons


async def _render_result(callback: CallbackQuery, *, case, consultation) -> None:
    view = consultation_result_view(consultation)
    if view is None:
        await _safe_edit(
            callback,
            "👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ\n\n"
            "Консультация ещё не завершена. Откройте актуальную запись — там показан текущий безопасный шаг.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    closed = _case_is_closed(case)
    lines = [
        "👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ",
        f"№ {case.case_number}",
        _progress_line(case, consultation),
        "",
        "СЕЙЧАС",
        view.title,
        view.status_text,
    ]
    scheduled_at = _format_scheduled_at(consultation)
    if scheduled_at:
        lines.extend(["", f"🗓 Встреча: {scheduled_at}"])

    if view.show_lawyer_result:
        result = clip_client_result(consultation.lawyer_result)
        lines.extend(["", "РЕЗУЛЬТАТ ЮРИСТА"])
        if result:
            lines.append(result)
        else:
            lines.append(
                "Отдельный текст для клиента не сохранён. Детали результата остаются в материалах обращения."
            )

    if closed:
        lines.extend(
            [
                "",
                "ЧТО ДАЛЬШЕ",
                "Действий по закрытому обращению больше не требуется. Итог сохранён только для просмотра.",
                "",
                "АРХИВ ОБРАЩЕНИЯ",
                "Документы, платежи и история остаются доступны ниже. Новое обращение создаётся отдельно и не меняет этот архив.",
            ]
        )
    else:
        lines.extend(["", "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ", view.next_step])

    await _safe_edit(
        callback,
        "\n".join(lines),
        reply_markup=one(*_result_buttons(view, case=case)),
    )


@router.callback_query(TerminalBookedOpenFilter())
async def terminal_booked_open(
    callback: CallbackQuery,
    result_case,
    result_consultation,
):
    await _render_result(
        callback,
        case=result_case,
        consultation=result_consultation,
    )


@router.callback_query(TerminalContactLawyerFilter())
async def terminal_contact_lawyer(
    callback: CallbackQuery,
    result_case,
    result_consultation,
):
    view = consultation_result_view(result_consultation)
    title = view.title if view else "Консультация завершена"
    await _safe_edit(
        callback,
        "💬 СВЯЗАТЬСЯ С ЮРИДИЧЕСКОЙ КОМАНДОЙ\n\n"
        f"{title}. Для текущего дела используйте переписку — старая запись "
        "на консультацию больше не является следующим шагом.",
        reply_markup=one(
            ("✉️ Написать по делу", "message_create"),
            ("🗂 Открыть переписку", "message_history"),
            ("👨‍⚖ Открыть итог консультации", "consultation_result_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consultation_result_open")
async def consultation_result_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_case = await ctx.case_service.get_active_case_for_user(user.id)

    latest = None
    if active_case:
        latest = await latest_terminal_client_consultation(
            db,
            client_id=user.id,
            case_id=active_case.id,
        )
    if latest is None:
        latest = await latest_terminal_client_consultation(db, client_id=user.id)

    if latest is None:
        await _safe_edit(
            callback,
            "👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ\n\n"
            "Завершённая консультация пока не найдена. Откройте текущее дело или свяжитесь с юридической командой.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💬 Юридическая консультация", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case, consultation = latest
    await _render_result(callback, case=case, consultation=consultation)


@router.callback_query(lambda c: c.data == "consult_follow_up_start")
async def consult_follow_up_start(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await _safe_edit(
            callback,
            "Повторную консультацию не удалось начать: активное дело уже закрыто.",
            reply_markup=one(
                ("👨‍⚖ Итог консультации", "consultation_result_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    latest = await latest_terminal_client_consultation(
        db,
        client_id=user.id,
        case_id=case.id,
    )
    if latest is None:
        await _safe_edit(
            callback,
            "Рекомендация на повторную консультацию больше не актуальна.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    _, outcome = latest
    try:
        follow_up, created = await prepare_follow_up_consultation(
            db,
            case=case,
            outcome=outcome,
            client_id=user.id,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Повторную консультацию не удалось подготовить.\n\n{error}",
            reply_markup=one(
                ("📁 Открыть актуальное дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подготовить повторную консультацию")
        await _safe_edit(
            callback,
            "Повторная консультация временно не подготовлена. Данные предыдущей встречи не изменены.",
            reply_markup=one(
                ("🔄 Повторить", "consult_follow_up_start"),
                ("✉️ Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    notice = (
        "✅ Повторная консультация подготовлена."
        if created
        else "✅ Повторная консультация уже подготовлена."
    )
    description = clip_client_result(follow_up.client_description, limit=900)
    await _safe_edit(
        callback,
        f"{notice}\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Выберите новую дату и время. Предыдущий вопрос сохранён как основа новой встречи; перед записью его можно обновить.\n\n"
        f"Текущий вопрос:\n{description}",
        reply_markup=one(
            ("📅 Выбрать дату и время", "consult_booking_start"),
            ("📝 Обновить вопрос", "consult_subject_start"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )