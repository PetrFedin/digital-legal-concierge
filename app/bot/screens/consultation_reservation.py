from __future__ import annotations

from datetime import datetime, timezone
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
    ConsultationService,
)
from app.domain.consultations.reservation_service import (
    ConsultationReservationService,
)
from app.domain.consultations.slot_service import SlotService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import OPEN_PAYMENT_STATUSES, PaymentStatus
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.payment import Payment


router = Router()
logger = logging.getLogger(__name__)


class ReservationScreenError(RuntimeError):
    pass


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def _format_time(value: datetime) -> str:
    return value.strftime("%H:%M")


async def _load_context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ReservationScreenError("Доступ клиента ограничен.")

    cases = await ctx.case_service.list_active_cases_for_user(
        user.id,
        route="M2",
    )
    if len(cases) != 1:
        raise ReservationScreenError(
            "Не удалось однозначно определить активное консультационное дело."
        )
    case = cases[0]

    active = list(
        (
            await db.execute(
                select(Consultation)
                .options(
                    joinedload(Consultation.slot).joinedload(
                        ConsultationSlot.lawyer
                    )
                )
                .where(
                    Consultation.case_id == case.id,
                    Consultation.status.notin_(ConsultationService.INACTIVE_STATUSES),
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    if len(active) != 1:
        raise ReservationScreenError(
            "Для дела не определена единственная активная консультация."
        )
    return user, case, active[0]


async def _protected_payment(db, *, case_id: int) -> Payment | None:
    return (
        await db.execute(
            select(Payment)
            .where(
                Payment.case_id == case_id,
                Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT.value,
                Payment.status.in_(
                    set(OPEN_PAYMENT_STATUSES) | {PaymentStatus.PAID.value}
                ),
            )
            .order_by(Payment.id.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _show_unavailable(callback: CallbackQuery, text: str) -> None:
    await callback.message.edit_text(
        f"⚠️ {text}",
        reply_markup=one(
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_slot_reserved_open")
async def open_reserved_slot(callback: CallbackQuery, db):
    try:
        await SlotService(db).release_expired_holds()
        user, case, consultation = await _load_context(callback, db)
        status = ConsultationStatus(consultation.status)
        protected_payment = await _protected_payment(db, case_id=case.id)
        await db.commit()
    except (ReservationScreenError, ValueError):
        await db.rollback()
        await _show_unavailable(
            callback,
            "Резерв не найден или уже недоступен. Откройте актуальный шаг дела.",
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening consultation reservation")
        await _show_unavailable(callback, "Не удалось открыть выбранное время.")
        return

    if status == ConsultationStatus.SLOT_PENDING and consultation.slot_id is None:
        await callback.message.edit_text(
            "🕐 Временный резерв уже освобождён или истёк.\n\n"
            "Выберите новый свободный вариант.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status == ConsultationStatus.PAYMENT_PENDING or protected_payment is not None:
        await callback.message.edit_text(
            "💳 Оплата уже подготовлена или получена.\n\n"
            "Автоматическое освобождение времени отключено, чтобы платёжная "
            "ссылка не осталась без связанной консультации. Для изменения "
            "времени обратитесь к менеджеру.",
            reply_markup=one(
                ("💳 Открыть оплату", "consult_pay"),
                ("💬 Связаться с менеджером", "contact_lawyer"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    slot = consultation.slot
    if (
        status != ConsultationStatus.SLOT_RESERVED
        or slot is None
        or slot.status != "held"
        or slot.held_by_user_id != user.id
        or slot.hold_expires_at is None
        or _as_utc(slot.hold_expires_at) <= datetime.now(timezone.utc)
    ):
        await callback.message.edit_text(
            "🕐 Срок удержания времени истёк. Выберите новый свободный вариант.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    lawyer_name = slot.lawyer.full_name if slot.lawyer is not None else "уточняется"
    await callback.message.edit_text(
        "🕐 Выбранное время консультации\n\n"
        f"Дата: {_format_date(slot.starts_at)}\n"
        f"Время: {_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}\n"
        f"Юрист: {lawyer_name}\n"
        f"Резерв действует до: {_format_time(slot.hold_expires_at)}.\n\n"
        "До создания платёжной ссылки время можно освободить или заменить.",
        reply_markup=one(
            ("💳 Перейти к оплате", "consult_pay"),
            ("🔄 Выбрать другое время", "consult_reservation_change"),
            ("✖️ Освободить резерв", "consult_reservation_release"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_release_confirmation(
    callback: CallbackQuery,
    db,
    *,
    change_time: bool,
) -> None:
    try:
        user, case, consultation = await _load_context(callback, db)
        protected_payment = await _protected_payment(db, case_id=case.id)
        status = ConsultationStatus(consultation.status)
    except (ReservationScreenError, ValueError):
        await db.rollback()
        await _show_unavailable(callback, "Резерв уже недоступен.")
        return

    if status == ConsultationStatus.SLOT_PENDING and consultation.slot_id is None:
        await callback.message.edit_text(
            "Резерв уже освобождён.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    if status != ConsultationStatus.SLOT_RESERVED or protected_payment is not None:
        await db.rollback()
        await _show_unavailable(
            callback,
            "После начала оплаты время нельзя освободить автоматически.",
        )
        return

    title = "Выбрать другое время" if change_time else "Освободить резерв"
    consequence = (
        "Текущее время будет освобождено, после чего откроется актуальное расписание."
        if change_time
        else "Текущее время станет доступно другим клиентам."
    )
    confirm_callback = (
        "consult_reservation_change_confirm"
        if change_time
        else "consult_reservation_release_confirm"
    )
    await callback.message.edit_text(
        f"{title}?\n\n{consequence}\n\n"
        "Платёж не будет создан этой операцией.",
        reply_markup=one(
            ("✅ Подтвердить", confirm_callback),
            ("⬅ Оставить текущее время", "consult_slot_reserved_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_reservation_change")
async def request_reservation_change(callback: CallbackQuery, db):
    await _show_release_confirmation(callback, db, change_time=True)


@router.callback_query(lambda c: c.data == "consult_reservation_release")
async def request_reservation_release(callback: CallbackQuery, db):
    await _show_release_confirmation(callback, db, change_time=False)


async def _release_reservation(
    callback: CallbackQuery,
    db,
    *,
    change_time: bool,
) -> None:
    try:
        user, case, consultation = await _load_context(callback, db)
        await ConsultationReservationService(db).release_before_payment(
            consultation=consultation,
            case=case,
            client_id=user.id,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
            reason=(
                "Клиент освободил резерв для выбора другого времени"
                if change_time
                else "Клиент отказался от временного резерва"
            ),
        )
        await db.commit()
    except (
        ReservationScreenError,
        ConsultationNotFoundError,
        ConsultationSlotError,
        ActiveConsultationConflictError,
        ValueError,
    ) as exc:
        await db.rollback()
        await _show_unavailable(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while releasing consultation reservation")
        await _show_unavailable(
            callback,
            "Не удалось освободить резерв. Текущее время не изменено.",
        )
        return

    text = (
        "✅ Текущее время освобождено. Выберите новый свободный вариант."
        if change_time
        else "✅ Временный резерв освобождён. Платёж не создавался."
    )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("📅 Выбрать время", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: c.data == "consult_reservation_change_confirm"
)
async def confirm_reservation_change(callback: CallbackQuery, db):
    await _release_reservation(callback, db, change_time=True)


@router.callback_query(
    lambda c: c.data == "consult_reservation_release_confirm"
)
async def confirm_reservation_release(callback: CallbackQuery, db):
    await _release_reservation(callback, db, change_time=False)
