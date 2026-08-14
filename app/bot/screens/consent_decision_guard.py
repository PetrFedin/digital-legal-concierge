from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.consent_decision_service import (
    CONSENT_ACCEPT,
    CONSENT_DECLINE,
    ConsentDecisionError,
    ConsentDecisionService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case

router = Router()
logger = logging.getLogger(__name__)

_LEGACY_UNBOUND = {
    "consent_accept",
    "consent_decline",
    "consent_decline_confirm",
}


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Telegram не обновил атомарный экран согласия")


def _bound(action: str, case_id: int) -> str:
    return f"{action}:v2:{int(case_id)}"


def _bound_case_id(callback: CallbackQuery, action: str) -> int | None:
    value = str(callback.data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


def _status(case: Case) -> CaseStatus | None:
    try:
        return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))
    except (TypeError, ValueError):
        return None


async def _owned_case_for_screen(callback: CallbackQuery, db) -> tuple[object, Case | None]:
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case_id = _bound_case_id(callback, "consent_open")
    if case_id is None:
        return user, await ctx.case_service.get_active_case_for_user(user.id)
    case = await db.get(Case, case_id)
    if case is None or int(case.client_id) != int(user.id):
        return user, None
    return user, case


async def _stale(callback: CallbackQuery, db, outcome: str) -> None:
    await db.rollback()
    if outcome == "stale_m1":
        text = (
            "Согласие уже было учтено, а ведение дела перешло дальше. "
            "Старая кнопка ничего не изменила."
        )
        buttons = (
            ("📁 Открыть текущее дело", "my_case_open"),
            ("📄 Документы", "documents_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    elif outcome == "stale_m2":
        text = (
            "Эта кнопка согласия относится к прежнему маршруту. Сейчас активно "
            "консультационное обращение; статус и документы не изменены."
        )
        buttons = (
            ("📁 Открыть текущее дело", "my_case_open"),
            ("✉️ Написать команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "Эта кнопка больше не соответствует текущему этапу. Ничего не изменено — "
            "откройте актуальную карточку дела."
        )
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    await _safe_edit(callback, text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: c.data == "consent_open"
    or (bool(c.data) and c.data.startswith("consent_open:v2:"))
)
async def guarded_consent_open(callback: CallbackQuery, db):
    _user, case = await _owned_case_for_screen(callback, db)
    if case is None:
        await _safe_edit(
            callback,
            "Дело из этого экрана больше не найдено или недоступно. Согласие не изменялось.",
            reply_markup=one(
                ("📁 Открыть актуальное дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _status(case)
    case_id = int(case.id)
    if status == CaseStatus.CALCULATED:
        await _safe_edit(
            callback,
            "Сначала выберите дальнейший путь после расчёта. Согласие само по себе не выбирает ведение дела.",
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
            "Вы выбрали ведение дела. Чтобы передать документы юридической команде, подтвердите согласие отдельно. "
            "Подтверждение относится только к указанному обращению и не применяется к будущим делам.\n\n"
            "Если вы не готовы подтверждать согласие, можно вернуться к нейтральному расчёту или выбрать консультацию.",
            reply_markup=one(
                ("✅ Подтвердить согласие", _bound("consent_accept", case_id)),
                ("Не подтверждать", _bound("consent_decline", case_id)),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status is not None and status.value.startswith("M1_"):
        await _stale(callback, db, "stale_m1")
        return
    if status is not None and status.value.startswith("M2_"):
        await _stale(callback, db, "stale_m2")
        return
    await _stale(callback, db, "stale_other")


@router.callback_query(lambda c: c.data in _LEGACY_UNBOUND)
async def legacy_unbound_consent_refresh(callback: CallbackQuery, db):
    # Historical consent keyboards have no case identifier. They can navigate
    # to the current consent screen but are never allowed to authorize a new
    # mutation after another case has become active.
    await callback.answer("Обновляем согласие для текущего обращения.")
    await guarded_consent_open(callback, db)


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("consent_decline:v2:"))
async def guarded_consent_decline_prompt(callback: CallbackQuery, db):
    case_id = _bound_case_id(callback, "consent_decline")
    if case_id is None:
        await guarded_consent_open(callback, db)
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await db.get(Case, case_id)
    if case is None or int(case.client_id) != int(user.id):
        await _stale(callback, db, "stale_other")
        return
    if _status(case) != CaseStatus.CLIENT_DECISION:
        status = _status(case)
        await _stale(
            callback,
            db,
            "stale_m1"
            if status is not None and status.value.startswith("M1_")
            else "stale_m2"
            if status is not None and status.value.startswith("M2_")
            else "stale_other",
        )
        return
    await _safe_edit(
        callback,
        "Подтвердите отказ от согласия для этого обращения.\n\n"
        "Ведение дела M1 не начнётся, документы не будут переданы юристу. Предварительный расчёт останется сохранённым, и позже можно будет выбрать путь заново.",
        reply_markup=one(
            ("Подтвердить: не давать согласие", _bound("consent_decline_confirm", case_id)),
            ("← Вернуться к согласию", _bound("consent_open", case_id)),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: bool(c.data) and c.data.startswith("consent_accept:v2:"))
async def guarded_consent_accept(callback: CallbackQuery, db):
    case_id = _bound_case_id(callback, "consent_accept")
    if case_id is None:
        await guarded_consent_open(callback, db)
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await ConsentDecisionService(db).apply(
            client_id=user.id,
            case_id=case_id,
            decision=CONSENT_ACCEPT,
        )
        if result.outcome == "route_not_selected":
            await db.rollback()
            await _safe_edit(
                callback,
                "Сначала выберите дальнейший путь после расчёта. Согласие не выбирает M1 автоматически и ничего не изменило.",
                reply_markup=one(
                    ("🧭 Выбрать дальнейший путь", "calc_decision_open"),
                    ("📁 Моё дело", "my_case_open"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        if result.outcome != "accepted":
            await _stale(callback, db, result.outcome)
            return
        await db.commit()
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Согласие не сохранено: дело из этого сообщения больше не найдено или недоступно.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except (ConsentDecisionError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Согласие не сохранено: {error}",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарное сохранение согласия клиента не завершено")
        await _safe_edit(
            callback,
            "Согласие временно не сохранено. Переход в M1 отменён целиком; повторите действие из актуального экрана.",
            reply_markup=one(
                ("🔄 Открыть согласие", _bound("consent_open", case_id)),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Написать команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "✅ Согласие сохранено.\n\n"
        "Маршрут ведения дела теперь начат. Следующий шаг — загрузить документы и передать их юридической команде.",
        reply_markup=one(
            ("📄 Перейти к документам", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data) and c.data.startswith("consent_decline_confirm:v2:")
)
async def guarded_consent_decline(callback: CallbackQuery, db):
    case_id = _bound_case_id(callback, "consent_decline_confirm")
    if case_id is None:
        await guarded_consent_open(callback, db)
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        result = await ConsentDecisionService(db).apply(
            client_id=user.id,
            case_id=case_id,
            decision=CONSENT_DECLINE,
        )
        if result.outcome in {"stale_m1", "stale_m2", "stale_other"}:
            await _stale(callback, db, result.outcome)
            return
        await db.commit()
    except LookupError:
        await db.rollback()
        await _safe_edit(
            callback,
            "Дело из этого сообщения больше не найдено. Старая кнопка отказа ничего не изменила.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    except (ConsentDecisionError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Отказ не сохранён: {error}",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Атомарное сохранение отказа от согласия не завершено")
        await _safe_edit(
            callback,
            "Отказ временно не сохранён. Текущий этап не изменён; откройте актуальное дело перед повтором.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _safe_edit(
        callback,
        "Согласие не предоставлено.\n\n"
        "Ведение дела не начато, документы юристу не передаются. Предварительный расчёт сохранён, и выбор можно сделать позже.",
        reply_markup=one(
            ("🧭 Вернуться к выбору пути", "calc_decision_open"),
            ("💬 Перейти к консультации", _bound("calc_to_m2", case_id)),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
