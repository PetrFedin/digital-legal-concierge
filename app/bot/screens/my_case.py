from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select, func

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import get_client_visible_status, get_case_progress_percent
from app.models.calculation import Calculation
from app.models.document import Document
from app.models.payment import Payment

router = Router()


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


@router.callback_query(lambda c: c.data == "my_case_open")
async def my_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "📁 У вас пока нет активного дела.",
            reply_markup=one(("🧮 Рассчитать", "calc_start"), ("💬 Юрист", "calc_to_m2")),
        )
        return

    calc = (await db.execute(select(Calculation).where(Calculation.case_id == case.id))).scalars().first()
    documents_count = (await db.execute(select(func.count(Document.id)).where(Document.case_id == case.id))).scalar_one()
    payments_count = (
        await db.execute(
            select(func.count(Payment.id))
            .where(Payment.case_id == case.id)
            .where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
        )
    ).scalar_one()

    text = (
        "📁 Мое дело\n\n"
        f"Номер: {case.case_number}\n"
        f"Маршрут: {case.route or '—'}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Прогресс: {get_case_progress_percent(case.status)}%\n\n"
        f"Следующий шаг: {case.next_action}\n\n"
        f"📊 Расчет: {money(calc.penalty_amount if calc else None)}\n"
        f"Дней просрочки: {calc.delay_days if calc else '—'}\n\n"
        f"📄 Документы: {documents_count}\n"
        f"💳 Ожидают оплаты: {payments_count}"
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("Следующий шаг", f"next_action:{case.status}"),
            ("📄 Документы", "documents_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith('next_action:'))
async def next_action(callback: CallbackQuery):
    status = callback.data.split(':', 1)[1]
    mapping = {
        'CALCULATED': 'consent_open',
        'M1_DOCUMENTS_PENDING': 'documents_open',
        'M1_DOCS_REQUESTED': 'documents_open',
        'M1_CONTRACT_READY': 'contract_open',
        'M1_WAITING_PAYMENT_30000': 'pay_start_30000',
        'M1_POWER_OF_ATTORNEY': 'poa_instruction',
        'M1_WAITING_30_DAYS': 'court_status',
        'M1_COURT_STAGE': 'court_status',
        'M1_WAITING_PAYMENT_70000': 'pay_court_70000',
        'M1_ENFORCEMENT': 'pay_success_fee',
        'M1_WAITING_SUCCESS_FEE': 'pay_success_fee',
        'M2_DESCRIPTION_PENDING': 'consult_description_start',
        'M2_DOCUMENTS_OPTIONAL': 'documents_open',
        'M2_SLOT_PENDING': 'consult_slot_open',
        'M2_PAYMENT_PENDING': 'consult_pay',
        'M2_CONSULTATION_BOOKED': 'consultation_booked_open',
    }
    target = mapping.get(status)
    if not target:
        await callback.message.edit_text('Сейчас действие не требуется. Мы сообщим, когда появится следующий шаг.', reply_markup=one(('📁 Мое дело', 'my_case_open'), ('🏠 Главная', 'nav_home')))
        return
    await callback.message.edit_text('Откройте следующий шаг:', reply_markup=one(('Перейти', target), ('📁 Мое дело', 'my_case_open')))
