from __future__ import annotations

from datetime import datetime, timezone

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.domain.cases.case_history import add_case_history_event
from app.bot.keyboards import one
from app.domain.statuses.case_statuses import CaseStatus

router = Router()
logger = logging.getLogger(__name__)


_M1_CONSENT_ALREADY_RECORDED = {
    CaseStatus.M1_DOCUMENTS_PENDING,
    CaseStatus.M1_DOCUMENTS_RECEIVED,
    CaseStatus.M1_LAWYER_REVIEW,
    CaseStatus.M1_DOCS_REQUESTED,
    CaseStatus.M1_ACCEPTED,
    CaseStatus.M1_REJECTED,
    CaseStatus.M1_CONTRACT_READY,
    CaseStatus.M1_WAITING_PAYMENT_30000,
    CaseStatus.M1_PAYMENT_30000_RECEIVED,
    CaseStatus.M1_POWER_OF_ATTORNEY,
    CaseStatus.M1_POA_RECEIVED,
    CaseStatus.M1_CLAIM_PREPARATION,
    CaseStatus.M1_CLAIM_SENT,
    CaseStatus.M1_WAITING_30_DAYS,
    CaseStatus.M1_COURT_STAGE,
    CaseStatus.M1_WAITING_PAYMENT_70000,
    CaseStatus.M1_PAYMENT_70000_RECEIVED,
    CaseStatus.M1_ENFORCEMENT,
    CaseStatus.M1_MONEY_RECEIVED,
    CaseStatus.M1_WAITING_SUCCESS_FEE,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED,
}


