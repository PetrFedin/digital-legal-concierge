from __future__ import annotations

import re

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import SelfFilingIntakeStates
from app.domain.cases.self_filing_service import SelfFilingError, SelfFilingService
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.statuses.case_statuses import CaseStatus

router = Router()

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _bound(action: str, case_id: int) -> str:
    return f"{action}:v2:{int(case_id)}"


def _case_id(data: str | None, action: str) -> int | None:
    value = str(data or "")
    prefix = f"{action}:v2:"
    if not value.startswith(prefix):
        return None
    try:
        case_id = int(value[len(prefix) :])
    except ValueError:
        return None
    return case_id if case_id > 0 else None


def _status(case) -> CaseStatus | None:
    try:
        return (
            case.status
            if isinstance(case.status, CaseStatus)
            else CaseStatus(str(case.status))
        )
    except (TypeError, ValueError):
        return None


async def _safe_edit(callback: CallbackQuery, text: str, *, reply_markup) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise
        await callback.answer("Экран уже актуален.")


async def _owned_case(callback: CallbackQuery, db, *, case_id: int):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_case_for_user(
        user_id=int(user.id),
        case_id=int(case_id),
    )
    return user, case


def _exit(case_id: int):
    return one(
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Выйти без изменения данных", "nav_home"),
    )


