from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.models.payment import Payment

router = Router()


def _money(value):
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def _status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


async def _case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


@router.callback_query(lambda c: c.data == "consent_open")
async def consent_open(callback: CallbackQuery):
    await callback.message.edit_text(
        "📄 Согласие на обработку персональных данных\n\n"
        "Для загрузки документов и передачи их юристу нужно подтвердить согласие.\n\n"
        "Подтверждая согласие, вы разрешаете обработку данных и документов "
        "только в рамках обращения.",
        reply_markup=one(
            ("Согласен", "consent_accept"),
            ("Не согласен", "consent_decline"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_accept")
async def consent_accept(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if not case:
        case = await ctx.get_or_create_active_case_for_user(user)
    try:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_DOCUMENTS_PENDING,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил согласие на обработку персональных данных",
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "✅ Согласие сохранено. Следующий шаг — загрузить документы по делу.",
        reply_markup=one(
            ("📄 Перейти к документам", "documents_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consent_decline")
async def consent_decline(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if case:
        try:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.CALCULATED,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент не подтвердил согласие на обработку данных",
            )
            await db.commit()
        except ValueError as error:
            await db.rollback()
            await callback.answer(str(error), show_alert=True)
            return
    await callback.message.edit_text(
        "Без согласия мы не можем принять документы на проверку. "
        "Расчёт сохранён, к нему можно вернуться позже.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
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
            ("💬 Задать вопрос", "message_create"),
            ("📄 Документы", "documents_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "contract_sign")
async def contract_sign(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    status = _status(case)
    if status not in {
        CaseStatus.M1_CONTRACT_READY,
        CaseStatus.M1_WAITING_PAYMENT_30000,
    }:
        await callback.message.edit_text(
            "Этот договор уже подтверждён либо текущий этап изменился.",
            reply_markup=one(("📁 Мое дело", "my_case_open")),
        )
        return
    try:
        if status == CaseStatus.M1_CONTRACT_READY:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_PAYMENT_30000,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент подтвердил договор",
            )
        payment = await PaymentService(db).get_or_create_payment(
            case=case,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "✅ Подтверждение договора сохранено.\n\n"
        "Следующий шаг — первый платёж по договору.",
        reply_markup=one(
            (f"Оплатить {_money(payment.amount)}", "pay_start_30000"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "poa_instruction")
async def poa_instruction(callback: CallbackQuery):
    await callback.message.edit_text(
        "📑 Доверенность\n\n"
        "Оформите доверенность и нотариальные копии по инструкции юриста. "
        "После оформления подтвердите готовность — команда проверит документы "
        "до подготовки и отправки претензии.",
        reply_markup=one(
            ("Доверенность оформлена", "poa_done"),
            ("💬 Задать вопрос", "message_create"),
            ("📄 Добавить документ", "documents_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "poa_done")
async def poa_done(callback: CallbackQuery, db):
    ctx, user, case = await _case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    status = _status(case)
    if status == CaseStatus.M1_POWER_OF_ATTORNEY:
        try:
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
            await callback.answer(str(error), show_alert=True)
            return
        text = (
            "✅ Сообщение об оформлении доверенности сохранено.\n\n"
            "Теперь юрист проверит документы. Отправка претензии и начало "
            "30-дневного срока будут зафиксированы только после фактического действия."
        )
    elif status in {
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
    }:
        text = "Доверенность уже отмечена. Текущий этап дела не изменён."
    else:
        await callback.message.edit_text(
            "Подтверждение доверенности недоступно на текущем этапе.",
            reply_markup=one(("📁 Мое дело", "my_case_open")),
        )
        return
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("💬 Задать вопрос", "message_create"),
        ),
    )


@router.callback_query(lambda c: c.data == "court_status")
async def court_status(callback: CallbackQuery, db):
    _ctx, _user, case = await _case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    status = _status(case)
    if status == CaseStatus.M1_WAITING_30_DAYS:
        text = (
            "⏳ Идёт контрольный срок после отправки претензии.\n\n"
            "Просмотр этого экрана не открывает судебный этап. "
            "Юрист обновит статус после истечения срока и принятия решения."
        )
        buttons = (
            ("🕘 История", "case_history_open"),
            ("💬 Задать вопрос", "message_create"),
            ("📁 Мое дело", "my_case_open"),
        )
    elif status in {
        CaseStatus.M1_COURT_STAGE,
        CaseStatus.M1_WAITING_PAYMENT_70000,
    }:
        text = (
            "🏛 Судебный этап открыт юристом. Значимые события и документы "
            "появятся в истории дела."
        )
        buttons = (
            ("Перейти к договорному этапу", "pay_court_70000"),
            ("🕘 История", "case_history_open"),
            ("💬 Задать вопрос", "message_create"),
            ("📁 Мое дело", "my_case_open"),
        )
    elif status in {
        CaseStatus.M1_PAYMENT_70000_RECEIVED,
        CaseStatus.M1_ENFORCEMENT,
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
        CaseStatus.M1_SUCCESS_FEE_RECEIVED,
    }:
        text = "Судебный договорный этап уже пройден. Откройте текущее состояние дела."
        buttons = (
            ("📁 Мое дело", "my_case_open"),
            ("🕘 История", "case_history_open"),
        )
    else:
        text = "Судебный этап ещё не открыт для текущего статуса."
        buttons = (("📁 Мое дело", "my_case_open"),)
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "pay_court_70000")
async def pay_court(callback: CallbackQuery, db):
    from app.bot.screens.payments import start_payment

    await start_payment(callback, db, PaymentCode.M1_COURT_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def pay_success_fee(callback: CallbackQuery, db):
    ctx, _user, case = await _case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    status = _status(case)
    if status == CaseStatus.M1_ENFORCEMENT:
        await callback.message.edit_text(
            "Финальный платёж ещё не рассчитан: сначала команда должна "
            "зафиксировать фактическое получение денег.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("💬 Задать вопрос", "message_create"),
            ),
        )
        return
    if status not in {
        CaseStatus.M1_MONEY_RECEIVED,
        CaseStatus.M1_WAITING_SUCCESS_FEE,
    }:
        await callback.message.edit_text(
            "Финальный платёж недоступен на текущем этапе.",
            reply_markup=one(("📁 Мое дело", "my_case_open")),
        )
        return

    existing = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE)
        )
    ).scalars().first()
    service = PaymentService(db)
    try:
        if status == CaseStatus.M1_MONEY_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_SUCCESS_FEE,
                actor_type="system",
                actor_id=None,
                comment="Рассчитан финальный договорный платёж",
            )
        if existing:
            payment = existing
        else:
            amount = await service.estimate_success_fee_for_case(case.id)
            payment = await service.get_or_create_payment(
                case=case,
                payment_code=PaymentCode.M1_SUCCESS_FEE,
                amount=amount,
            )
        payment = await service.create_payment_link(payment)
        await db.commit()
    except (RuntimeError, ValueError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Платёжная ссылка не создана: {error}",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("💬 Связаться с юристом", "contact_lawyer"),
            ),
        )
        return

    from app.bot.screens.payments import payment_keyboard

    await callback.message.edit_text(
        f"💳 Финальный платёж\n\nСумма: {_money(payment.amount)}\n\n"
        "После подтверждения оплаты система завершит финансовый этап.",
        reply_markup=payment_keyboard(payment),
    )
