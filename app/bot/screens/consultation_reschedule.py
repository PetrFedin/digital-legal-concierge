from __future__ import annotations

from datetime import date, datetime, timezone
import logging

from aiogram import Router
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.domain.consultations.cancellation_request_service import (
    ConsultationCancellationRequestService,
)
from app.domain.consultations.client_schedule_service import (
    ClientConsultationConflictError,
)
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationSlotError,
)
from app.domain.consultations.reschedule_service import ConsultationRescheduleService
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.audit_log import AuditLog
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot


router = Router()
logger = logging.getLogger(__name__)


MANAGEABLE_STATUSES = frozenset(
    {
        ConsultationStatus.PAID_PENDING_CONFIRMATION.value,
        ConsultationStatus.CONFIRMED.value,
        ConsultationStatus.BOOKED.value,
    }
)
RESCHEDULABLE_STATUSES = frozenset(
    {
        ConsultationStatus.CONFIRMED.value,
        ConsultationStatus.BOOKED.value,
    }
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _format_date(value: datetime) -> str:
    return value.strftime("%d.%m.%Y")


def _format_time(value: datetime) -> str:
    return value.strftime("%H:%M")


def _duration(slot: ConsultationSlot) -> int:
    return max(
        1,
        int((_as_utc(slot.ends_at) - _as_utc(slot.starts_at)).total_seconds() // 60),
    )


async def _load_context(
    callback: CallbackQuery,
    db,
    *,
    statuses: frozenset[str],
):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ConsultationNotFoundError("Доступ клиента ограничен.")
    cases = await ctx.case_service.list_active_cases_for_user(
        user.id,
        route=RouteCode.M2,
    )
    if len(cases) != 1:
        raise ActiveConsultationConflictError(
            "Не удалось однозначно определить консультационное дело."
        )
    case = cases[0]
    consultations = list(
        (
            await db.execute(
                select(Consultation)
                .options(
                    joinedload(Consultation.lawyer),
                    joinedload(Consultation.slot).joinedload(
                        ConsultationSlot.lawyer
                    ),
                )
                .where(
                    Consultation.case_id == case.id,
                    Consultation.status.in_(statuses),
                )
                .order_by(Consultation.created_at.desc(), Consultation.id.desc())
            )
        )
        .unique()
        .scalars()
        .all()
    )
    if len(consultations) != 1:
        raise ActiveConsultationConflictError(
            "Для дела не определена единственная подтверждённая консультация."
        )
    consultation = consultations[0]
    slot = consultation.slot
    if (
        slot is None
        or slot.status != "booked"
        or slot.consultation_id != consultation.id
    ):
        raise ConsultationSlotError("Подтверждённый слот консультации не найден.")
    return user, case, consultation, slot


async def _has_cancellation_request(db, *, case_id: int) -> bool:
    return (
        await db.execute(
            select(AuditLog.id)
            .where(
                AuditLog.entity_type == "case",
                AuditLog.entity_id == case_id,
                AuditLog.action == ConsultationCancellationRequestService.ACTION,
            )
            .limit(1)
        )
    ).scalar_one_or_none() is not None


async def _show_error(callback: CallbackQuery, text: str) -> None:
    await callback.message.edit_text(
        f"⚠️ {text}",
        reply_markup=one(
            ("📋 Текущая запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def open_booked_consultation(callback: CallbackQuery, db):
    try:
        _, case, consultation, slot = await _load_context(
            callback,
            db,
            statuses=MANAGEABLE_STATUSES,
        )
        cancellation_requested = await _has_cancellation_request(
            db,
            case_id=case.id,
        )
        await db.commit()
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        ConsultationSlotError,
    ) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening booked consultation")
        await _show_error(callback, "Не удалось открыть консультацию.")
        return

    lawyer = consultation.lawyer or slot.lawyer
    lawyer_name = lawyer.full_name if lawyer is not None else "уточняется"
    status_title = {
        ConsultationStatus.PAID_PENDING_CONFIRMATION.value: (
            "Оплата получена, ожидается подтверждение юриста"
        ),
        ConsultationStatus.CONFIRMED.value: "Подтверждена юристом",
        ConsultationStatus.BOOKED.value: "Назначена",
    }.get(consultation.status, "Статус уточняется")
    lines = [
        "📋 Юридическая консультация",
        "",
        f"Статус: {status_title}",
        f"Дата: {_format_date(slot.starts_at)}",
        f"Время: {_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}",
        f"Продолжительность: {_duration(slot)} минут",
        f"Юрист: {lawyer_name}",
    ]
    buttons = []
    if cancellation_requested:
        lines.extend(
            [
                "",
                "Запрос отмены передан менеджеру. До решения запись и оплата сохранены.",
            ]
        )
        buttons.append(("💬 Связаться с менеджером", "contact_lawyer"))
    else:
        if consultation.status in RESCHEDULABLE_STATUSES:
            buttons.append(("🔄 Изменить время", "consult_reschedule"))
        buttons.append(("❌ Запросить отмену", "consult_cancel"))
    buttons.extend(
        [
            ("📄 Добавить документы", "documents_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text("\n".join(lines), reply_markup=one(*buttons))


async def _load_reschedule_options(callback: CallbackQuery, db):
    user, case, consultation, old_slot = await _load_context(
        callback,
        db,
        statuses=RESCHEDULABLE_STATUSES,
    )
    if await _has_cancellation_request(db, case_id=case.id):
        raise ActiveConsultationConflictError(
            "Перенос недоступен, пока менеджер рассматривает запрос отмены."
        )
    if case.status != CaseStatus.M2_CONSULTATION_BOOKED.value:
        raise ActiveConsultationConflictError(
            "Перенос недоступен на текущем этапе дела."
        )
    expected_lawyer_id = case.assigned_lawyer_id or consultation.lawyer_id
    if expected_lawyer_id is None:
        raise ConsultationSlotError("Для консультации не определён текущий юрист.")

    slots = await SlotService(db).get_available_slots(
        lawyer_id=expected_lawyer_id,
        limit=200,
    )
    paid_duration = _duration(old_slot)
    compatible = [
        slot
        for slot in slots
        if slot.id != old_slot.id and _duration(slot) == paid_duration
    ]
    return user, case, consultation, old_slot, compatible


@router.callback_query(lambda c: c.data == "consult_reschedule")
async def open_reschedule(callback: CallbackQuery, db):
    try:
        _, _, _, old_slot, slots = await _load_reschedule_options(callback, db)
        await db.commit()
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        ConsultationSlotError,
    ) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening consultation reschedule")
        await _show_error(callback, "Не удалось открыть варианты переноса.")
        return

    if not slots:
        await callback.message.edit_text(
            "У текущего юриста нет другого свободного времени той же "
            f"продолжительности ({_duration(old_slot)} минут).\n\n"
            "Текущая запись сохранена.",
            reply_markup=one(
                ("🔄 Обновить", "consult_reschedule"),
                ("💬 Согласовать время", "contact_lawyer"),
                ("⬅ Оставить запись", "consultation_booked_open"),
            ),
        )
        return

    dates: list[date] = []
    seen: set[date] = set()
    for slot in slots:
        slot_date = slot.starts_at.date()
        if slot_date not in seen:
            seen.add(slot_date)
            dates.append(slot_date)
    await callback.message.edit_text(
        "🔄 Изменение времени консультации\n\n"
        f"Оплаченная длительность сохраняется: {_duration(old_slot)} минут.\n"
        "Текущая запись останется действующей до успешной атомарной замены.",
        reply_markup=one(
            *[
                (
                    f"📅 {value:%d.%m.%Y}",
                    f"consult_reschedule_date:{value.isoformat()}",
                )
                for value in dates
            ],
            ("⬅ Оставить запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_date:"))
async def open_reschedule_date(callback: CallbackQuery, db):
    try:
        selected_date = date.fromisoformat(callback.data.split(":", 1)[1])
        _, _, _, _, slots = await _load_reschedule_options(callback, db)
        selected = [slot for slot in slots if slot.starts_at.date() == selected_date]
        await db.commit()
    except (TypeError, ValueError):
        await db.rollback()
        await callback.answer(
            "Некорректная дата. Откройте перенос заново.",
            show_alert=True,
        )
        return
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        ConsultationSlotError,
    ) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening reschedule date")
        await _show_error(callback, "Не удалось открыть свободное время.")
        return

    if not selected:
        await callback.answer(
            "На эту дату подходящего времени больше нет.",
            show_alert=True,
        )
        await open_reschedule(callback, db)
        return
    await callback.message.edit_text(
        f"Выберите новое время на {selected_date:%d.%m.%Y}.\n\n"
        "Текущая запись будет освобождена только после успешной замены.",
        reply_markup=one(
            *[
                (
                    f"{_format_time(slot.starts_at)}–{_format_time(slot.ends_at)}",
                    f"consult_reschedule_slot:{slot.id}",
                )
                for slot in selected
            ],
            ("← Другие даты", "consult_reschedule"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_slot:"))
async def confirm_reschedule(callback: CallbackQuery, db):
    try:
        new_slot_id = int(callback.data.split(":", 1)[1])
        if new_slot_id <= 0:
            raise ValueError
        user, case, consultation, _, options = await _load_reschedule_options(
            callback,
            db,
        )
        if new_slot_id not in {slot.id for slot in options}:
            raise ConsultationSlotError(
                "Новое время больше недоступно; прежняя запись сохранена."
            )
        consultation, new_slot = await ConsultationRescheduleService(db).reschedule(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=new_slot_id,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )
        await db.commit()
    except (TypeError, ValueError):
        await db.rollback()
        await callback.answer(
            "Некорректное время. Откройте перенос заново.",
            show_alert=True,
        )
        return
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        ConsultationSlotError,
        ClientConsultationConflictError,
    ) as exc:
        await db.rollback()
        await _show_error(callback, str(exc))
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while rescheduling consultation")
        await _show_error(
            callback,
            "Не удалось изменить время. Текущая запись сохранена.",
        )
        return

    await callback.message.edit_text(
        "✅ Время консультации изменено\n\n"
        f"Дата: {_format_date(new_slot.starts_at)}\n"
        f"Время: {_format_time(new_slot.starts_at)}–{_format_time(new_slot.ends_at)}\n"
        f"Продолжительность: {_duration(new_slot)} минут\n\n"
        "Оплата, юрист и связь с делом сохранены.",
        reply_markup=one(
            ("📋 Детали консультации", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )
