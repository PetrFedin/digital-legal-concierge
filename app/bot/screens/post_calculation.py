from __future__ import annotations

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Этот выбор уже открыт.")


def _status(case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


@router.callback_query(lambda c: c.data == "calc_decision_open")
async def decision_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await _safe_edit(
            callback,
            "Сохранённое активное дело больше не найдено. Начните новый расчёт или вернитесь на главную.",
            reply_markup=one(
                ("🧮 Новый расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _status(case)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "🧭 Что делать после расчёта\n\n"
            "Расчёт сохранён. Выберите дальнейший путь — решение можно не принимать прямо сейчас.\n\n"
            "⚖️ Ведение дела — передача документов юристу и дальнейшее сопровождение.\n"
            "💬 Консультация — описать вопрос, при желании добавить документы и выбрать время.",
            reply_markup=one(
                ("⚖️ Продолжить ведение дела", "calc_continue_m1"),
                ("💬 Перейти к консультации", "calc_to_m2"),
                ("Пока ничего не менять", "calc_postpone"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status == CaseStatus.CLIENT_DECISION:
        await _safe_edit(
            callback,
            "🧭 Вы выбрали ведение дела\n\n"
            "Следующий обязательный шаг — подтвердить согласие на обработку персональных данных. "
            "До подтверждения документы юристу не передаются.\n\n"
            "Решение ещё можно изменить и перейти к консультации.",
            reply_markup=one(
                ("📄 Перейти к согласию", "consent_open"),
                ("💬 Вместо этого — консультация", "calc_to_m2"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(status.value).startswith("M1_"):
        await _safe_edit(
            callback,
            "Эта кнопка относится к более раннему этапу. Ведение дела уже начато — показан безопасный переход к текущему состоянию.",
            reply_markup=one(
                ("📁 Открыть текущее дело", "my_case_open"),
                ("📄 Документы", "documents_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(status.value).startswith("M2_"):
        await _safe_edit(
            callback,
            "Эта кнопка относится к более раннему этапу. Сейчас активно консультационное обращение.",
            reply_markup=one(
                ("📁 Открыть текущее дело", "my_case_open"),
                ("💬 Продолжить консультацию", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "Состояние дела уже изменилось. Откройте актуальную карточку — там показан текущий следующий шаг.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
