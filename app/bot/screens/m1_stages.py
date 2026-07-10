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


@router.callback_query(lambda c: c.data == "consent_open")
async def consent_open(callback: CallbackQuery):
    await callback.message.edit_text(
        "📄 Согласие на обработку персональных данных\n\n"
        "Для загрузки документов и передачи их юристу нужно подтвердить согласие.\n\n"
        "Подтверждая согласие, вы разрешаете обработку данных и документов в рамках обращения.",
        reply_markup=one(("Согласен", "consent_accept"), ("Не согласен", "consent_decline"), ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consent_accept")
async def consent_accept(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        case = await ctx.get_or_create_active_case_for_user(user)
    await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_DOCUMENTS_PENDING, actor_type="client", actor_id=user.id, force=True, comment="Клиент подтвердил согласие на обработку ПД")
    await db.commit()
    await callback.message.edit_text(
        "✅ Согласие сохранено.\n\nСледующий шаг — загрузить документы по делу.",
        reply_markup=one(("📄 Перейти к документам", "documents_open"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data == "consent_decline")
async def consent_decline(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await ctx.case_service.change_status(case=case, next_status=CaseStatus.CALCULATED, actor_type="client", actor_id=user.id, force=True, comment="Клиент не подтвердил согласие на обработку ПД")
        await db.commit()
    await callback.message.edit_text(
        "Без согласия мы не можем принять документы на проверку.\n\nРасчет сохранен, к нему можно вернуться позже.",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "contract_open")
async def contract_open(callback: CallbackQuery):
    await callback.message.edit_text(
        "📝 Договор\n\n"
        "Дело принято в работу. В MVP договор отображается как экран подтверждения. "
        "Файл договора можно будет подключить через админку в шаблонах.",
        reply_markup=one(("Подписать договор", "contract_sign"), ("💬 Задать вопрос", "message_create"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data == "contract_sign")
async def contract_sign(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text("Нет активного дела.", reply_markup=one(("🏠 Главная", "nav_home")))
        return
    await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_WAITING_PAYMENT_30000, actor_type="client", actor_id=user.id, force=True, comment="Клиент подписал договор")
    payment = await PaymentService(db).get_or_create_payment(case=case, payment_code=PaymentCode.M1_INITIAL_PAYMENT)
    await db.commit()
    await callback.message.edit_text(
        "✅ Договор подписан.\n\nТеперь нужно оплатить первый платеж по договору.",
        reply_markup=one((f"Оплатить {_money(payment.amount)}", "pay_start_30000"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data == "poa_instruction")
async def poa_instruction(callback: CallbackQuery):
    await callback.message.edit_text(
        "📑 Доверенность\n\n"
        "Подготовьте доверенность и нотариальные копии документов по инструкции юриста. "
        "После оформления нажмите кнопку ниже — администратор/юрист проверит получение.",
        reply_markup=one(("Я оформил доверенность", "poa_done"), ("💬 Задать вопрос", "message_create"), ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data == "poa_done")
async def poa_done(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text("Нет активного дела.")
        return
    await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_POA_RECEIVED, actor_type="client", actor_id=user.id, force=True, comment="Клиент отметил оформление доверенности")
    await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_CLAIM_SENT, actor_type="system", actor_id=None, force=True, comment="Запущен этап претензии")
    await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_WAITING_30_DAYS, actor_type="system", actor_id=None, force=True, comment="Запущено ожидание 30 дней после претензии")
    await db.commit()
    await callback.message.edit_text(
        "📨 Претензия\n\nСтатус обновлен: претензия направлена, начался срок ожидания 30 календарных дней.",
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("💬 Задать вопрос", "message_create")),
    )


@router.callback_query(lambda c: c.data == "court_status")
async def court_status(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text("Нет активного дела.")
        return
    if case.status == CaseStatus.M1_WAITING_30_DAYS:
        await ctx.case_service.change_status(case=case, next_status=CaseStatus.M1_COURT_STAGE, actor_type="system", actor_id=None, force=True, comment="Открыт судебный этап")
    await db.commit()
    await callback.message.edit_text(
        "🏛 Судебный этап\n\nЮрист сопровождает процесс. Все значимые события будут отображаться в истории дела.",
        reply_markup=one(("Оплатить второй платеж", "pay_court_70000"), ("📁 Мое дело", "my_case_open"), ("💬 Задать вопрос", "message_create")),
    )


@router.callback_query(lambda c: c.data == "pay_court_70000")
async def pay_court(callback: CallbackQuery, db):
    from app.bot.screens.payments import start_payment
    await start_payment(callback, db, PaymentCode.M1_COURT_PAYMENT)


@router.callback_query(lambda c: c.data == "pay_success_fee")
async def pay_success_fee(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text("Нет активного дела.")
        return
    existing = (await db.execute(select(Payment).where(Payment.case_id == case.id).where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE))).scalars().first()
    service = PaymentService(db)
    if existing:
        payment = existing
    else:
        # MVP: если сумма взыскания еще не внесена юристом, выставляем 10% от предварительного расчета или 1 руб. как технический платеж.
        amount = await service.estimate_success_fee_for_case(case.id)
        payment = await service.get_or_create_payment(case=case, payment_code=PaymentCode.M1_SUCCESS_FEE, amount=amount)
    payment = await service.create_payment_link(payment)
    await db.commit()
    await callback.message.edit_text(
        f"💳 Финальный платеж\n\nСумма: {_money(payment.amount)}\n\nПосле оплаты дело будет закрыто.",
        reply_markup=one(("Перейти к оплате", "noop"), ("✅ DEV подтвердить оплату", f"pay_fake_success:{payment.id}"), ("📁 Мое дело", "my_case_open")),
    )
