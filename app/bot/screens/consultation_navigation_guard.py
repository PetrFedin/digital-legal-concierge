from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.case_callback_scope import bound_case_callback
from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens import consultation_intake as legacy_intake
from app.bot.screens import payment_archive_guard
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus, RouteCode

router = Router()

_ENTRY_CALLBACKS = {"contact_lawyer", "consult_start"}


def _status(case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


async def _render(callback: CallbackQuery, text: str, *buttons: tuple[str, str]) -> None:
    await legacy_intake._safe_edit(
        callback,
        text,
        reply_markup=one(
            *buttons,
            ("✉️ Написать команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: str(c.data or "") in _ENTRY_CALLBACKS)
async def route_active_m2_navigation(
    callback: CallbackQuery,
    db,
    state: FSMContext,
):
    """Make generic consultation/help buttons resume the exact active M2 stage.

    Historical screens can contain contact_lawyer/consult_start long after the
    consultation progressed. Those callbacks are navigation only: they never
    create another case, reserve another slot or manufacture a payment while an
    M2 request is already active.
    """

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)

    if case is None or str(case.route or "").upper() != RouteCode.M2.value:
        # Preserve the canonical entry behaviour for a genuinely new request or
        # an active M1 case. The intake handler is responsible for explicit new
        # request creation and never silently mutates the selected Case.
        await legacy_intake.contact_lawyer(callback, db, state)
        return

    status = _status(case)
    case_number = str(case.case_number)
    case_id = int(case.id)
    await state.clear()

    if status == CaseStatus.M2_PAYMENT_PENDING:
        if payments_disabled():
            await _render(
                callback,
                "👨‍⚖ КОНСУЛЬТАЦИЯ\n"
                f"Обращение № {case_number}\n\n"
                "Вопрос и время уже выбраны. Онлайн-оплата для этого маршрута отключена; сейчас нужно подтвердить только актуальный удерживаемый слот.",
                (
                    "✅ Подтвердить выбранное время",
                    bound_case_callback("consult_pay", case_id),
                ),
                ("📄 Документы", "documents_open"),
            )
            return
        await payment_archive_guard.guard_active_m2_payment_list(callback, db)
        return

    if status == CaseStatus.M2_CONSULTATION_BOOKED:
        await _render(
            callback,
            "👨‍⚖ КОНСУЛЬТАЦИЯ ПОДТВЕРЖДЕНА\n"
            f"Обращение № {case_number}\n\n"
            "Новая запись не создаётся. Откройте текущую встречу, чтобы проверить дату, подготовку, документы, перенос или отмену.",
            ("▶️ Открыть текущую запись", "consultation_booked_open"),
            ("📄 Документы", "documents_open"),
        )
        return

    if status == CaseStatus.M2_CONSULTATION_DONE:
        await _render(
            callback,
            "👨‍⚖ КОНСУЛЬТАЦИЯ ПРОВЕДЕНА\n"
            f"Обращение № {case_number}\n\n"
            "Результат уже сохранён. Старый вход «Юридическая помощь» не начинает вторую консультацию поверх текущего решения.",
            ("▶️ Открыть итог консультации", "consultation_result_open"),
            ("🕘 История", "case_history_open"),
        )
        return

    if status in {
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_SLOT_PENDING,
    }:
        await _render(
            callback,
            "👨‍⚖ ПРОДОЛЖИТЬ КОНСУЛЬТАЦИЮ\n"
            f"Обращение № {case_number}\n\n"
            "Вопрос сохранён. Следующий шаг — выбрать актуальную дату и время. Документы можно добавить до или после выбора слота.",
            ("▶️ Выбрать дату и время", "consult_booking_start"),
            ("📄 Добавить документы", "documents_open"),
        )
        return

    if status in {
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
    }:
        await _render(
            callback,
            "👨‍⚖ ПРОДОЛЖИТЬ КОНСУЛЬТАЦИЮ\n"
            f"Обращение № {case_number}\n\n"
            "Сначала сохраните ситуацию и конкретный вопрос для юриста. Уже введённые данные текущего обращения не будут подменены другим делом.",
            ("▶️ Описать вопрос", "consult_subject_start"),
            ("📄 Документы", "documents_open"),
        )
        return

    if status == CaseStatus.M2_TO_M1:
        await _render(
            callback,
            "↗️ КОНСУЛЬТАЦИЯ ПЕРЕДАНА В M1\n"
            f"Обращение № {case_number}\n\n"
            "Продолжение уже идёт в основном юридическом деле. Новая M2-консультация этой кнопкой не создаётся.",
            ("▶️ Открыть текущее дело", "my_case_open"),
            ("🕘 История", "case_history_open"),
        )
        return

    await _render(
        callback,
        "👨‍⚖ ЮРИДИЧЕСКАЯ ПОМОЩЬ\n"
        f"Обращение № {case_number}\n\n"
        "У обращения уже есть активный контекст, но текущий шаг требует обновления. Новая консультация не создана.",
        ("🔄 Обновить текущее дело", "my_case_open"),
    )


__all__ = ["router"]
