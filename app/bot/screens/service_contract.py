from __future__ import annotations

import logging

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError, TelegramServerError
from aiogram.types import BufferedInputFile, CallbackQuery
from sqlalchemy import select

from app.bot.case_callback_scope import bound_case_callback, callback_matches_action
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


def _contract_confirm_callback(case_id: int, document_id: int, version: int) -> str:
    return f"contract_confirm:v2:{int(case_id)}:{int(document_id)}:{int(version)}"


def _parse_contract_confirmation(data: str | None) -> tuple[int | None, int, int] | None:
    """Return (expected_case_id, document_id, version).

    Historical callbacks encoded only document id + version. They remain
    read-compatible and still fail if that exact document is not current for the
    selected Case. Fresh screens carry Case id as well, so they can never be
    reinterpreted after the client switches cabinet context.
    """

    value = str(data or "")
    try:
        if value.startswith("contract_confirm:v2:"):
            parts = value.split(":")
            if len(parts) != 5:
                return None
            case_id = int(parts[2])
            document_id = int(parts[3])
            version = int(parts[4])
            if case_id <= 0 or document_id <= 0 or version <= 0:
                return None
            return case_id, document_id, version
        if value.startswith("contract_confirm:"):
            _, document_id_raw, version_raw = value.split(":", 2)
            document_id = int(document_id_raw)
            version = int(version_raw)
            if document_id <= 0 or version <= 0:
                return None
            return None, document_id, version
    except (TypeError, ValueError):
        return None
    return None


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

    case_id = int(case.id)
    case_number = str(case.case_number)
    document = await current_service_contract(db, case_id=case_id)
    if document is None:
        await _show(
            callback,
            "📝 ДОГОВОР ГОТОВИТСЯ\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Юрист уже принял дело, но проверенная версия договора ещё не опубликована. Подтверждение и первый платёж заблокированы до появления конкретного файла.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Дождитесь публикации договора. Если срок затягивается — напишите команде.",
            buttons=(
                (
                    "✉️ Задать вопрос команде",
                    bound_case_callback("message_create", case_id),
                ),
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
                f"Обращение № {case_number}\n"
                f"Версия: {int(document.version or 1)}\n"
                f"Контроль: {str(document.sha256 or '')[:12]}…"
            ),
        )
    except Exception:
        logger.exception("Не удалось выдать клиенту защищённую версию договора")
        await _show(
            callback,
            "⚠️ ДОГОВОР ВРЕМЕННО НЕДОСТУПЕН\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Договор опубликован, но файл сейчас не удалось безопасно открыть. Подтверждение заблокировано.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Повторите выдачу позже или напишите команде.",
            buttons=(
                ("🔄 Повторить открытие", "contract_open"),
                (
                    "✉️ Задать вопрос команде",
                    bound_case_callback("message_create", case_id),
                ),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    if str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value:
        await _show(
            callback,
            "✅ ДОГОВОР ПОДТВЕРЖДЁН\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Эта версия договора уже подтверждена; повторное подтверждение не требуется.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Перейдите к первому платежу.",
            buttons=(
                (
                    "💳 Перейти к оплате",
                    bound_case_callback("pay_start_30000", case_id),
                ),
                ("💳 Все оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    document_id = int(document.id)
    version = int(document.version or 1)
    file_name = str(document.file_name)
    await _show(
        callback,
        "📝 ДОГОВОР ОТКРЫТ\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        f"Вы получили версию {version} файла «{file_name}». Проверьте реквизиты, объём услуг, стоимость и условия.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        "Подтвердите именно эту редакцию после проверки. Если команда опубликует новую версию, старая кнопка перестанет работать.\n\n"
        "Подтверждение в Telegram фиксируется в истории, но не заявляется как квалифицированная электронная подпись.",
        buttons=(
            (
                "✅ Подтвердить эту версию",
                _contract_confirm_callback(case_id, document_id, version),
            ),
            (
                "✉️ Задать вопрос команде",
                bound_case_callback("message_create", case_id),
            ),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: callback_matches_action(c.data, "contract_sign"))
async def legacy_contract_confirmation(callback: CallbackQuery, db):
    """Historical generic or Case-only buttons may not confirm a document.

    Older M1 screens emitted ``contract_sign`` or ``contract_sign:v2:<case>``.
    Neither identifies the exact published document/version. This early router
    deliberately consumes both forms before the legacy M1 mutator mounted later.
    """

    _ctx, _user, case = await _context(callback, db)
    if not case:
        await _stale(callback, "Активное дело не найдено. Старая кнопка не выполнила действие.")
        return
    await _show(
        callback,
        "Перед подтверждением нужно открыть текущую версию договора. "
        "Старая кнопка без идентификатора документа и номера версии не может подтвердить договор или создать платёж.",
        buttons=(
            ("📝 Открыть актуальный договор", "contract_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: str(c.data or "").startswith("contract_confirm:"))
async def confirm_exact_service_contract(callback: CallbackQuery, db):
    parsed = _parse_contract_confirmation(callback.data)
    if parsed is None:
        await _stale(callback, "Кнопка договора повреждена. Откройте актуальную версию заново.")
        return
    expected_case_id, document_id, version = parsed

    _ctx, user, case = await _context(callback, db)
    if not case:
        await _stale(callback, "Активное дело не найдено. Подтверждение не выполнено.")
        return

    case_id = int(case.id)
    case_number = str(case.case_number)
    if expected_case_id is not None and expected_case_id != case_id:
        await db.rollback()
        await _show(
            callback,
            "Эта кнопка договора относится к другому обращению. Подтверждение и платёж не выполнены.",
            buttons=(
                ("📁 Выбрать обращение", "my_cases_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if str(case.status) == CaseStatus.M1_WAITING_PAYMENT_30000.value:
        payment = await _existing_initial_payment(db, case_id)
        if payment is None:
            await _stale(
                callback,
                "Договор уже подтверждён, но платёж не найден. Повторное подтверждение заблокировано — напишите команде.",
            )
            return
        await _show_payment_result(
            callback,
            payment,
            case_id=case_id,
            case_number=case_number,
        )
        return
    if str(case.status) != CaseStatus.M1_CONTRACT_READY.value:
        await _stale(
            callback,
            "Этап договора уже изменился. Старая версия не подтверждалась и дело не изменено.",
        )
        return

    current = await current_service_contract(db, case_id=case_id)
    if current is None or int(current.id) != document_id or int(current.version or 1) != version:
        await _show(
            callback,
            "⚠️ ВЕРСИЯ ДОГОВОРА ИЗМЕНИЛАСЬ\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Открытая ранее версия больше не является актуальной. Никакой платёж не создан.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            "Откройте новую редакцию и проверьте её перед подтверждением.",
            buttons=(
                ("📝 Открыть новую версию", "contract_open"),
                (
                    "✉️ Задать вопрос команде",
                    bound_case_callback("message_create", case_id),
                ),
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
        payment_amount = payment.amount
        await db.commit()
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

    # Do not carry ORM objects across the transaction boundary just for UI.
    await _show_payment_result(
        callback,
        None,
        case_id=case_id,
        case_number=case_number,
        payment_amount=payment_amount,
    )


async def _show_payment_result(
    callback: CallbackQuery,
    payment: Payment | None,
    *,
    case_id: int,
    case_number: str,
    payment_amount=None,
) -> None:
    from app.bot.screens.payments import money

    amount = payment_amount if payment_amount is not None else payment.amount
    if payments_disabled():
        await _show(
            callback,
            "✅ ДОГОВОР ПОДТВЕРЖДЁН\n"
            f"Обращение № {case_number}\n\n"
            "СЕЙЧАС\n"
            "Конкретная версия договора сохранена в истории.\n\n"
            "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
            f"Первый платёж: {money(amount)}. Онлайн-оплата отключена; этап продолжится только после фактической проверки поступления командой.",
            buttons=(
                ("💳 Оплаты", "payments_open"),
                ("📁 Моё дело", "my_case_open"),
                (
                    "✉️ Задать вопрос команде",
                    bound_case_callback("message_create", case_id),
                ),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await _show(
        callback,
        "✅ ДОГОВОР ПОДТВЕРЖДЁН\n"
        f"Обращение № {case_number}\n\n"
        "СЕЙЧАС\n"
        "Конкретная версия договора сохранена в истории.\n\n"
        "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n"
        f"Оплатите первый платёж {money(amount)}.",
        buttons=(
            (
                f"💳 Оплатить {money(amount)}",
                bound_case_callback("pay_start_30000", case_id),
            ),
            ("💳 Все оплаты", "payments_open"),
            ("📁 Моё дело", "my_case_open"),
            (
                "✉️ Задать вопрос команде",
                bound_case_callback("message_create", case_id),
            ),
            ("🏠 Главная", "nav_home"),
        ),
    )