def _status(case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


async def _context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Экран уже актуален.")


async def _present_committed_accept(callback: CallbackQuery) -> None:
    text = (
        "✅ Согласие сохранено.\n\n"
        "Теперь можно безопасно загрузить документы по делу и передать их юристу на проверку."
    )
    reply_markup = one(
        ("📄 Перейти к документам", "documents_open"),
        ("📁 Моё дело", "my_case_open"),
        ("💬 Задать вопрос команде", "message_create"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            await callback.answer("Согласие уже сохранено.")
            return
        logger.warning("Не удалось обновить сообщение после сохранения согласия: %s", error)
        try:
            await callback.message.answer(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            logger.exception("Не удалось показать сохранённое согласие новым сообщением")
            await callback.answer(
                "Согласие сохранено. Откройте «Моё дело» для продолжения.",
                show_alert=True,
            )
            return
        await callback.answer("Согласие сохранено. Результат открыт новым сообщением.")


async def _present_committed_decline(callback: CallbackQuery) -> None:
    text = (
        "Согласие не предоставлено.\n\n"
        "Документы не будут приниматься для передачи юристу по маршруту ведения дела. "
        "Предварительный расчёт сохранён, и решение можно принять позже."
    )
    reply_markup = one(
        ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
        ("💬 Перейти к консультации", "calc_to_m2"),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            await callback.answer("Отказ уже сохранён.")
            return
        logger.warning("Не удалось обновить сообщение после отказа от согласия: %s", error)
        try:
            await callback.message.answer(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            logger.exception("Не удалось показать результат отказа новым сообщением")
            await callback.answer(
                "Отказ сохранён. Откройте «Моё дело» для продолжения.",
                show_alert=True,
            )
            return
        await callback.answer("Отказ сохранён. Результат открыт новым сообщением.")


async def _render_stale_m1(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "Согласие уже было учтено ранее, а дело перешло дальше. Старая кнопка не изменит текущий этап.",
        reply_markup=one(
            ("📁 Открыть текущее дело", "my_case_open"),
            ("📄 Документы", "documents_open"),
            ("💬 Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _render_stale_m2(callback: CallbackQuery) -> None:
    await _safe_edit(
        callback,
        "Эта кнопка согласия относится к прежнему маршруту. Сейчас активно консультационное обращение, поэтому статус дела не изменён.",
        reply_markup=one(
            ("📁 Открыть текущее дело", "my_case_open"),
            ("💬 Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_open")
async def consent_open(callback: CallbackQuery, db):
    _ctx, _user, case = await _context(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Активное дело не найдено. Старая кнопка согласия не создаёт новое обращение автоматически.",
            reply_markup=one(
                ("🧮 Начать расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _status(case)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "Сначала выберите дальнейший путь после расчёта. Согласие понадобится только для маршрута ведения дела.",
            reply_markup=one(
                ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status == CaseStatus.CLIENT_DECISION:
        await _safe_edit(
            callback,
            "📄 Согласие на обработку персональных данных\n\n"
            "Для загрузки документов и передачи их юристу нужно подтвердить согласие.\n\n"
            "Подтверждая согласие, вы разрешаете обработку данных и документов только в рамках обращения. "
            "Если согласие не предоставлено, документы по маршруту ведения дела не передаются.",
            reply_markup=one(
                ("✅ Подтверждаю согласие", "consent_accept"),
                ("Не даю согласие", "consent_decline"),
                ("← Вернуться к выбору пути", "calc_decision_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status in _M1_CONSENT_ALREADY_RECORDED:
        await _render_stale_m1(callback)
        return
    if status.value.startswith("M2_"):
        await _render_stale_m2(callback)
        return

    await _safe_edit(
        callback,
        "Согласие сейчас не является следующим шагом. Откройте актуальное состояние дела.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_accept")
async def consent_accept(callback: CallbackQuery, db):
    ctx, user, case = await _context(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Согласие не сохранено: активное дело больше не найдено. Новое дело по старой кнопке не создаётся.",
            reply_markup=one(
                ("🧮 Начать расчёт", "calc_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _status(case)
    if status in _M1_CONSENT_ALREADY_RECORDED:
        await _render_stale_m1(callback)
        return
    if status.value.startswith("M2_"):
        await _render_stale_m2(callback)
        return
    if status not in {CaseStatus.CALCULATED, CaseStatus.CLIENT_DECISION}:
        await _safe_edit(
            callback,
            "Старая кнопка согласия больше не соответствует текущему этапу. Данные дела не изменены.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        now = datetime.now(timezone.utc)
        case.consent_status = 'GIVEN'
        case.consent_date = now
        await add_case_history_event(db, actor_type='client', actor_id=user.id, case_id=case.id, action='CONSENT_GIVEN', new_value={'consent_status': 'GIVEN', 'consent_date': now.isoformat()})
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент явно подтвердил согласие на обработку персональных данных",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить согласие клиента")
        await _safe_edit(
            callback,
            "Согласие временно не сохранено. Текущий этап не изменён — повторите подтверждение или откройте дело.",
            reply_markup=one(
                ("🔄 Повторить подтверждение", "consent_accept"),
                ("📁 Моё дело", "my_case_open"),
                ("💬 Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _present_committed_accept(callback)


@router.callback_query(lambda c: c.data == "consent_decline")
async def consent_decline(callback: CallbackQuery, db):
    _ctx, _user, case = await _context(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Активное дело не найдено. Никаких изменений не внесено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    status = _status(case)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "Согласие ещё не предоставлено, поэтому ничего отменять не нужно. Расчёт сохранён — выберите дальнейший путь, когда будете готовы.",
            reply_markup=one(
                ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status == CaseStatus.CLIENT_DECISION:
        await _safe_edit(
            callback,
            "Подтвердите отказ от согласия\n\n"
            "После подтверждения маршрут ведения дела не перейдёт к загрузке документов. "
            "Предварительный расчёт останется сохранён, и к выбору можно будет вернуться позже.",
            reply_markup=one(
                ("Подтвердить отказ", "consent_decline_confirm"),
                ("← Вернуться к согласию", "consent_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status in _M1_CONSENT_ALREADY_RECORDED:
        await _safe_edit(
            callback,
            "Дело уже перешло дальше после ранее подтверждённого согласия. Старая кнопка отказа не откатывает этап и не удаляет данные автоматически. "
            "Для изменения или отзыва согласия обратитесь к команде по текущему делу.",
            reply_markup=one(
                ("💬 Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status.value.startswith("M2_"):
        await _render_stale_m2(callback)
        return

    await _safe_edit(
        callback,
        "Эта кнопка отказа относится к другому этапу. Никаких изменений не внесено.",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_decline_confirm")
async def consent_decline_confirm(callback: CallbackQuery, db):
    ctx, user, case = await _context(callback, db)
    if not case:
        await _safe_edit(
            callback,
            "Активное дело больше не найдено. Отказ не записывался в новое обращение.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    status = _status(case)
    if status == CaseStatus.CALCULATED:
        await _present_committed_decline(callback)
        return
    if status in _M1_CONSENT_ALREADY_RECORDED:
        await _safe_edit(
            callback,
            "Эта кнопка подтверждения устарела: дело уже перешло дальше. Автоматический откат не выполнен. Для отзыва ранее данного согласия напишите команде.",
            reply_markup=one(
                ("💬 Написать команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status.value.startswith("M2_"):
        await _render_stale_m2(callback)
        return
    if status != CaseStatus.CLIENT_DECISION:
        await _safe_edit(
            callback,
            "Эта кнопка подтверждения больше не соответствует текущему этапу. Никаких изменений не внесено.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        now = datetime.now(timezone.utc)
        case.consent_status = 'DECLINED'
        case.decline_date = now
        await add_case_history_event(db, actor_type='client', actor_id=user.id, case_id=case.id, action='CONSENT_DECLINED', new_value={'consent_status': 'DECLINED', 'decline_date': now.isoformat()})
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.CALCULATED,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент явно подтвердил отказ от согласия на обработку данных для маршрута М1",
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить отказ клиента от согласия")
        await _safe_edit(
            callback,
            "Отказ временно не сохранён. Текущий этап не изменён — повторите подтверждение или вернитесь к согласию.",
            reply_markup=one(
                ("🔄 Повторить подтверждение отказа", "consent_decline_confirm"),
                ("← Вернуться к согласию", "consent_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _present_committed_decline(callback)
