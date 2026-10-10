import logging

from aiogram import Router
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramNetworkError,
    TelegramServerError,
)
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.payment import Payment

router = Router()
logger = logging.getLogger(__name__)


def _status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


async def _case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _present_committed_result(
    callback: CallbackQuery,
    text: str,
    *,
    reply_markup,
) -> None:
    """Show a durable M1 result without converting Telegram UI failure into write failure."""

    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
        return
    except TelegramBadRequest as error:
        if "message is not modified" in str(error).lower():
            return
        logger.warning("Не удалось обновить M1-экран после сохранения: %s", error)
    except (TelegramNetworkError, TelegramServerError) as error:
        logger.warning("Telegram недоступен после сохранения M1-действия: %s", error)

    try:
        await callback.message.answer(text, reply_markup=reply_markup)
    except (TelegramBadRequest, TelegramNetworkError, TelegramServerError) as error:
        # The database transaction is already committed. Do not invite the user
        # to repeat a legal/process mutation merely because Telegram is stale.
        logger.warning("Не удалось показать сохранённый M1-результат: %s", error)


async def _show_stale_stage(
    callback: CallbackQuery,
    text: str,
    *,
    include_documents: bool = False,
) -> None:
    buttons: list[tuple[str, str]] = []
    if include_documents:
        buttons.append(("📄 Документы", "documents_open"))
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await _present_committed_result(
        callback,
        text,
        reply_markup=one(*buttons),
    )


