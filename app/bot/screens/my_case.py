from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import func, select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import (
    get_case_progress_percent,
    get_client_visible_status,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.calculation import Calculation
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.payment import Payment

router = Router()


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


def _is_m2(case) -> bool:
    return case.route in {RouteCode.M2, RouteCode.M2.value}


async def _load_single_active_consultation(db, case):
    if not _is_m2(case):
        return None, False
    active = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id == case.id)
                .where(
                    Consultation.status.notin_(ConsultationService.INACTIVE_STATUSES)
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            )
        )
        .scalars()
        .all()
    )
    if len(active) > 1:
        return None, True
    return (active[0] if active else None), False


def _consultation_status(consultation):
    if consultation is None:
        return None
    try:
        return ConsultationStatus(consultation.status)
    except (TypeError, ValueError):
        return None


def _next_action_target(case, consultation):
    if _is_m2(case):
        status = _consultation_status(consultation)
        return {
            ConsultationStatus.DESCRIPTION_PENDING: "consult_description_start",
            ConsultationStatus.DOCUMENTS_OPTIONAL: "m2_documents_open",
            ConsultationStatus.SLOT_PENDING: "consult_slot_open",
            ConsultationStatus.SLOT_RESERVED: "consult_slot_reserved_open",
            ConsultationStatus.PAYMENT_PENDING: "consult_pay",
            ConsultationStatus.CONFIRMED: "consultation_booked_open",
            ConsultationStatus.BOOKED: "consultation_booked_open",
        }.get(status)

    return {
        "CALCULATED": "consent_open",
        "M1_DOCUMENTS_PENDING": "documents_open",
        "M1_DOCS_REQUESTED": "documents_open",
        "M1_CONTRACT_READY": "contract_open",
        "M1_WAITING_PAYMENT_30000": "pay_start_30000",
        "M1_POWER_OF_ATTORNEY": "poa_instruction",
        "M1_WAITING_30_DAYS": "court_status",
        "M1_COURT_STAGE": "court_status",
        "M1_WAITING_PAYMENT_70000": "pay_court_70000",
        "M1_ENFORCEMENT": "pay_success_fee",
        "M1_WAITING_SUCCESS_FEE": "pay_success_fee",
    }.get(case.status)


def _next_action_text(case, consultation, conflict: bool) -> str:
    if conflict:
        return "Требуется проверка данных консультации сотрудником."
    if not _is_m2(case):
        return case.next_action or "Ожидайте уведомления о следующем этапе."

    status = _consultation_status(consultation)
    return {
        ConsultationStatus.DESCRIPTION_PENDING: "Опишите вопрос для юриста",
        ConsultationStatus.DOCUMENTS_OPTIONAL: (
            "Приложите документы или пропустите документный шаг"
        ),
        ConsultationStatus.SLOT_PENDING: "Выберите удобное время консультации",
        ConsultationStatus.SLOT_RESERVED: (
            "Проверьте выбранное время; слот временно удерживается за вами"
        ),
        ConsultationStatus.PAYMENT_PENDING: (
            "Оплатите консультацию до окончания срока удержания слота"
        ),
        ConsultationStatus.PAID_PENDING_CONFIRMATION: (
            "Оплата получена. Ожидается подтверждение консультации"
        ),
        ConsultationStatus.CONFIRMED: "Консультация подтверждена",
        ConsultationStatus.BOOKED: "Откройте данные забронированной консультации",
        ConsultationStatus.DONE: "Консультация проведена",
        ConsultationStatus.CLOSED: "Консультация завершена",
        ConsultationStatus.CANCELLED: "Консультация отменена",
        ConsultationStatus.DECLINED: "Консультация не подтверждена",
    }.get(status, "Не удалось определить следующий шаг консультации.")


@router.callback_query(lambda c: c.data == "my_case_open")
async def my_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case or case.client_id != user.id:
        await callback.message.edit_text(
            "📁 У вас пока нет активного дела.",
            reply_markup=one(("🧮 Рассчитать", "calc_start"), ("💬 Юрист", "calc_to_m2")),
        )
        return

    calc = (
        await db.execute(select(Calculation).where(Calculation.case_id == case.id))
    ).scalars().first()
    documents_count = (
        await db.execute(
            select(func.count(Document.id)).where(Document.case_id == case.id)
        )
    ).scalar_one()
    payments_count = (
        await db.execute(
            select(func.count(Payment.id))
            .where(Payment.case_id == case.id)
            .where(Payment.status.in_(["PENDING", "WAITING_CONFIRMATION"]))
        )
    ).scalar_one()
    consultation, consultation_conflict = await _load_single_active_consultation(
        db, case
    )
    target = (
        None
        if consultation_conflict
        else _next_action_target(case, consultation)
    )

    text = (
        "📁 Мое дело\n\n"
        f"Номер: {case.case_number}\n"
        f"Маршрут: {case.route or '—'}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Прогресс: {get_case_progress_percent(case.status)}%\n\n"
        f"Следующий шаг: {_next_action_text(case, consultation, consultation_conflict)}\n\n"
        f"📊 Расчет: {money(calc.penalty_amount if calc else None)}\n"
        f"Дней просрочки: {calc.delay_days if calc else '—'}\n\n"
        f"📄 Документы: {documents_count}\n"
        f"💳 Ожидают оплаты: {payments_count}"
    )
    buttons = []
    if target:
        buttons.append(("Следующий шаг", "next_action"))
    buttons.extend(
        [
            ("📄 Документы", "documents_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(
    lambda c: c.data == "next_action" or c.data.startswith("next_action:")
)
async def next_action(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case or case.client_id != user.id:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    consultation, consultation_conflict = await _load_single_active_consultation(
        db, case
    )
    target = (
        None
        if consultation_conflict
        else _next_action_target(case, consultation)
    )
    if not target:
        await callback.message.edit_text(
            "Сейчас действие не требуется. Мы сообщим, когда появится следующий шаг.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "Откройте следующий шаг:",
        reply_markup=one(
            ("Перейти", target),
            ("📁 Мое дело", "my_case_open"),
        ),
    )
