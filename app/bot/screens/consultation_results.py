from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
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


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Экран уже актуален.")


def _case_is_closed(case) -> bool:
    try:
        status = (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )
    except ValueError:
        return False
    return status in CLOSED_CASE_STATUSES


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
    return consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")


def _result_buttons(view, *, case) -> list[tuple[str, str]]:
    if _case_is_closed(case):
        return [("🏠 На главную", "nav_home")]
    if str(case.status) == CaseStatus.M2_TO_M1.value and str(getattr(view, "primary_callback", "")) == "documents_open":
        return [
            ("✅ Подтвердить продолжение М1", "m2_to_m1_continue"),
            ("📁 Моё дело", "my_case_open"),
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
            "Консультация ещё не завершена. Откройте актуальную запись.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    lines = [
        view.title,
        "",
        f"Дело: {case.case_number}",
    ]
    scheduled_at = _format_scheduled_at(consultation)
    if scheduled_at:
        lines.append(f"Время встречи: {scheduled_at}")
    lines.extend(["", view.status_text])

    if view.show_lawyer_result:
        result = clip_client_result(consultation.lawyer_result)
        if result:
            lines.extend(["", "Результат юриста:", result])
        else:
            lines.extend(
                [
                    "",
                    "Результат юриста сохранён без отдельного текста для клиента. Уточнить детали можно у команды.",
                ]
            )

    if _case_is_closed(case) and view.primary_callback != "nav_home":
        lines.extend(
            [
                "",
                "Что дальше:",
                "Это дело уже закрыто. Итог сохранён для просмотра; новых действий по старому делу нет.",
            ]
        )
    else:
        lines.extend(["", "Что дальше:", view.next_step])
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
        "💬 Связаться с юридической командой\n\n"
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
            "Завершённая консультация пока не найдена.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💬 Юридическая консультация", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    case, consultation = latest
    await _render_result(callback, case=case, consultation=consultation)


@router.callback_query(lambda c: c.data == "m2_to_m1_continue")
async def m2_to_m1_continue(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case or str(case.status) != CaseStatus.M2_TO_M1.value:
        await _safe_edit(
            callback,
            "Предложение продолжить работу по стандартному маршруту уже не актуально.",
            reply_markup=one(("📁 Моё дело", "my_case_open"), ("🏠 Главная", "nav_home")),
        )
        return
    latest = await latest_terminal_client_consultation(
        db, client_id=user.id, case_id=case.id
    )
    if not latest or str(latest[1].decision) != "to_m1":
        await _safe_edit(
            callback,
            "Решение юриста о переходе в стандартный маршрут не найдено.",
            reply_markup=one(("📁 Моё дело", "my_case_open"), ("🏠 Главная", "nav_home")),
        )
        return
    try:
        await ctx.case_service.transfer_to_m1(
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил продолжение М1 после консультации",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить переход М2 в М1")
        await _safe_edit(
            callback,
            "Не удалось сохранить подтверждение. Дело осталось на прежнем этапе. Попробуйте ещё раз.",
            reply_markup=one(
                ("🔄 Повторить", "m2_to_m1_continue"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    await _safe_edit(
        callback,
        "✅ Вы подтвердили продолжение работы по стандартному маршруту М1. "
        "История консультации сохранена. Следующий шаг — документы.",
        reply_markup=one(
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


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
        "Предыдущий вопрос сохранён как основа новой встречи. "
        "Перед выбором времени его можно обновить.\n\n"
        f"Текущий вопрос:\n{description}",
        reply_markup=one(
            ("📅 Выбрать дату и время", "consult_booking_start"),
            ("📝 Обновить вопрос", "consult_subject_start"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
