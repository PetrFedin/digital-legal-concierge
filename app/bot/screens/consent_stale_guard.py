from __future__ import annotations

from aiogram import Router
from aiogram.filters import Filter
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


class CalculatedConsentAcceptFilter(Filter):
    """Catch the old consent button before legacy code can use it as route choice."""

    async def __call__(self, callback: CallbackQuery, db) -> bool:
        if callback.data != "consent_accept":
            return False
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        case = await ctx.case_service.get_active_case_for_user(user.id)
        if case is None:
            return False
        try:
            status = (
                case.status
                if isinstance(case.status, CaseStatus)
                else CaseStatus(str(case.status))
            )
        except (TypeError, ValueError):
            return False
        return status == CaseStatus.CALCULATED


@router.callback_query(CalculatedConsentAcceptFilter())
async def calculated_consent_accept_is_not_route_choice(
    callback: CallbackQuery,
    db,
):
    # No DB mutation. The user must explicitly choose M1 or M2 from the current
    # decision screen. Telegram keeps old inline keyboards indefinitely, so an
    # old consent message must never double as a hidden M1 selection.
    await db.rollback()
    await callback.message.edit_text(
        "↩️ Эта кнопка согласия относится к прежнему экрану.\n\n"
        "Сейчас расчёт сохранён, но маршрут ещё не выбран. Согласие само по себе "
        "не означает выбор ведения дела и ничего не изменило.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Выберите актуальный путь: ведение дела, консультация или пока ничего не менять.",
        reply_markup=one(
            ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router", "CalculatedConsentAcceptFilter"]
