from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.cases.case_timeline import (
    get_case_progress_bar,
    get_case_progress_percent,
    get_client_status_description,
    get_client_status_owner,
    get_client_visible_status,
    get_route_roadmap,
    get_route_title,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.case_statuses import RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.calculation import Calculation
from app.models.consultation import Consultation
from app.models.document import Document
from app.models.payment import Payment


router = Router()
MOSCOW = ZoneInfo("Europe/Moscow")
PENDING_PAYMENT_STATUSES = {
    PaymentStatus.PENDING.value,
    PaymentStatus.WAITING_CONFIRMATION.value,
}


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ") + " ₽"


def _is_m2(case) -> bool:
    return case.route in {RouteCode.M2, RouteCode.M2.value}


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_datetime(value: datetime | None) -> str:
    aware = _as_utc(value)
    if aware is None:
        return "не выбрано"
    return aware.astimezone(MOSCOW).strftime("%d.%m.%Y в %H:%M")


def _format_time(value: datetime | None) -> str:
    aware = _as_utc(value)
    if aware is None:
        return "—"
    return aware.astimezone(MOSCOW).strftime("%H:%M")


def _roadmap_text(case) -> str:
    progress = get_case_progress_percent(case.status)
    roadmap = get_route_roadmap(case.route, progress)
    icons = {"done": "✅", "current": "🔵", "upcoming": "⚪️"}
    lines = [
        "🗺 Дорожная карта дела",
        "",
        f"Маршрут: {get_route_title(case.route)}",
        f"Общий прогресс: {get_case_progress_bar(case.status)} {progress}%",
        "",
    ]
    lines.extend(
        f"{icons.get(str(item['state']), '⚪️')} {item['title']}"
        for item in roadmap
    )
    lines.extend(
        [
            "",
            "✅ — этап завершён",
            "🔵 — текущий блок работы",
            "⚪️ — следующий этап",
            "",
            "Прогресс отражает этап процесса, а не прогноз результата или "
            "точную продолжительность дела.",
        ]
    )
    return "\n".join(lines)


async def _load_owned_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        return user, None
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None or case.client_id != user.id:
        return user, None
    return user, case


async def _load_single_active_consultation(db, case):
    if not _is_m2(case):
        return None, False
    active = list(
        (
            await db.execute(
                select(Consultation)
                .options(
                    joinedload(Consultation.slot),
                    joinedload(Consultation.lawyer),
                )
                .where(Consultation.case_id == case.id)
                .where(
                    Consultation.status.notin_(
                        ConsultationService.INACTIVE_STATUSES
                    )
                )
                .order_by(
                    Consultation.created_at.desc(),
                    Consultation.id.desc(),
                )
            )
        )
        .unique()
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


def _payment_status_title(payment) -> str:
    if payment is None:
        return "платёж не выставлен"
    if payment.manual_review_required:
        return "проверяется сотрудником"
    return {
        PaymentStatus.PENDING.value: "ожидается",
        PaymentStatus.WAITING_CONFIRMATION.value: "проверяется",
        PaymentStatus.PAID.value: "оплачено",
        PaymentStatus.FAILED.value: "не завершено",
        PaymentStatus.CANCELLED.value: "отменено",
        PaymentStatus.REFUNDED.value: "возвращено",
        PaymentStatus.EXPIRED.value: "срок ссылки истёк",
    }.get(str(payment.status or ""), "статус уточняется")


def _next_action_target(case, consultation, payment):
    if _is_m2(case):
        status = _consultation_status(consultation)
        if payment is not None and payment.manual_review_required:
            return "contact_lawyer"
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
        "CLIENT_DECISION": "consent_open",
        "M1_DOCUMENTS_PENDING": "documents_open",
        "M1_DOCS_REQUESTED": "documents_open",
        "M1_CONTRACT_READY": "contract_open",
        "M1_WAITING_PAYMENT_30000": "pay_start_30000",
        "M1_POWER_OF_ATTORNEY": "poa_instruction",
        "M1_WAITING_30_DAYS": "court_status",
        "M1_COURT_STAGE": "court_status",
        "M1_WAITING_PAYMENT_70000": "pay_court_70000",
        "M1_MONEY_RECEIVED": "pay_success_fee",
        "M1_WAITING_SUCCESS_FEE": "pay_success_fee",
    }.get(case.status)


def _next_action_text(case, consultation, conflict: bool, payment) -> str:
    if conflict:
        return (
            "Дождитесь проверки: сотрудник сверяет данные консультации и "
            "устранит расхождение."
        )
    if not _is_m2(case):
        return case.next_action or (
            "Действий от вас сейчас не требуется. Мы сообщим о следующем шаге."
        )
    if payment is not None and payment.manual_review_required:
        return (
            "Оплата сохранена и проверяется сотрудником. Повторно платить не нужно."
        )

    status = _consultation_status(consultation)
    return {
        ConsultationStatus.DESCRIPTION_PENDING: (
            "Опишите ситуацию и главный вопрос для юриста."
        ),
        ConsultationStatus.DOCUMENTS_OPTIONAL: (
            "Приложите полезные документы или пропустите этот шаг."
        ),
        ConsultationStatus.SLOT_PENDING: (
            "Выберите удобный способ, дату и время консультации."
        ),
        ConsultationStatus.SLOT_RESERVED: (
            "Проверьте выбранное время и перейдите к оплате до окончания резерва."
        ),
        ConsultationStatus.PAYMENT_PENDING: (
            "Оплатите консультацию до окончания срока удержания слота."
        ),
        ConsultationStatus.PAID_PENDING_CONFIRMATION: (
            "Действий не требуется: назначенный юрист подтверждает консультацию."
        ),
        ConsultationStatus.CONFIRMED: (
            "Откройте карточку консультации и проверьте дату, время и формат."
        ),
        ConsultationStatus.BOOKED: (
            "Подготовьте вопросы и материалы к назначенной консультации."
        ),
        ConsultationStatus.DONE: (
            "Ознакомьтесь с итогом консультации и предложенным планом действий."
        ),
        ConsultationStatus.CLOSED: "Консультационный маршрут завершён.",
        ConsultationStatus.CANCELLED: (
            "Свяжитесь с менеджером, чтобы уточнить варианты новой записи."
        ),
        ConsultationStatus.DECLINED: (
            "Выберите другое время или свяжитесь с менеджером."
        ),
    }.get(
        status,
        "Дождитесь обновления: сотрудник уточняет следующий шаг консультации.",
    )


def _consultation_summary(consultation, conflict: bool, payment) -> str:
    if conflict:
        return (
            "\n⚠️ По консультации обнаружено несколько активных записей. "
            "Сотрудник должен проверить данные."
        )
    if consultation is None:
        return ""
    status = _consultation_status(consultation)
    status_title = {
        ConsultationStatus.DESCRIPTION_PENDING: "нужно описание ситуации",
        ConsultationStatus.DOCUMENTS_OPTIONAL: "можно приложить документы",
        ConsultationStatus.SLOT_PENDING: "нужно выбрать время",
        ConsultationStatus.SLOT_RESERVED: "время временно удерживается",
        ConsultationStatus.PAYMENT_PENDING: "ожидается оплата",
        ConsultationStatus.PAID_PENDING_CONFIRMATION: (
            "ожидается подтверждение юриста"
        ),
        ConsultationStatus.CONFIRMED: "подтверждена",
        ConsultationStatus.BOOKED: "назначена",
        ConsultationStatus.DONE: "проведена",
        ConsultationStatus.CLOSED: "завершена",
        ConsultationStatus.CANCELLED: "отменена",
        ConsultationStatus.DECLINED: "не подтверждена",
    }.get(status, "статус уточняется")
    slot = consultation.slot
    lawyer_name = (
        consultation.lawyer.full_name
        if consultation.lawyer is not None
        else "будет указан"
    )
    starts_at = slot.starts_at if slot is not None else consultation.scheduled_at
    end_text = _format_time(slot.ends_at) if slot is not None else "—"
    duration = (
        max(
            1,
            int(
                (
                    _as_utc(slot.ends_at) - _as_utc(slot.starts_at)
                ).total_seconds()
                // 60
            ),
        )
        if slot is not None
        else None
    )
    hold_text = "не используется"
    if (
        slot is not None
        and slot.status == "held"
        and slot.hold_expires_at is not None
    ):
        remaining = max(
            0,
            int(
                (
                    _as_utc(slot.hold_expires_at) - datetime.now(timezone.utc)
                ).total_seconds()
            ),
        )
        hold_text = (
            f"до {_format_time(slot.hold_expires_at)} "
            f"(около {(remaining + 59) // 60} мин.)"
        )
    return (
        "\n\n📅 Консультация\n"
        f"Статус: {status_title}\n"
        f"Дата и начало: {_format_datetime(starts_at)}\n"
        f"Окончание: {end_text}\n"
        f"Продолжительность: {duration or '—'} минут\n"
        "Часовой пояс: Москва, UTC+3\n"
        f"Юрист: {lawyer_name}\n"
        f"Формат: {consultation.consultation_type or 'уточняется'}\n"
        f"Оплата: {_payment_status_title(payment)}\n"
        f"Временный резерв: {hold_text}"
    )


@router.callback_query(lambda c: c.data == "my_case_open")
async def my_case(callback: CallbackQuery, db):
    # Reading the dashboard also runs the canonical idempotent cleanup so an
    # expired hold is never presented as payable or active.
    await SlotService(db).release_expired_holds()
    await db.commit()

    _, case = await _load_owned_case(callback, db)
    if case is None:
        await callback.message.edit_text(
            "📁 У вас пока нет активного дела.\n\n"
            "Начните с предварительного расчёта или оформите консультацию юриста.",
            reply_markup=one(
                ("🧮 Рассчитать неустойку", "calc_start"),
                ("💬 Записаться к юристу", "calc_to_m2"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    calc = (
        await db.execute(
            select(Calculation).where(Calculation.case_id == case.id)
        )
    ).scalars().first()
    documents_count = int(
        (
            await db.execute(
                select(func.count(Document.id)).where(Document.case_id == case.id)
            )
        ).scalar_one()
    )
    payments_count = int(
        (
            await db.execute(
                select(func.count(Payment.id))
                .where(Payment.case_id == case.id)
                .where(Payment.status.in_(PENDING_PAYMENT_STATUSES))
            )
        ).scalar_one()
    )
    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
    ).scalars().first()
    consultation, consultation_conflict = await _load_single_active_consultation(
        db,
        case,
    )
    target = (
        None
        if consultation_conflict
        else _next_action_target(case, consultation, payment)
    )
    progress = get_case_progress_percent(case.status)
    calculation_block = ""
    if calc is not None:
        calculation_block = (
            "\n\n📊 Предварительный расчёт\n"
            f"Сумма: {money(calc.penalty_amount)}\n"
            f"Просрочка: "
            f"{calc.delay_days if calc.delay_days is not None else '—'} дн."
        )

    text = (
        f"⚖️ Дело № {case.case_number}\n"
        "━━━━━━━━━━━━━━━━\n"
        f"Маршрут: {get_route_title(case.route)}\n"
        f"Статус: {get_client_visible_status(case.status)}\n"
        f"Прогресс: {get_case_progress_bar(case.status)} {progress}%\n\n"
        "🔎 Что происходит сейчас\n"
        f"{get_client_status_description(case.status)}\n\n"
        "👤 Кто действует сейчас\n"
        f"{get_client_status_owner(case.status)}\n\n"
        "➡️ Ваш следующий шаг\n"
        f"{_next_action_text(case, consultation, consultation_conflict, payment)}"
        f"{calculation_block}"
        f"{_consultation_summary(consultation, consultation_conflict, payment)}\n\n"
        "📌 Материалы дела\n"
        f"Документы: {documents_count}\n"
        f"Платежи, требующие внимания: {payments_count}\n\n"
        "Мы уведомим вас, когда статус изменится или потребуется новое действие."
    )

    buttons = []
    if target:
        buttons.append(("➡️ Выполнить следующий шаг", "next_action"))
    if consultation is not None and not consultation_conflict:
        buttons.append(("📅 Консультация", "consultation_booked_open"))
    buttons.extend(
        [
            ("🗺 Этапы и прогресс", "case_roadmap_open"),
            ("📄 Документы", "documents_open"),
            ("💳 Оплаты", "payments_open"),
            ("🕘 История дела", "case_history_open"),
            ("💬 Связаться с юристом", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.callback_query(lambda c: c.data == "case_roadmap_open")
async def case_roadmap(callback: CallbackQuery, db):
    _, case = await _load_owned_case(callback, db)
    if case is None:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    await callback.message.edit_text(
        _roadmap_text(case),
        reply_markup=one(
            ("📁 Вернуться в дело", "my_case_open"),
            ("💬 Задать вопрос", "contact_lawyer"),
        ),
    )


@router.callback_query(
    lambda c: c.data == "next_action" or c.data.startswith("next_action:")
)
async def next_action(callback: CallbackQuery, db):
    await SlotService(db).release_expired_holds()
    await db.commit()
    _, case = await _load_owned_case(callback, db)
    if case is None:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    payment = (
        await db.execute(
            select(Payment)
            .where(Payment.case_id == case.id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
    ).scalars().first()
    consultation, consultation_conflict = await _load_single_active_consultation(
        db,
        case,
    )
    target = (
        None
        if consultation_conflict
        else _next_action_target(case, consultation, payment)
    )
    action_text = _next_action_text(
        case,
        consultation,
        consultation_conflict,
        payment,
    )
    if not target:
        await callback.message.edit_text(
            "⏳ Сейчас действие от вас не требуется.\n\n"
            f"{action_text}\n\n"
            "Когда появится новый шаг, бот отправит уведомление.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💬 Задать вопрос", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "➡️ Следующий шаг\n\n"
        f"{action_text}\n\n"
        "Нажмите кнопку ниже, чтобы продолжить безопасно с текущего этапа дела.",
        reply_markup=one(
            ("Продолжить", target),
            ("📁 Вернуться в дело", "my_case_open"),
        ),
    )
