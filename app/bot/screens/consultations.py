from collections import defaultdict
from datetime import date
import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationDescriptionError,
    ConsultationNotFoundError,
    ConsultationService,
    ConsultationSlotError,
)
from app.domain.consultations.reschedule_service import (
    ConsultationRescheduleService,
)
from app.domain.consultations.slot_service import SlotService
from app.domain.consultations.state_machine import InvalidConsultationTransition
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case
from app.models.consultation import Consultation

router = Router()
logger = logging.getLogger(__name__)

DESCRIPTION_PROMPT = (
    "Опишите вашу ситуацию и вопрос для юриста одним сообщением.\n\n"
    "Не указывайте данные банковских карт, пароли и другие секретные сведения."
)
DESCRIPTION_SAVED_TEXT = (
    "Описание сохранено.\n\n"
    "Теперь вы можете приложить документы или пропустить этот шаг."
)
M2_DOMAIN_ERRORS = (
    ConsultationNotFoundError,
    ConsultationDescriptionError,
    ConsultationSlotError,
    ActiveConsultationConflictError,
    InvalidConsultationTransition,
)

CONSULTATION_STATUS_TITLES = {
    ConsultationStatus.DESCRIPTION_PENDING: "Ожидается описание ситуации",
    ConsultationStatus.DOCUMENTS_OPTIONAL: "Можно приложить документы",
    ConsultationStatus.SLOT_PENDING: "Ожидается выбор времени",
    ConsultationStatus.SLOT_RESERVED: "Время временно удерживается",
    ConsultationStatus.PAYMENT_PENDING: "Ожидается оплата",
    ConsultationStatus.PAID_PENDING_CONFIRMATION: (
        "Оплата получена, ожидается подтверждение юриста"
    ),
    ConsultationStatus.CONFIRMED: "Консультация подтверждена",
    ConsultationStatus.BOOKED: "Консультация назначена",
    ConsultationStatus.DONE: "Консультация проведена",
    ConsultationStatus.CANCELLED: "Консультация отменена",
    ConsultationStatus.RESCHEDULED: "Время консультации изменено",
    ConsultationStatus.DECLINED: "Выбранное время не подтверждено",
    ConsultationStatus.CLOSED: "Консультация завершена",
}


def format_date(value):
    return value.strftime("%d.%m.%Y")


def format_time(value):
    return value.strftime("%H:%M")


def _case_belongs_to_user(case, user) -> bool:
    return bool(case and case.client_id == user.id)


def _consultation_status(value) -> ConsultationStatus | None:
    try:
        return ConsultationStatus(value)
    except (TypeError, ValueError):
        return None


def _consultation_status_title(value) -> str:
    status = _consultation_status(value)
    return CONSULTATION_STATUS_TITLES.get(status, "Статус уточняется")


async def _load_m2_slot_context(
    callback: CallbackQuery,
    db,
    *,
    allow_reserved: bool,
):
    """Load the single active M2 consultation allowed to use the slot flow."""
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ConsultationNotFoundError("Доступ клиента ограничен.")
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not _case_belongs_to_user(case, user):
        raise ConsultationNotFoundError("Активное дело клиента не найдено.")
    if case.route not in {RouteCode.M2, RouteCode.M2.value}:
        raise ActiveConsultationConflictError(
            "Выбор времени доступен только для маршрута М2."
        )

    allowed_case_statuses = {CaseStatus.M2_SLOT_PENDING.value}
    allowed_consultation_statuses = {ConsultationStatus.SLOT_PENDING}
    if allow_reserved:
        allowed_case_statuses.add(CaseStatus.M2_PAYMENT_PENDING.value)
        allowed_consultation_statuses.add(ConsultationStatus.SLOT_RESERVED)
    if case.status not in allowed_case_statuses:
        raise ActiveConsultationConflictError(
            "Текущее состояние дела не допускает выбор времени консультации."
        )

    active = list(
        (
            await db.execute(
                select(Consultation)
                .where(Consultation.case_id == case.id)
                .where(
                    Consultation.status.notin_(ConsultationService.INACTIVE_STATUSES)
                )
                .order_by(
                    Consultation.created_at.desc(),
                    Consultation.id.desc(),
                )
            )
        )
        .scalars()
        .all()
    )
    if len(active) != 1:
        raise ActiveConsultationConflictError(
            "Для дела должна существовать ровно одна активная консультация М2."
        )
    consultation = active[0]
    consultation_status = _consultation_status(consultation.status)
    if consultation_status is None:
        raise ActiveConsultationConflictError(
            "Неизвестный статус консультации не допускает выбор времени."
        )
    if consultation_status not in allowed_consultation_statuses:
        raise ActiveConsultationConflictError(
            "Текущий шаг консультации не допускает выбор времени."
        )
    if (
        consultation_status == ConsultationStatus.SLOT_PENDING
        and case.status != CaseStatus.M2_SLOT_PENDING.value
    ):
        raise ActiveConsultationConflictError(
            "Статусы дела и консультации не согласованы."
        )
    if (
        consultation_status == ConsultationStatus.SLOT_RESERVED
        and case.status != CaseStatus.M2_PAYMENT_PENDING.value
    ):
        raise ActiveConsultationConflictError(
            "Статусы дела и консультации не согласованы."
        )
    return user, case, consultation


