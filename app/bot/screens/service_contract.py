from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.service_contract import (
    confirm_service_contract,
    current_service_contract,
)
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.payment import Payment
from app.storage import LocalStorageService

router = Router()
logger = logging.getLogger(__name__)


async def _context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _show(callback: CallbackQuery, text: str, *, buttons) -> None:
    markup = one(*buttons)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        await callback.message.answer(text, reply_markup=markup)
    except (TelegramNetworkError, TelegramServerError):
        logger.warning("Не удалось показать экран договора клиенту")


async def _existing_initial_payment(db, case_id: int) -> Payment | None:
    return (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == int(case_id))
            .where(Payment.payment_code == PaymentCode.M1_INITIAL_PAYMENT)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .limit(1)
        )
    ).scalars().first()


async def _stale(callback: CallbackQuery, text: str) -> None:
    await _show(
        callback,
        text,
        buttons=(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "contract_open")
async def open_service_contract(callback: CallbackQuery, db):
    _ctx, _user, case = await _context(callback, db)
    if not case:
        await _stale(callback, "Активное дело не найдено. Откройте актуальную карточку.")
        return
    if str(case.status) not in {
        CaseStatus.M1_CONTRACT_READY.value,
        CaseStatus.M1_WAITING_PAYMENT_30000.value,
    }:
        await _stale(
            callback,
            "Договорный этап уже изменился. Старая кнопка ничего не меняет — откройте актуальное дело.",
        )
        return

    document = await current_service_contract(db, case_id=case.id)
    if document is None:
        await _show(
            callback,
            "📝 Договор готовится\n\n"
            "Юрист уже принял дело, но проверенная версия договора ещё не опубликована. "
            "Подтверждение и первый платёж заблокированы до появления конкретного файла.\n\n"
            "Когда договор будет опубликован, бот сообщит об этом. Если срок затягивается — напишите команде.",
            buttons=(
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        payload = LocalStorageService().read_document_bytes(
            document.file_path,
            expected_sha256=document.sha256,
            encryption_key_id=document.encryption_key_id,
            encryption_envelope_id=document.encryption_envelope_id,
            encrypted_data_key=document.encrypted_data_key,
            encrypted_data_key_nonce=document.encrypted_data_key_nonce,
        )
        await callback.message.answer_document(
            BufferedInputFile(payload, filename=document.file_name),
            caption=(
                f"📝 {document.title}\n"
                f"Версия: {int(document.version or 1)}\n"
                f"Контроль: {str(document.sha256 or '')[:12]}…"
            ),
        )
    except Exception:
        logger.exception("Не удалось выдать клиенту защищённую версию договора")
        await _show(
            callback,
            "⚠️ Договор опубликован, но файл сейчас не удалось безопасно открыть. "
            "Подтверждение заблокировано — повторите выдачу позже или напишите команде.",
            buttons=(
                ("🔄 Повторить открытие", "contract_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    if str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value:
        await _show(
            callback,
            "✅ Эта версия договора уже подтверждена.\n\n"
            "Повторное подтверждение не требуется. Следующий шаг — первый платёж.",
            buttons=(
                ("💳 Перейти к оплате", "pay_start_30000"),
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _show(
        callback,
        "📝 Договор открыт\n\n"
        f"Вы получили версию {int(document.version or 1)} файла «{document.file_name}». "
        "Проверьте реквизиты, объём услуг, стоимость и условия.\n\n"
        "Кнопка подтверждения будет привязана именно к этой версии. Если команда опубликует новую редакцию, "
        "старая кнопка перестанет работать. Подтверждение в Telegram фиксируется в истории, но не заявляется "
        "как квалифицированная электронная подпись.",
        buttons=(
            (
                "✅ Подтвердить эту версию",
                f"contract_confirm:{int(document.id)}:{int(document.version or 1)}",
            ),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "contract_sign")
async def legacy_contract_confirmation(callback: CallbackQuery, db):
    """Old generic buttons may not confirm a contract they did not identify."""

    _ctx, _user, case = await _context(callback, db)
    if not case:
        await _stale(callback, "Активное дело не найдено. Старая кнопка не выполнила действие.")
        return
    await _show(
        callback,
        "Перед подтверждением нужно открыть текущую версию договора. "
        "Старая кнопка без номера версии не может создать платёж.",
        buttons=(
            ("📝 Открыть актуальный договор", "contract_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: str(c.data or "").startswith("contract_confirm:"))
async def confirm_exact_service_contract(callback: CallbackQuery, db):
    _ctx, user, case = await _context(callback, db)
    if not case:
        await _stale(callback, "Активное дело не найдено. Подтверждение не выполнено.")
        return
    try:
        _, document_id_raw, version_raw = str(callback.data).split(":", 2)
        document_id = int(document_id_raw)
        version = int(version_raw)
    except (TypeError, ValueError):
        await _stale(callback, "Кнопка договора повреждена. Откройте актуальную версию заново.")
        return

    if str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value:
        payment = await _existing_initial_payment(db, case.id)
        if payment is None:
            await _stale(
                callback,
                "Договор уже подтверждён, но платёж не найден. Повторное подтверждение заблокировано — напишите команде.",
            )
            return
        await _show_payment_result(callback, payment)
        return
    if str(case.status) != CaseStatus.M1_CONTRACT_READY.value:
        await _stale(
            callback,
            "Этап договора уже изменился. Старая версия не подтверждалась и дело не изменено.",
        )
        return

    current = await current_service_contract(db, case_id=case.id)
    if current is None or int(current.id) != document_id or int(current.version or 1) != version:
        await _show(
            callback,
            "⚠️ Версия договора изменилась после открытия.\n\n"
            "Никакой платёж не создан. Откройте новую редакцию и проверьте её перед подтверждением.",
            buttons=(
                ("📝 Открыть новую версию", "contract_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    try:
        payment = await confirm_service_contract(
            db,
            case=case,
            client_id=user.id,
            document=current,
        )
        await db.commit()
        await db.refresh(payment)
    except ValueError as error:
        await db.rollback()
        await _show(
            callback,
            f"Подтверждение не сохранено: {error}",
            buttons=(
                ("📝 Открыть актуальный договор", "contract_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить конкретную версию договора")
        await _stale(
            callback,
            "Подтверждение временно не сохранено. Дело и платёж не изменены; повторите после обновления.",
        )
        return

    await _show_payment_result(callback, payment)


async def _show_payment_result(callback: CallbackQuery, payment: Payment) -> None:
    from app.bot.screens.payments import money

    if payments_disabled():
        await _show(
            callback,
            "✅ Конкретная версия договора подтверждена и сохранена в истории.\n\n"
            f"Первый платёж: {money(payment.amount)}. Онлайн-оплата отключена; этап продолжится "
            "только после фактической проверки поступления командой.",
            buttons=(
                ("💳 Оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _show(
        callback,
        "✅ Конкретная версия договора подтверждена и сохранена в истории.\n\n"
        f"Следующий шаг — первый платёж {money(payment.amount)}.",
        buttons=(
            (f"💳 Оплатить {money(payment.amount)}", "pay_start_30000"),
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ),
    )