@router.callback_query(lambda c: c.data == "contract_open")
async def contract_open(callback: CallbackQuery):
    await callback.message.edit_text(
        "📝 Договор\n\n"
        "Проверьте реквизиты, объём услуг, стоимость и порядок взаимодействия "
        "в договоре, который направила команда.\n\n"
        "Кнопка ниже фиксирует ваше подтверждение в системе, но не заменяет "
        "обязательную форму подписи, если она предусмотрена договором.",
        reply_markup=one(
            ("Подтвердить договор", "contract_sign"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📄 Документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "contract_sign")
async def contract_sign(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Откройте актуальную карточку перед новым действием.",
        )
        return

    status = _status(case)
    if status not in {
        CaseStatus.M1_CONTRACT_READY,
        CaseStatus.M1_WAITING_PAYMENT_30000,
    }:
        await _show_stale_stage(
            callback,
            "Эта кнопка договора больше не соответствует текущему этапу. "
            "Дело не изменено — откройте актуальный следующий шаг.",
            include_documents=True,
        )
        return

    service = PaymentService(db)
    try:
        if status == CaseStatus.M1_CONTRACT_READY:
            if case.contract_signed_at is None:
                case.contract_signed_at = datetime.now(timezone.utc)
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_PAYMENT_30000,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент подтвердил договор",
            )
        payment = await service.get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        )
        await db.commit()
    except (RuntimeError, ValueError) as error:
        await db.rollback()
        await _show_stale_stage(
            callback,
            f"Подтверждение договора пока не сохранено: {error}\n\n"
            "Проверьте актуальный этап и повторите действие только после обновления карточки.",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить договор M1")
        await _show_stale_stage(
            callback,
            "Подтверждение договора временно не сохранено. Данные дела не изменены.",
        )
        return

    from app.bot.screens.payments import money

    if payments_disabled():
        text = (
            "✅ Подтверждение договора сохранено.\n\n"
            f"Первый платёж: {money(payment.amount)}. Онлайн-оплата сейчас отключена, "
            "поэтому этап подтвердит команда после фактической фиксации платежа. "
            "До этого доверенность не откроется автоматически."
        )
        markup = one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "✅ Подтверждение договора сохранено.\n\n"
            f"Следующий шаг — первый платёж {money(payment.amount)}."
        )
        markup = one(
            (f"💳 Оплатить {money(payment.amount)}", "pay_start_30000"),
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    await _present_committed_result(callback, text, reply_markup=markup)


@router.callback_query(lambda c: c.data == "poa_instruction")
async def poa_instruction(callback: CallbackQuery, db):
    ctx, _user, case = await _case(callback, db)
    if case and _status(case) in {
        CaseStatus.M1_PAYMENT_30000_RECEIVED,
        CaseStatus.M1_POWER_OF_ATTORNEY,
    }:
        if case.poa_instruction_sent_at is None:
            case.poa_instruction_sent_at = datetime.now(timezone.utc)
            await db.commit()
    await callback.message.edit_text(
        "📑 Доверенность\n\n"
        "Оформите доверенность и нотариальные копии по инструкции юриста. "
        "После оформления подтвердите готовность — команда проверит документы "
        "до подготовки и отправки претензии.",
        reply_markup=one(
            ("Доверенность оформлена", "poa_done"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📄 Добавить документ", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "poa_done")
async def poa_done(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Подтверждение доверенности не выполнялось.",
            include_documents=True,
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_POWER_OF_ATTORNEY:
        try:
            if case.poa_received_at is None:
                case.poa_received_at = datetime.now(timezone.utc)
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_POA_RECEIVED,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент сообщил об оформлении доверенности",
            )
            await db.commit()
        except ValueError as error:
            await db.rollback()
            await _show_stale_stage(
                callback,
                f"Подтверждение доверенности не сохранено: {error}",
                include_documents=True,
            )
            return
        except Exception:
            await db.rollback()
            logger.exception("Не удалось сохранить подтверждение доверенности")
            await _show_stale_stage(
                callback,
                "Подтверждение доверенности временно не сохранено. "
                "Данные дела не изменены.",
                include_documents=True,
            )
            return

        await _present_committed_result(
            callback,
            "✅ Сообщение об оформлении доверенности сохранено.\n\n"
            "Теперь юрист проверит документы. Отправка претензии и начало "
            "30-дневного срока будут зафиксированы только после фактического действия.",
            reply_markup=one(
                ("📄 Документы", "documents_open"),
                ("📁 Моё дело", "my_case_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status in {
        CaseStatus.M1_POA_RECEIVED,
        CaseStatus.M1_CLAIM_PREPARATION,
        CaseStatus.M1_CLAIM_SENT,
        CaseStatus.M1_WAITING_30_DAYS,
        CaseStatus.M1_LAWSUIT_PREPARATION,
        CaseStatus.M1_LAWSUIT_FILED,
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_DECISION_RECEIVED,
        CaseStatus.M1_WAITING_PAYMENT_70000,
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }:
        await _show_stale_stage(
            callback,
            "Доверенность уже отмечена. Повторная запись не создавалась — "
            "откройте актуальный этап дела.",
            include_documents=True,
        )
        return

    await _show_stale_stage(
        callback,
        "Подтверждение доверенности недоступно на текущем этапе. Дело не изменено.",
        include_documents=True,
    )


@router.callback_query(lambda c: c.data == "court_status")
async def court_status(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Откройте актуальную карточку.",
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_WAITING_30_DAYS:
        deadline = (
            case.claim_waiting_until.astimezone(timezone.utc).strftime("%d.%m.%Y")
            if case.claim_waiting_until
            else None
        )
        deadline_text = (
            f" Контрольная дата: {deadline}."
            if deadline
            else " Контрольная дата уточняется юридической командой."
        )
        text = (
            "⏳ Идёт контрольный срок после отправки претензии.\n\n"
            + deadline_text
            + "\n\nПросмотр этого экрана ничего не меняет. После истечения 30 дней "
            "юрист сможет начать подготовку иска."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_LAWSUIT_PREPARATION:
        text = (
            "⚖️ Юрист готовит иск.\n\n"
            "От вас сейчас не требуется отдельного действия. После фактической "
            "подачи иска этап будет обновлён в истории дела."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_LAWSUIT_FILED:
        text = (
            "⚖️ Иск подан.\n\n"
            "Ожидаем начала судебного этапа. Значимые события фиксируются "
            "юристом и появляются в истории дела."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_COURT_STAGE:
        text = (
            "🏛 Судебный этап открыт юристом.\n\n"
            "Значимые события будут появляться в истории дела. Второй платёж "
            "станет доступен только после получения и фиксации решения суда."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_DECISION_RECEIVED:
        text = (
            "✅ Решение суда получено и зафиксировано.\n\n"
            "Система готовит следующий договорный платёж. До перехода дела в "
            "платёжный этап кнопка оплаты не показывается."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    elif status == CaseStatus.M1_WAITING_PAYMENT_70000:
        if payments_disabled():
            text = (
                "💳 Второй платёж открыт после судебного решения.\n\n"
                "Онлайн-оплата сейчас отключена. Команда зафиксирует фактический "
                "платёж и только после этого откроет исполнительный этап."
            )
            buttons = (
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            )
        else:
            text = (
                "💳 Второй платёж открыт после судебного решения.\n\n"
                "После подтверждения оплаты система откроет исполнительный этап."
            )
            buttons = (
                ("💳 Перейти к оплате", "pay_court_70000"),
                ("🕘 История", "case_history_open"),
                ("✉️ Задать вопрос команде", "message_create"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            )
    elif status in {
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        CaseStatus.M1_CLOSED,
    }:
        text = "Судебный платёжный этап уже пройден. Откройте текущее состояние дела."
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("🕘 История", "case_history_open"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = "Судебный этап ещё не открыт для текущего состояния дела."
        buttons = (
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "pay_court_70000")
async def pay_court(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Платёж не создавался.",
        )
        return
    if _status(case) != CaseStatus.M1_WAITING_PAYMENT_70000:
        await _show_stale_stage(
            callback,
            "Эта кнопка оплаты больше не соответствует текущему этапу. "
            "Новый платёж не создавался.",
        )
        return
    if payments_disabled():
        await _show_stale_stage(
            callback,
            "Онлайн-оплата сейчас отключена. Второй платёж фиксирует команда; "
            "исполнительный этап откроется только после подтверждения.",
        )
        return

    from app.bot.screens.payments import start_payment

    await start_payment(callback, db, PaymentCode.M1_COURT_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def pay_success_fee(callback: CallbackQuery, db):
    ctx, _user, case = await _case(callback, db)
    if not case:
        await _show_stale_stage(
            callback,
            "Активное дело не найдено. Финальный платёж не создавался.",
        )
        return

    status = _status(case)
    if status == CaseStatus.M1_ENFORCEMENT:
        await _show_stale_stage(
            callback,
            "Финальный платёж ещё не рассчитан: сначала команда должна "
            "зафиксировать фактическое получение денег.",
        )
        return
    if status not in {
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
    }:
        await _show_stale_stage(
            callback,
            "Эта кнопка финального платежа больше не соответствует текущему этапу. "
            "Новый платёж не создавался.",
        )
        return

    existing = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
    ).scalars().first()
    service = PaymentService(db)
    try:
        amount = await service.estimate_success_fee_for_case(case.id)
        if existing and existing.amount != amount:
            raise ValueError(
                "Сумма существующего финального платежа не соответствует "
                "фактическому поступлению. Требуется ручная проверка."
            )
        if status == CaseStatus.M1_MONEY_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_SUCCESS_FEE,
                actor_type="system",
                actor_id=None,
                comment="Открыт финальный договорный платёж после получения денег",
            )
        if existing:
            payment = existing
            case.success_fee_amount = amount
        else:
            payment = await service.get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M1_SUCCESS_FEE,
                amount=amount,
            )
        if not payments_disabled():
            payment = await service.create_payment_link(payment)
        await db.commit()
    except (RuntimeError, ValueError) as error:
        await db.rollback()
        await _show_stale_stage(
            callback,
            f"Финальный платёж пока не открыт: {error}\n\n"
            "Данные дела сохранены. Проверьте актуальный этап перед повтором.",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось открыть финальный платёж M1")
        await _show_stale_stage(
            callback,
            "Финальный платёж временно не открыт. Данные дела не изменены.",
        )
        return

    from app.bot.screens.payments import money, payment_keyboard

    if payments_disabled():
        text = (
            "💳 Финальный платёж открыт.\n\n"
            f"Сумма: {money(payment.amount)}\n\n"
            "Онлайн-оплата сейчас отключена. Команда подтвердит фактический платёж; "
            "дело будет закрыто только после подтверждения финансового этапа."
        )
        markup = one(
            ("📁 Моё дело", "my_case_open"),
            ("✉️ Задать вопрос команде", "message_create"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "💳 Финальный платёж\n\n"
            f"Сумма: {money(payment.amount)}\n\n"
            "После подтверждения оплаты система завершит финансовый этап и закроет дело."
        )
        markup = payment_keyboard(payment)
    await _present_committed_result(callback, text, reply_markup=markup)
