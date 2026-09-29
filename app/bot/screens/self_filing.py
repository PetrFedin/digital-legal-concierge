from __future__ import annotations

import re
from datetime import datetime, timezone

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import SelfFilingIntakeStates
from app.domain.cases.self_filing_service import (
    SelfFilingEmailVerificationError,
    SelfFilingError,
    SelfFilingService,
)
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


def _mask_email(value: str | None) -> str:
    email = str(value or "").strip().lower()
    if "@" not in email:
        return "указанный email"
    local, domain = email.rsplit("@", 1)
    if not local:
        return f"***@{domain}"
    visible = local[:2] if len(local) > 2 else local[:1]
    return f"{visible}***@{domain}"


def _challenge_active(package) -> bool:
    if not package.email_verification_hash or package.email_verification_expires_at is None:
        return False
    expires = package.email_verification_expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) <= expires


def _verification_markup(case_id: int):
    return one(
        ("📨 Отправить новый код", _bound("self_filing_email_resend", case_id)),
        ("✏️ Изменить email или данные", _bound("self_filing_profile_restart", case_id)),
        ("📁 Моё дело", "my_case_open"),
        ("🏠 Главная", "nav_home"),
    )


def _verification_text(package, *, resent: bool = False) -> str:
    masked = _mask_email(package.delivery_email)
    prefix = "✅ Новый код отправлен." if resent else "✉️ Подтвердите email."
    return (
        f"{prefix}\n\n"
        f"Мы отправили шестизначный код на {masked}. "
        "Введите код одним сообщением.\n\n"
        "Готовый пакет с персональными и юридическими документами будет отправлен "
        "только на подтверждённый адрес. До подтверждения email нельзя перейти "
        "к загрузке документов или оплате."
    )


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
        "Этот экран больше не соответствует текущему состоянию услуги. "
        "Данные не изменены.",
        reply_markup=one(
            ("📁 Открыть актуальное дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _begin_profile(
    callback: CallbackQuery,
    state: FSMContext,
    db,
    *,
    case_id: int,
    force_restart: bool,
) -> None:
    user, case = await _owned_case(callback, db, case_id=case_id)
    if (
        case is None
        or _status(case) != CaseStatus.M1_SELF_FILING_PROFILE_PENDING
        or str(case.service_mode or "") != M1ServiceMode.SELF_FILING_PACKAGE.value
    ):
        await _stale(callback, db, case_id=case_id)
        return

    service = SelfFilingService(db)
    package = await service.require_package(case_id=int(case.id))
    await state.clear()
    await state.update_data(
        self_filing_case_id=int(case.id),
        self_filing_client_id=int(user.id),
    )

    if (
        not force_restart
        and package.delivery_email
        and package.email_confirmed_at is None
    ):
        await state.set_state(SelfFilingIntakeStates.waiting_email_code)
        active = _challenge_active(package)
        await db.rollback()
        if active:
            await _safe_edit(
                callback,
                _verification_text(package),
                reply_markup=_verification_markup(case_id),
            )
        else:
            await _safe_edit(
                callback,
                "✉️ Email указан, но действующего кода подтверждения уже нет.\n\n"
                f"Адрес: {_mask_email(package.delivery_email)}. "
                "Запросите новый код или измените данные. До подтверждения адреса "
                "документы и оплата не открываются.",
                reply_markup=_verification_markup(case_id),
            )
        return

    await state.set_state(SelfFilingIntakeStates.waiting_region)
    await db.rollback()
    await _safe_edit(
        callback,
        "📍 ПАКЕТ ДЛЯ САМОСТОЯТЕЛЬНОЙ ПОДАЧИ\n\n"
        "Укажите регион России, в котором вы живёте/находитесь или с которым "
        "связан спор. Например: «Республика Татарстан» или «Новосибирская область».\n\n"
        "Это не выбирает суд автоматически. Конкретную подсудность позже "
        "подтвердит юрист по документам дела.",
        reply_markup=_exit(case_id),
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
    await _begin_profile(
        callback,
        state,
        db,
        case_id=case_id,
        force_restart=False,
    )


@router.callback_query(
    lambda c: bool(c.data)
    and c.data.startswith("self_filing_profile_restart:v2:")
)
async def self_filing_profile_restart(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback.data, "self_filing_profile_restart")
    if case_id is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
        return
    await _begin_profile(
        callback,
        state,
        db,
        case_id=case_id,
        force_restart=True,
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
        "📍 Адрес для документов\n\n"
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
        "✉️ Email для профиля и доставки\n\n"
        "Укажите email, который будет сохранён в вашем профиле и на который "
        "после оплаты будет отправлен готовый судебный комплект. Мы отправим "
        "одноразовый код — без подтверждения почты документы и оплата не откроются.",
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
        "После подтверждения мы отправим одноразовый код на этот email. "
        "Только после правильного кода адрес считается подтверждённым. "
        "Суд и основание подсудности позже отдельно подтвердит юрист.",
        reply_markup=one(
            (
                "✅ Всё верно — отправить код",
                _bound("self_filing_profile_confirm", case_id),
            ),
            (
                "🔄 Ввести заново",
                _bound("self_filing_profile_restart", case_id),
            ),
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
        package = await SelfFilingService(db).save_confirmed_profile(
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
                (
                    "🔄 Ввести заново",
                    _bound("self_filing_profile_restart", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось надёжно сохранить данные и отправить код. "
            "Состояние не изменено; повторите из актуального дела.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    await state.update_data(
        self_filing_case_id=int(case.id),
        self_filing_client_id=int(user.id),
    )
    await state.set_state(SelfFilingIntakeStates.waiting_email_code)
    await _safe_edit(
        callback,
        _verification_text(package),
        reply_markup=_verification_markup(case_id),
    )


@router.callback_query(
    lambda c: bool(c.data)
    and c.data.startswith("self_filing_email_resend:v2:")
)
async def self_filing_email_resend(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    case_id = _case_id(callback.data, "self_filing_email_resend")
    if case_id is None:
        await callback.answer("Некорректная кнопка.", show_alert=True)
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
        package = await SelfFilingService(db).resend_delivery_email_verification(
            case_id=case_id,
            client_id=int(user.id),
        )
        await db.commit()
    except SelfFilingError as error:
        await db.rollback()
        await _safe_edit(
            callback,
            f"Новый код не отправлен: {error}",
            reply_markup=one(
                (
                    "✏️ Изменить email или данные",
                    _bound("self_filing_profile_restart", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        await _safe_edit(
            callback,
            "Не удалось отправить новый код. Данные обращения не изменены.",
            reply_markup=_verification_markup(case_id),
        )
        return

    await state.clear()
    await state.update_data(
        self_filing_case_id=int(case.id),
        self_filing_client_id=int(user.id),
    )
    await state.set_state(SelfFilingIntakeStates.waiting_email_code)
    await _safe_edit(
        callback,
        _verification_text(package, resent=True),
        reply_markup=_verification_markup(case_id),
    )


@router.message(SelfFilingIntakeStates.waiting_email_code)
async def self_filing_email_code(
    message: Message,
    state: FSMContext,
    db,
):
    data = await state.get_data()
    case_id = int(data.get("self_filing_case_id") or 0)
    expected_client_id = int(data.get("self_filing_client_id") or 0)
    if case_id <= 0 or expected_client_id <= 0:
        await state.clear()
        await db.rollback()
        await message.answer(
            "Сеанс подтверждения email завершился. Откройте актуальное обращение.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    if int(user.id) != expected_client_id:
        await db.rollback()
        await message.answer(
            "Код относится к другому клиентскому сеансу. Данные не изменены.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    try:
        await SelfFilingService(db).verify_delivery_email(
            case_id=case_id,
            client_id=int(user.id),
            code=str(message.text or ""),
        )
        await db.commit()
    except SelfFilingEmailVerificationError as error:
        if error.persist_state:
            await db.commit()
        else:
            await db.rollback()
        await message.answer(
            f"⚠️ {error}",
            reply_markup=_verification_markup(case_id),
        )
        return
    except SelfFilingError as error:
        await db.rollback()
        await message.answer(
            f"Email не подтверждён: {error}",
            reply_markup=_verification_markup(case_id),
        )
        return
    except Exception:
        await db.rollback()
        await message.answer(
            "Не удалось проверить код. Состояние не изменено; повторите действие.",
            reply_markup=_verification_markup(case_id),
        )
        return

    await state.clear()
    await message.answer(
        "✅ Email подтверждён.\n\n"
        "Теперь загрузите:\n"
        "• ДДУ;\n"
        "• паспорт или иной документ, удостоверяющий личность;\n"
        "• все приложения и дополнительные соглашения к ДДУ;\n"
        "• иные документы по спору, которые попросит юрист.\n\n"
        "Оплата 15 000 ₽ появится только после того, как юрист подтвердит "
        "полноту исходных документов и конкретную подсудность. После подтверждения "
        "оплаты готовый результат будет отправлен на этот email в течение "
        "3 календарных дней. В судебный комплект входят ровно четыре документа: "
        "претензия, исковое заявление, расчёт суммы иска и дорожная карта клиента.",
        reply_markup=one(
            ("📄 Загрузить документы", "documents_upload_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


__all__ = ["router"]
