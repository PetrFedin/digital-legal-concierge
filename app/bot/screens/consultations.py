from collections import defaultdict
from datetime import timezone
import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationDescriptionError,
    ConsultationNotFoundError,
    ConsultationService,
)
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.consultations.state_machine import InvalidConsultationTransition
from app.domain.statuses.case_statuses import RouteCode
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
    ActiveConsultationConflictError,
    InvalidConsultationTransition,
)


def format_date(value):
    return value.strftime("%d.%m.%Y")


def format_time(value):
    return value.strftime("%H:%M")


async def ensure_booking_case(ctx, user):
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        return case
    return await ctx.case_service.create_case(
        client=user,
        route=None,
        title="Запись на юридическую консультацию",
    )


def _case_belongs_to_user(case, user) -> bool:
    return bool(case and case.client_id == user.id)


async def _set_description_state(state: FSMContext, *, case_id: int, consultation_id: int):
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
        case = await ctx.get_or_create_active_case_for_user(user)
        if not _case_belongs_to_user(case, user):
            raise ConsultationNotFoundError("Дело не принадлежит текущему клиенту.")
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
            "Не удалось продолжить оформление консультации. Попробуйте ещё раз немного позже.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while starting the Telegram M2 description flow"
        )
        await callback.message.edit_text(
            "Не удалось продолжить оформление консультации. Попробуйте ещё раз немного позже.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    try:
        status = ConsultationStatus(consultation.status)
    except (TypeError, ValueError):
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
        text = "Запись на консультацию уже оформлена. Откройте её в разделе «Мое дело»."
    await callback.message.edit_text(
        text,
        reply_markup=one(("📁 Мое дело", "my_case_open"), ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data in {"consult_booking_start", "consult_slot_open"})
async def booking_start(callback: CallbackQuery, db):
    slots = await SlotService(db).get_available_slots(limit=60)
    if not slots:
        await callback.message.edit_text(
            "Сейчас свободных слотов нет. Юристы добавят новые даты, и они появятся здесь.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    dates = []
    seen = set()
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key not in seen:
            seen.add(key)
            dates.append((f"📅 {format_date(slot.starts_at)}", f"consult_date:{key}"))
    await callback.message.edit_text(
        "📅 Выберите доступную дату консультации.",
        reply_markup=one(*dates, ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data.startswith("consult_date:"))
async def choose_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    slots = await SlotService(db).get_available_slots(limit=100)
    selected = [slot for slot in slots if slot.starts_at.date().isoformat() == date_key]
    if not selected:
        await callback.answer("На эту дату свободное время уже закончилось.", show_alert=True)
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
        reply_markup=one(*buttons, ("← Другие даты", "consult_booking_start")),
    )


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def choose_slot(callback: CallbackQuery, db):
    slot_id = int(callback.data.split(":", 1)[1])
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ensure_booking_case(ctx, user)
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    try:
        consultation, slot = await ConsultationService(db).reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
        )
    except SlotUnavailableError as exc:
        await db.rollback()
        await callback.answer(str(exc), show_alert=True)
        await booking_start(callback, db)
        return
    await db.commit()
    await callback.message.edit_text(
        "✅ Время временно удерживается за вами.\n\n"
        f"Дата: {format_date(slot.starts_at)}\n"
        f"Время: {format_time(slot.starts_at)}–{format_time(slot.ends_at)}\n\n"
        "Чтобы слот не занимали без намерения прийти, запись подтверждается оплатой. "
        "Резерв действует 20 минут.",
        reply_markup=one(
            ("💳 Оплатить и подтвердить", "consult_pay"),
            ("Выбрать другое время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_subject_start")
async def subject_start(callback: CallbackQuery, db, state: FSMContext):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    active_case = await ctx.case_service.get_active_case_for_user(user.id)
    if not _case_belongs_to_user(active_case, user):
        await callback.message.edit_text(
            "Активная консультация не найдена.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    active_consultation = (
        await db.execute(
            select(Consultation)
            .where(Consultation.case_id == active_case.id)
            .where(Consultation.status == ConsultationStatus.BOOKED.value)
            .order_by(Consultation.created_at.desc())
        )
    ).scalars().first()
    if not active_consultation:
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
    for case in cases[:10]:
        label = case.title or case.case_number
        buttons.append((f"📁 {case.case_number}: {label[:35]}", f"consult_subject_case:{case.id}"))
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
        "Можно выбрать любое существующее дело — независимо от маршрута — либо описать новое/другое дело.",
        reply_markup=one(*buttons, ("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))
async def subject_existing_case(callback: CallbackQuery, state: FSMContext):
    case_id = int(callback.data.split(":", 1)[1])
    await state.update_data(subject_type="existing_case", related_case_id=case_id)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Напишите, что именно вы хотите обсудить с юристом по выбранному делу.\n\n"
        "Не пересказывайте всё дело — сформулируйте конкретные вопросы, сомнения или новые обстоятельства.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consult_subject_new")
async def subject_new_case(callback: CallbackQuery, state: FSMContext):
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите новое или другое дело и укажите, что именно хотите обсудить с юристом.\n\n"
        "Это может быть ситуация, которая ранее не обсуждалась и не относится к маршруту 1.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consult_description_start")
async def legacy_description_start(callback: CallbackQuery, state: FSMContext, db):
    await begin_m2_description_flow(
        callback=callback,
        state=state,
        db=db,
        reason="Клиент продолжил оформление консультации",
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def save_description(message: Message, state: FSMContext, db):
    if not message.text or not message.text.strip():
        await message.answer("Пожалуйста, отправьте описание текстовым сообщением.")
        return
    text = message.text.strip()
    data = await state.get_data()
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_message(message)
        case = await ctx.case_service.get_active_case_for_user(user.id)
        if not _case_belongs_to_user(case, user):
            raise ConsultationNotFoundError("Активное дело клиента не найдено.")
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
            raise ConsultationNotFoundError("FSM относится к другой консультации.")
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
            "Не удалось продолжить оформление консультации. Попробуйте ещё раз немного позже."
        )
        return
    except Exception:
        await db.rollback()
        logger.exception(
            "Unexpected error while saving a Telegram consultation description"
        )
        await message.answer(
            "Не удалось продолжить оформление консультации. Попробуйте ещё раз немного позже."
        )
        return

    await state.clear()
    await message.answer(
        DESCRIPTION_SAVED_TEXT,
        reply_markup=one(("🏠 Главная", "nav_home")),
    )


@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def consultation_booked_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text("Нет активной записи.", reply_markup=one(("🏠 Главная", "nav_home")))
        return
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    date_text = consultation.scheduled_at.strftime("%d.%m.%Y %H:%M") if consultation.scheduled_at else "уточняется"
    subject_text = consultation.client_description or "вопрос ещё не указан"
    await callback.message.edit_text(
        "👨‍⚖ Консультация\n\n"
        f"Дата и время: {date_text}\n"
        f"Статус: {consultation.status}\n"
        f"Вопрос: {subject_text[:500]}",
        reply_markup=one(
            ("📝 Указать/изменить вопрос", "consult_subject_start"),
            ("📄 Добавить документы", "documents_open"),
            ("Перенести консультацию", "consult_reschedule"),
            ("Отменить консультацию", "consult_cancel"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_reschedule")
async def consult_reschedule(callback: CallbackQuery):
    await callback.message.edit_text(
        "Выберите новую дату и время. Текущий слот будет освобождён после выбора нового.",
        reply_markup=one(("📅 Выбрать дату", "consult_booking_start"), ("Назад", "consultation_booked_open")),
    )


@router.callback_query(lambda c: c.data == "consult_cancel")
async def consult_cancel(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        consultation = await ConsultationService(db).get_or_create_for_case(case)
        await ConsultationService(db).cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент отменил консультацию",
        )
        await db.commit()
    await callback.message.edit_text(
        "Консультация отменена. Слот снова доступен для записи.",
        reply_markup=one(("🏠 Главная", "nav_home")),
    )