async def _stale(callback: CallbackQuery, db, *, case_id: int) -> None:
    await db.rollback()
    await _safe_edit(
        callback,
        "Этот экран больше не соответствует текущему этапу услуги. "
        "Данные не изменены.",
        reply_markup=one(
            ("📁 Открыть актуальное дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data)
    and c.data.startswith("self_filing_profile_start:v2:")
)
async def self_filing_profile_start(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback.data, "self_filing_profile_start")
    if case_id is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    user, case = await _owned_case(callback, db, case_id=case_id)
    if (
        case is None
        or _status(case) != CaseStatus.M1_SELF_FILING_PROFILE_PENDING
        or str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value
    ):
        await _stale(callback, db, case_id=case_id)
        return

    await state.clear()
    await state.update_data(
        self_filing_case_id=int(case.id),
        self_filing_client_id=int(user.id),
    )
    await state.set_state(SelfFilingIntakeStates.waiting_region)
    await db.rollback()
    await _safe_edit(
        callback,
        "📍 ПАКЕТ ДЛЯ САМОСТОЯТЕЛЬНОЙ ПОДАЧИ\n\n"
        "Шаг 1 из 3\n\n"
        "Укажите регион России, в котором вы живёте/находитесь или с которым "
        "связан спор. Например: «Республика Татарстан» или «Новосибирская область».\n\n"
        "Это не выбирает суд автоматически. Конкретную подсудность позже "
        "подтвердит юрист по документам дела.",
        reply_markup=_exit(case_id),
    )


@router.message(SelfFilingIntakeStates.waiting_region)
async def self_filing_region(message: Message, state: FSMContext, db):
    value = " ".join(str(message.text or "").split())
    data = await state.get_data()
    case_id = int(data.get("self_filing_case_id") or 0)
    if not value or len(value) > 255 or case_id <= 0:
        await message.answer(
            "Укажите регион текстом, до 255 символов.",
            reply_markup=_exit(case_id or 0),
        )
        return
    await state.update_data(self_filing_region=value)
    await state.set_state(SelfFilingIntakeStates.waiting_address)
    await db.rollback()
    await message.answer(
        "📍 Шаг 2 из 3\n\n"
        "Укажите ваш адрес для подготовки документов и проверки возможной "
        "подсудности. Бот не будет по нему автоматически выбирать суд.",
        reply_markup=_exit(case_id),
    )


@router.message(SelfFilingIntakeStates.waiting_address)
async def self_filing_address(message: Message, state: FSMContext, db):
    value = " ".join(str(message.text or "").split())
    data = await state.get_data()
    case_id = int(data.get("self_filing_case_id") or 0)
    if not value or len(value) > 2000 or case_id <= 0:
        await message.answer(
            "Укажите адрес текстом. Если адрес длинный, уложитесь в 2000 символов.",
            reply_markup=_exit(case_id or 0),
        )
        return
    await state.update_data(self_filing_address=value)
    await state.set_state(SelfFilingIntakeStates.waiting_email)
    await db.rollback()
    await message.answer(
        "✉️ Шаг 3 из 3\n\n"
        "Укажите email, на который после проверки и подготовки будет отправлен "
        "готовый пакет документов.",
        reply_markup=_exit(case_id),
    )


@router.message(SelfFilingIntakeStates.waiting_email)
async def self_filing_email(message: Message, state: FSMContext, db):
    email = str(message.text or "").strip().lower()
    data = await state.get_data()
    case_id = int(data.get("self_filing_case_id") or 0)
    if len(email) > 320 or not _EMAIL_RE.fullmatch(email):
        await message.answer(
            "Проверьте email. Нужен адрес вида name@example.ru.",
            reply_markup=_exit(case_id or 0),
        )
        return
    await state.update_data(self_filing_email=email)
    data = await state.get_data()
    await state.set_state(SelfFilingIntakeStates.reviewing_profile)
    await db.rollback()
    await message.answer(
        "Проверьте данные перед сохранением:\n\n"
        f"Регион: {data.get('self_filing_region')}\n"
        f"Адрес: {data.get('self_filing_address')}\n"
        f"Email: {email}\n\n"
        "Сохранение этих данных ещё не означает выбор суда. Суд и основание "
        "подсудности подтвердит юрист после проверки документов.",
        reply_markup=one(
            ("✅ Всё верно — сохранить", _bound("self_filing_profile_confirm", case_id)),
            ("🔄 Ввести заново", _bound("self_filing_profile_start", case_id)),
            ("🏠 Не сохранять", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: bool(c.data)
    and c.data.startswith("self_filing_profile_confirm:v2:")
)
async def self_filing_profile_confirm(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback.data, "self_filing_profile_confirm")
    data = await state.get_data()
    if (
        case_id is None
        or int(data.get("self_filing_case_id") or 0) != int(case_id)
        or await state.get_state() != SelfFilingIntakeStates.reviewing_profile.state
    ):
        await state.clear()
        await _stale(callback, db, case_id=int(case_id or 0))
        return

    user, case = await _owned_case(callback, db, case_id=case_id)
    if (
        case is None
        or _status(case) != CaseStatus.M1_SELF_FILING_PROFILE_PENDING
        or str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value
    ):
        await state.clear()
        await _stale(callback, db, case_id=case_id)
        return

    try:
        await SelfFilingService(db).save_confirmed_profile(
            case_id=case_id,
            client_id=int(user.id),
            region=str(data.get("self_filing_region") or ""),
            address=str(data.get("self_filing_address") or ""),
            email=str(data.get("self_filing_email") or ""),
        )
        await db.commit()
    except (SelfFilingError, ValueError) as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Данные не сохранены: {error}",
            reply_markup=one(
                ("🔄 Ввести заново", _bound("self_filing_profile_start", case_id)),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось надёжно сохранить данные. Этап не изменён; повторите из "
            "актуального дела.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    await _safe_edit(
        callback,
        "✅ Данные сохранены.\n\n"
        "Теперь загрузите:\n"
        "• ДДУ;\n"
        "• паспорт или иной документ, удостоверяющий личность;\n"
        "• все приложения и дополнительные соглашения к ДДУ;\n"
        "• иные документы по спору, которые попросит юрист.\n\n"
        "Оплата 15 000 ₽ появится только после того, как юрист подтвердит "
        "полноту комплекта и конкретную подсудность. Срок 2 рабочих дня начнётся "
        "после получения оплаты и подтверждённого полного комплекта.",
        reply_markup=one(
            ("📄 Загрузить документы", "documents_upload_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
