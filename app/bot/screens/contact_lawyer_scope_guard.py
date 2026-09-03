from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import (
    bound_case_callback,
    callback_matches_action,
    resolve_case_callback_scope,
)
from app.bot.consultation_result import (
    consultation_result_view,
    is_terminal_consultation,
    latest_case_consultation,
)
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import (
    consultation_booking_ui,
    consultation_intake,
    consultation_results,
    m1_rejection_recovery,
)
from app.domain.consultations.consultation_intake import consultation_description_ready
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus

router = Router()


def _status(case) -> CaseStatus | None:
    if case is None:
        return None
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


async def _no_active_context(callback: CallbackQuery, db, state: FSMContext) -> None:
    """Allow a genuinely global contact entry only when no old Case is named."""

    # The existing intake remains the single owner of creating the first M2
    # context. The scope guard has already proved that this raw callback is not a
    # stale old Case screen.
    await consultation_intake.contact_lawyer(callback, db, state)


async def _render_terminal_contact(callback: CallbackQuery, db, *, case, consultation) -> None:
    case_id = int(case.id)
    case_number = str(case.case_number)
    view = consultation_result_view(consultation)
    title = view.title if view else "Консультация завершена"
    await db.rollback()
    await consultation_results._safe_edit(
        callback,
        "💬 СВЯЗАТЬСЯ С ЮРИДИЧЕСКОЙ КОМАНДОЙ\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        f"{title}. Старая запись на консультацию больше не является следующим шагом.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Напишите по этому обращению или откройте сохранённый итог. Другой Case не подставляется автоматически.",
        reply_markup=one(
            (
                "✉️ Написать по обращению",
                bound_case_callback("message_create", case_id),
            ),
            ("🗂 Переписка", f"message_history:v2:{case_id}:0"),
            (
                "👨‍⚖ Итог консультации",
                bound_case_callback("consultation_result_open", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _render_active_contact(callback: CallbackQuery, db, *, case) -> None:
    case_id = int(case.id)
    case_number = str(case.case_number)
    route = str(case.route or "")

    if route != RouteCode.M2.value:
        await db.rollback()
        await consultation_results._safe_edit(
            callback,
            "💬 СВЯЗАТЬСЯ С ЮРИДИЧЕСКОЙ КОМАНДОЙ\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "У вас уже есть активное дело. Новая консультация не заменяет и не скрывает его.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Напишите команде именно по этому обращению или откройте его переписку.",
            reply_markup=one(
                (
                    "✉️ Написать по обращению",
                    bound_case_callback("message_create", case_id),
                ),
                ("🗂 Переписка", f"message_history:v2:{case_id}:0"),
                ("📄 Документы", bound_case_callback("documents_open", case_id)),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    consultation = await ConsultationService(db).get_current_for_case(case_id)
    if consultation is not None:
        consultation_status = str(consultation.status)
        description_ready = consultation_description_ready(consultation)
    else:
        consultation_status = ""
        description_ready = False
    await db.rollback()

    if consultation_status == ConsultationStatus.BOOKED.value:
        primary = (
            "👨‍⚖ Открыть подтверждённую запись",
            bound_case_callback("consultation_booked_open", case_id),
        )
    elif description_ready:
        primary = (
            "📅 Продолжить: выбрать время",
            bound_case_callback("consult_booking_start", case_id),
        )
    else:
        primary = (
            "📝 Продолжить: описать вопрос",
            bound_case_callback("consult_subject_start", case_id),
        )

    await consultation_results._safe_edit(
        callback,
        "💬 ЮРИДИЧЕСКАЯ КОНСУЛЬТАЦИЯ\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Сохранённый консультационный маршрут остаётся в этом же обращении.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Продолжите сохранённый этап либо напишите команде по этому Case.",
        reply_markup=one(
            primary,
            (
                "✉️ Написать сообщение",
                bound_case_callback("message_create", case_id),
            ),
            ("🗂 Переписка", f"message_history:v2:{case_id}:0"),
            ("📄 Документы", bound_case_callback("documents_open", case_id)),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "contact_lawyer"))
async def scoped_contact_lawyer(
    callback: CallbackQuery,
    db,
    state: FSMContext,
) -> None:
    """Route contact intent only after exact/visible Case provenance is checked."""

    value = str(callback.data or "")
    explicitly_bound = value.startswith("contact_lawyer:v2:")

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))

    if not active_cases and not explicitly_bound:
        scope = await resolve_case_callback_scope(
            callback,
            db,
            action="contact_lawyer",
            allow_legacy_message_case_context=True,
        )
        if scope is None:
            return
        await _no_active_context(callback, db, state)
        return

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="contact_lawyer",
        allow_legacy_message_case_context=True,
    )
    if scope is None or scope.case is None:
        return

    case = scope.case
    if _status(case) == CaseStatus.M1_REJECTED:
        await m1_rejection_recovery._show_options(callback, case)
        return

    consultation = await latest_case_consultation(db, case_id=int(case.id))
    if is_terminal_consultation(consultation):
        await _render_terminal_contact(
            callback,
            db,
            case=case,
            consultation=consultation,
        )
        return

    await _render_active_contact(callback, db, case=case)


@router.callback_query(
    lambda c: callback_matches_action(c.data, "consultation_booked_open")
)
async def scoped_consultation_booked_open(callback: CallbackQuery, db) -> None:
    """Protect the confirmed-consultation entry from stale Case reinterpretation."""

    value = str(callback.data or "")
    explicitly_bound = value.startswith("consultation_booked_open:v2:")
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_cases = await ctx.case_service.get_active_cases_for_user(int(user.id))

    if not active_cases and not explicitly_bound:
        # Keep the established completed-M2 archive recovery for a genuinely
        # context-free old button. A visible old Case number is still rejected by
        # the scope resolver before this fallback is reached.
        scope = await resolve_case_callback_scope(
            callback,
            db,
            action="consultation_booked_open",
            allow_legacy_message_case_context=True,
        )
        if scope is None:
            return
        await consultation_booking_ui.consultation_action_center(callback, db)
        return

    scope = await resolve_case_callback_scope(
        callback,
        db,
        action="consultation_booked_open",
        allow_legacy_message_case_context=True,
    )
    if scope is None or scope.case is None:
        return

    case = scope.case
    consultation = await latest_case_consultation(db, case_id=int(case.id))
    if is_terminal_consultation(consultation):
        await consultation_results._render_result(
            callback,
            case=case,
            consultation=consultation,
        )
        return

    await consultation_booking_ui.consultation_action_center(callback, db)


__all__ = ["router"]