async def _load_owned_consultation(
    callback: CallbackQuery,
    db,
    *,
    allowed_statuses: set[ConsultationStatus] | None = None,
    for_update: bool = False,
):
    """Load a consultation through the current Telegram user's owned case."""
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    if user.is_blocked:
        raise ConsultationNotFoundError("Доступ клиента ограничен.")
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not _case_belongs_to_user(case, user):
        raise ConsultationNotFoundError("Активная консультация не найдена.")

    statement = (
        select(Consultation)
        .options(
            joinedload(Consultation.slot),
            joinedload(Consultation.lawyer),
        )
        .where(Consultation.case_id == case.id)
        .order_by(Consultation.created_at.desc(), Consultation.id.desc())
    )
    if allowed_statuses is not None:
        statement = statement.where(
            Consultation.status.in_({status.value for status in allowed_statuses})
        )
    if for_update:
        statement = statement.with_for_update()
    consultation = (
        await db.execute(statement.limit(1))
    ).unique().scalars().one_or_none()
    if consultation is None:
        raise ConsultationNotFoundError("Активная консультация не найдена.")
    if consultation.case_id != case.id:
        raise ConsultationNotFoundError("Консультация не принадлежит клиенту.")
    return user, case, consultation


async def _show_slot_flow_unavailable(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выбор времени сейчас недоступен. Откройте «Мое дело», чтобы "
        "продолжить с текущего шага.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _set_description_state(
    state: FSMContext,
    *,
    case_id: int,
    consultation_id: int,
):
    await state.clear()
    await state.update_data(case_id=case_id, consultation_id=consultation_id)
    await state.set_state(ConsultationDescriptionStates.waiting_description)


async def begin_m2_description_flow(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    db,
    reason: str,
):
    """Start or resume the strict M2 description step for the current user."""
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        if user.is_blocked:
            raise ConsultationNotFoundError("Доступ клиента ограничен.")
        case = await ctx.get_or_create_active_case_for_user(user)
        if not _case_belongs_to_user(case, user):
            raise ConsultationNotFoundError(
                "Дело не принадлежит текущему клиенту."
            )
        if case.route not in {RouteCode.M2, RouteCode.M2.value}:
            await ctx.case_service.transfer_to_m2(
                case=case,
                actor_type="client",
                actor_id=user.id,
                reason=reason,
            )
        consultation = await ConsultationService(db).create_or_get_m2_consultation(
            case=case,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось продолжить оформление консультации. "
            "Попробуйте ещё раз немного позже.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while starting the Telegram M2 description flow"
        )
        await callback.message.edit_text(
            "Не удалось продолжить оформление консультации. "
            "Попробуйте ещё раз немного позже.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    status = _consultation_status(consultation.status)
    if status is None:
        logger.error(
            "Unsupported consultation status after starting Telegram M2 flow",
            extra={"consultation_id": consultation.id},
        )
        await state.clear()
        await callback.message.edit_text(
            "Не удалось определить текущий шаг консультации. "
            "Откройте её в разделе «Мое дело».",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    if status == ConsultationStatus.DESCRIPTION_PENDING:
        await _set_description_state(
            state,
            case_id=case.id,
            consultation_id=consultation.id,
        )
        await callback.message.edit_text(
            DESCRIPTION_PROMPT,
            reply_markup=one(("Отмена", "nav_home")),
        )
        return

    await state.clear()
    if status == ConsultationStatus.DOCUMENTS_OPTIONAL:
        text = (
            "Описание уже сохранено. Следующий шаг — приложить документы "
            "или пропустить их."
        )
    elif status == ConsultationStatus.SLOT_PENDING:
        text = (
            "Описание и документный шаг уже завершены. "
            "Продолжите оформление из раздела «Мое дело»."
        )
    elif status in {
        ConsultationStatus.SLOT_RESERVED,
        ConsultationStatus.PAYMENT_PENDING,
    }:
        text = (
            "Консультация уже перешла к выбору времени или оплате. "
            "Текущий шаг доступен в разделе «Мое дело»."
        )
    else:
        text = (
            "Запись на консультацию уже оформлена. "
            "Откройте её в разделе «Мое дело»."
        )
    reply_markup = (
        one(
            ("📎 Приложить документы", "m2_documents_open"),
            ("⏭ Пропустить", "m2_documents_skip"),
            ("🏠 Главная", "nav_home"),
        )
        if status == ConsultationStatus.DOCUMENTS_OPTIONAL
        else one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    )
    await callback.message.edit_text(text, reply_markup=reply_markup)


@router.callback_query(
    lambda c: c.data in {"consult_booking_start", "consult_slot_open"}
)
async def booking_start(callback: CallbackQuery, db):
    try:
        await _load_m2_slot_context(callback, db, allow_reserved=False)
        slots = await SlotService(db).get_available_slots(limit=60)
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await _show_slot_flow_unavailable(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening Telegram M2 slot dates")
        await _show_slot_flow_unavailable(callback)
        return

    if not slots:
        await callback.message.edit_text(
            "Сейчас свободных слотов нет. Попробуйте обновить список немного позже.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    dates = []
    seen = set()
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key not in seen:
            seen.add(key)
            dates.append(
                (
                    f"📅 {format_date(slot.starts_at)}",
                    f"consult_date:{key}",
                )
            )
    await callback.message.edit_text(
        "📅 Выберите доступную дату консультации.",
        reply_markup=one(
            *dates,
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_date:"))
async def choose_date(callback: CallbackQuery, db):
    raw_date = callback.data.split(":", 1)[1]
    try:
        selected_date = date.fromisoformat(raw_date)
    except (TypeError, ValueError):
        await callback.answer(
            "Некорректная дата. Откройте список заново.",
            show_alert=True,
        )
        await booking_start(callback, db)
        return

    try:
        await _load_m2_slot_context(callback, db, allow_reserved=False)
        slots = await SlotService(db).get_available_slots(limit=100)
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await _show_slot_flow_unavailable(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening Telegram M2 slot times")
        await _show_slot_flow_unavailable(callback)
        return

    selected = [slot for slot in slots if slot.starts_at.date() == selected_date]
    if not selected:
        await callback.answer(
            "На эту дату свободное время уже закончилось.",
            show_alert=True,
        )
        await booking_start(callback, db)
        return
    buttons = [
        (
            f"{format_time(slot.starts_at)}–{format_time(slot.ends_at)}",
            f"consult_slot_select:{slot.id}",
        )
        for slot in selected
    ]
    await callback.message.edit_text(
        f"🕐 Выберите свободное время на {format_date(selected[0].starts_at)}.",
        reply_markup=one(
            *buttons,
            ("← Другие даты", "consult_slot_open"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def choose_slot(callback: CallbackQuery, db):
    raw_slot_id = callback.data.split(":", 1)[1]
    try:
        slot_id = int(raw_slot_id)
        if slot_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        await callback.answer(
            "Некорректное время. Выберите слот заново.",
            show_alert=True,
        )
        await booking_start(callback, db)
        return

    try:
        user, case, consultation = await _load_m2_slot_context(
            callback,
            db,
            allow_reserved=True,
        )
        consultation, slot = await ConsultationService(db).reserve_pre_payment_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
            actor_type="client",
            source="telegram",
        )
        await db.commit()
    except ConsultationSlotError as exc:
        await db.rollback()
        await callback.answer(str(exc), show_alert=True)
        await booking_start(callback, db)
        return
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await _show_slot_flow_unavailable(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while reserving Telegram M2 slot")
        await callback.message.edit_text(
            "Не удалось удержать выбранное время. "
            "Попробуйте выбрать другой слот.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_slot_open"),
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    hold_until = (
        format_time(slot.hold_expires_at)
        if slot.hold_expires_at is not None
        else "в течение 20 минут"
    )
    try:
        await callback.message.edit_text(
            "✅ Время временно удерживается за вами.\n\n"
            f"Дата: {format_date(slot.starts_at)}\n"
            f"Время: {format_time(slot.starts_at)}–{format_time(slot.ends_at)}\n"
            f"Резерв действует до: {hold_until}.\n\n"
            "Следующий шаг будет доступен в разделе «Мое дело».",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
    except Exception:
        logger.exception(
            "Telegram response failed after M2 slot reservation commit",
            extra={"consultation_id": consultation.id, "slot_id": slot.id},
        )


@router.callback_query(lambda c: c.data == "consult_slot_reserved_open")
async def reserved_slot_open(callback: CallbackQuery, db):
    try:
        user, _, consultation = await _load_m2_slot_context(
            callback,
            db,
            allow_reserved=True,
        )
        if _consultation_status(consultation.status) != ConsultationStatus.SLOT_RESERVED:
            raise ActiveConsultationConflictError(
                "Консультация уже перешла к другому шагу."
            )
        if consultation.slot_id is None:
            raise ConsultationSlotError(
                "У консультации нет удерживаемого слота."
            )
        service = ConsultationService(db)
        slot = await service.slots.get_slot(consultation.slot_id)
        current_hold = service._is_current_hold(slot, consultation, user.id)
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await _show_slot_flow_unavailable(callback)
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while opening reserved Telegram M2 slot")
        await _show_slot_flow_unavailable(callback)
        return

    if not current_hold:
        await callback.message.edit_text(
            "Срок удержания времени истёк. Выберите новый свободный слот.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_slot_open"),
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "🕐 Выбранное время консультации\n\n"
        f"Дата: {format_date(slot.starts_at)}\n"
        f"Время: {format_time(slot.starts_at)}–{format_time(slot.ends_at)}\n"
        f"Резерв действует до: {format_time(slot.hold_expires_at)}.",
        reply_markup=one(
            ("💳 Перейти к оплате", "consult_pay"),
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_subject_start")
async def subject_start(callback: CallbackQuery, db, state: FSMContext):
    try:
        user, active_case, active_consultation = await _load_owned_consultation(
            callback,
            db,
            allowed_statuses={ConsultationStatus.BOOKED},
        )
    except M2_DOMAIN_ERRORS:
        await callback.message.edit_text(
            "Активная консультация не найдена.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    cases = (
        await db.execute(
            select(Case)
            .where(Case.client_id == user.id)
            .order_by(Case.created_at.desc())
        )
    ).scalars().all()
    buttons = []
    for owned_case in cases[:10]:
        label = owned_case.title or owned_case.case_number
        buttons.append(
            (
                f"📁 {owned_case.case_number}: {label[:35]}",
                f"consult_subject_case:{owned_case.id}",
            )
        )
    buttons.append(("➕ Другое или новое дело", "consult_subject_new"))
    await state.clear()
    await state.update_data(
        case_id=active_case.id,
        consultation_id=active_consultation.id,
        legacy_booked_description=True,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_subject_choice)
    await callback.message.edit_text(
        "К какому вопросу относится консультация?\n\n"
        "Можно выбрать любое существующее дело — независимо от маршрута — "
        "либо описать новое или другое дело.",
        reply_markup=one(*buttons, ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))
async def subject_existing_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    try:
        case_id = int(callback.data.split(":", 1)[1])
        if case_id <= 0:
            raise ValueError
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        related_case = (
            await db.execute(
                select(Case).where(
                    Case.id == case_id,
                    Case.client_id == user.id,
                )
            )
        ).scalar_one_or_none()
        if related_case is None:
            raise ConsultationNotFoundError(
                "Выбранное дело не принадлежит клиенту."
            )
    except (TypeError, ValueError, ConsultationNotFoundError):
        await callback.answer(
            "Выбранное дело недоступно. Откройте список заново.",
            show_alert=True,
        )
        return

    await state.update_data(
        subject_type="existing_case",
        related_case_id=related_case.id,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Напишите, что именно вы хотите обсудить с юристом по выбранному делу.\n\n"
        "Не пересказывайте всё дело — сформулируйте конкретные вопросы, "
        "сомнения или новые обстоятельства.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consult_subject_new")
async def subject_new_case(callback: CallbackQuery, state: FSMContext):
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите новое или другое дело и укажите, что именно хотите "
        "обсудить с юристом.\n\n"
        "Это может быть ситуация, которая ранее не обсуждалась и не относится "
        "к маршруту 1.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consult_description_start")
async def legacy_description_start(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await begin_m2_description_flow(
        callback=callback,
        state=state,
        db=db,
        reason="Клиент продолжил оформление консультации",
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def save_description(message: Message, state: FSMContext, db):
    if not message.text or not message.text.strip():
        await message.answer(
            "Пожалуйста, отправьте описание текстовым сообщением."
        )
        return
    text = message.text.strip()
    data = await state.get_data()
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case = await ctx.case_service.get_active_case_for_user(user.id)
        if not _case_belongs_to_user(case, user):
            raise ConsultationNotFoundError(
                "Активное дело клиента не найдено."
            )
        if data.get("case_id") != case.id:
            raise ConsultationNotFoundError("FSM относится к другому делу.")
        service = ConsultationService(db)
        if data.get("legacy_booked_description"):
            consultation = await service.get_or_create_for_case(case)
            subject_type = data.get("subject_type")
            related_case_id = data.get("related_case_id")
            if subject_type == "existing_case":
                related_case = (
                    await db.execute(
                        select(Case).where(
                            Case.id == related_case_id,
                            Case.client_id == user.id,
                        )
                    )
                ).scalar_one_or_none()
                if related_case is None:
                    raise ConsultationNotFoundError(
                        "Связанное дело клиента не найдено."
                    )
            elif subject_type == "new_or_other":
                related_case_id = None
            else:
                raise ConsultationNotFoundError(
                    "Тип вопроса консультации не выбран."
                )
        else:
            consultation = await service.create_or_get_m2_consultation(
                case=case,
                actor_type="client",
                actor_id=user.id,
                source="telegram",
            )
            subject_type = "new_or_other"
            related_case_id = None
        if data.get("consultation_id") != consultation.id:
            raise ConsultationNotFoundError(
                "FSM относится к другой консультации."
            )
        await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description=text,
            subject_type=subject_type,
            related_case_id=related_case_id,
            actor_type="client",
            source="telegram",
        )
        await db.commit()
    except ConsultationDescriptionError:
        await db.rollback()
        await message.answer(
            "Не удалось сохранить описание. Проверьте текст и попробуйте ещё раз."
        )
        return
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        InvalidConsultationTransition,
    ):
        await db.rollback()
        await message.answer(
            "Не удалось продолжить оформление консультации. "
            "Попробуйте ещё раз немного позже."
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while saving a Telegram consultation description"
        )
        await message.answer(
            "Не удалось продолжить оформление консультации. "
            "Попробуйте ещё раз немного позже."
        )
        return

    await state.clear()
    await message.answer(
        DESCRIPTION_SAVED_TEXT,
        reply_markup=one(
            ("📎 Приложить документы", "m2_documents_open"),
            ("⏭ Пропустить", "m2_documents_skip"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def consultation_booked_open(callback: CallbackQuery, db):
    try:
        _, _, consultation = await _load_owned_consultation(callback, db)
    except M2_DOMAIN_ERRORS:
        await callback.message.edit_text(
            "Консультация не найдена или уже недоступна.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    status = _consultation_status(consultation.status)
    date_text = (
        consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")
        if consultation.scheduled_at
        else "уточняется"
    )
    end_text = (
        format_time(consultation.slot.ends_at)
        if consultation.slot is not None
        else None
    )
    subject_text = consultation.client_description or "вопрос ещё не указан"
    lawyer_name = (
        consultation.lawyer.full_name
        if consultation.lawyer is not None
        else "уточняется"
    )
    lines = [
        "👨‍⚖ Консультация",
        "",
        f"Дата и начало: {date_text}",
    ]
    if end_text:
        lines.append(f"Окончание: {end_text}")
    lines.extend(
        [
            f"Юрист: {lawyer_name}",
            f"Формат: {consultation.consultation_type or 'уточняется'}",
            f"Статус: {_consultation_status_title(consultation.status)}",
            f"Вопрос: {subject_text[:500]}",
        ]
    )
    buttons = []
    if status == ConsultationStatus.BOOKED and not consultation.client_description:
        buttons.append(("📝 Указать вопрос", "consult_subject_start"))
    buttons.append(("📄 Добавить документы", "documents_open"))
    if status in {
        ConsultationStatus.PAID_PENDING_CONFIRMATION,
        ConsultationStatus.CONFIRMED,
        ConsultationStatus.BOOKED,
    }:
        buttons.extend(
            [
                ("🔄 Изменить время", "consult_reschedule"),
                ("❌ Отменить консультацию", "consult_cancel"),
            ]
        )
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )
    await callback.message.edit_text(
        "\n".join(lines),
        reply_markup=one(*buttons),
    )


async def _load_reschedule_slots(callback: CallbackQuery, db):
    user, case, consultation = await _load_owned_consultation(
        callback,
        db,
        allowed_statuses={
            ConsultationStatus.CONFIRMED,
            ConsultationStatus.BOOKED,
        },
    )
    if consultation.lawyer_id is None:
        raise ConsultationSlotError(
            "Для консультации не определён текущий юрист."
        )
    slots = await SlotService(db).get_available_slots(
        lawyer_id=consultation.lawyer_id,
        limit=100,
    )
    return user, case, consultation, slots


@router.callback_query(lambda c: c.data == "consult_reschedule")
async def consult_reschedule(callback: CallbackQuery, db):
    try:
        _, _, consultation, slots = await _load_reschedule_slots(callback, db)
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            "Изменение времени сейчас недоступно.",
            reply_markup=one(
                ("📋 Детали консультации", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    if not slots:
        await callback.message.edit_text(
            "У текущего юриста пока нет другого свободного времени. "
            "Текущая запись сохранена.",
            reply_markup=one(
                ("🔄 Обновить", "consult_reschedule"),
                ("💬 Связаться с менеджером", "contact_lawyer"),
                ("⬅ Оставить текущее время", "consultation_booked_open"),
            ),
        )
        return

    dates = []
    seen = set()
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key not in seen:
            seen.add(key)
            dates.append(
                (
                    f"📅 {format_date(slot.starts_at)}",
                    f"consult_reschedule_date:{key}",
                )
            )
    await callback.message.edit_text(
        "🔄 Изменение времени\n\n"
        "Выберите новую дату. Текущая запись сохранится до успешного "
        "подтверждения нового времени.",
        reply_markup=one(
            *dates,
            ("⬅ Оставить текущее время", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_date:"))
async def consult_reschedule_date(callback: CallbackQuery, db):
    try:
        selected_date = date.fromisoformat(callback.data.split(":", 1)[1])
        _, _, _, slots = await _load_reschedule_slots(callback, db)
        await db.commit()
    except (TypeError, ValueError, *M2_DOMAIN_ERRORS):
        await db.rollback()
        await callback.answer(
            "Дата недоступна. Откройте список переноса заново.",
            show_alert=True,
        )
        return

    selected = [slot for slot in slots if slot.starts_at.date() == selected_date]
    if not selected:
        await callback.answer(
            "На эту дату свободного времени больше нет.",
            show_alert=True,
        )
        await consult_reschedule(callback, db)
        return
    buttons = [
        (
            f"{format_time(slot.starts_at)}–{format_time(slot.ends_at)}",
            f"consult_reschedule_slot:{slot.id}",
        )
        for slot in selected
    ]
    await callback.message.edit_text(
        f"Выберите новое время на {format_date(selected[0].starts_at)}.\n\n"
        "Текущая запись будет освобождена только после успешной замены.",
        reply_markup=one(
            *buttons,
            ("← Другие даты", "consult_reschedule"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_reschedule_slot:"))
async def consult_reschedule_slot(callback: CallbackQuery, db):
    try:
        new_slot_id = int(callback.data.split(":", 1)[1])
        if new_slot_id <= 0:
            raise ValueError
        user, case, consultation = await _load_owned_consultation(
            callback,
            db,
            allowed_statuses={
                ConsultationStatus.CONFIRMED,
                ConsultationStatus.BOOKED,
            },
            for_update=True,
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
            "Некорректное время. Откройте список переноса заново.",
            show_alert=True,
        )
        return
    except ConsultationSlotError as exc:
        await db.rollback()
        await callback.message.edit_text(
            f"{str(exc)}\n\nВыберите другой свободный вариант.",
            reply_markup=one(
                ("📅 Выбрать другое время", "consult_reschedule"),
                ("📋 Текущая запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            "Перенос недоступен. Текущая запись не изменена.",
            reply_markup=one(
                ("📋 Текущая запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while rescheduling consultation")
        await callback.message.edit_text(
            "Не удалось изменить время. Текущая запись сохранена.",
            reply_markup=one(
                ("📋 Текущая запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
        return

    await callback.message.edit_text(
        "✅ Время консультации изменено\n\n"
        f"Дата: {format_date(new_slot.starts_at)}\n"
        f"Время: {format_time(new_slot.starts_at)}–"
        f"{format_time(new_slot.ends_at)}\n\n"
        "Оплата сохранена. Новая запись доступна в разделе «Моё дело».",
        reply_markup=one(
            ("📋 Детали консультации", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel")
async def consult_cancel(callback: CallbackQuery, db):
    try:
        _, _, consultation = await _load_owned_consultation(
            callback,
            db,
            allowed_statuses={
                ConsultationStatus.PAID_PENDING_CONFIRMATION,
                ConsultationStatus.CONFIRMED,
                ConsultationStatus.BOOKED,
            },
        )
    except M2_DOMAIN_ERRORS:
        await callback.message.edit_text(
            "Отмена сейчас недоступна. Откройте актуальную карточку консультации.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    date_text = (
        format_date(consultation.scheduled_at)
        if consultation.scheduled_at
        else "уточняется"
    )
    time_text = (
        format_time(consultation.scheduled_at)
        if consultation.scheduled_at
        else "уточняется"
    )
    lawyer_name = (
        consultation.lawyer.full_name
        if consultation.lawyer is not None
        else "уточняется"
    )
    await callback.message.edit_text(
        "Вы действительно хотите отменить консультацию?\n\n"
        f"Дата: {date_text}\n"
        f"Время: {time_text}\n"
        f"Юрист: {lawyer_name}\n\n"
        "Отмена записи не означает автоматический возврат средств. "
        "Если консультация оплачена, условия возврата проверяются отдельно.",
        reply_markup=one(
            ("✅ Да, отменить", "consult_cancel_confirm"),
            ("⬅ Оставить запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel_confirm")
async def confirm_consultation_cancel(callback: CallbackQuery, db):
    try:
        user, case, consultation = await _load_owned_consultation(
            callback,
            db,
            allowed_statuses={
                ConsultationStatus.PAID_PENDING_CONFIRMATION,
                ConsultationStatus.CONFIRMED,
                ConsultationStatus.BOOKED,
            },
            for_update=True,
        )
        await ConsultationService(db).cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил отмену консультации в Telegram",
        )
        await db.commit()
    except M2_DOMAIN_ERRORS:
        await db.rollback()
        await callback.message.edit_text(
            "Консультация уже отменена, завершена или недоступна.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while cancelling consultation")
        await callback.message.edit_text(
            "Не удалось отменить консультацию. Попробуйте немного позже.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await callback.message.edit_text(
        "Консультация отменена. Выбранное время освобождено.\n\n"
        "Если консультация была оплачена, условия возможного возврата "
        "уточняются отдельно.",
        reply_markup=one(
            ("💬 Связаться с менеджером", "contact_lawyer"),
            ("🏠 Главная", "nav_home"),
        ),
    )
