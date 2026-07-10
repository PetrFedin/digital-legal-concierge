from collections import defaultdict
from datetime import timezone

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.case import Case

router = Router()


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
async def legacy_description_start(callback: CallbackQuery, state: FSMContext):
    await state.update_data(subject_type="new_or_other", related_case_id=None)
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите ситуацию и конкретный вопрос для юриста.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def save_description(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer("Опишите вопрос чуть подробнее — минимум 20 символов.")
        return
    data = await state.get_data()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ensure_booking_case(ctx, user)
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    if consultation.status != ConsultationStatus.BOOKED:
        await message.answer(
            "Сначала выберите и оплатите время консультации.",
            reply_markup=one(("📅 Выбрать дату и время", "consult_booking_start")),
        )
        return
    await ConsultationService(db).save_description(
        consultation=consultation,
        case=case,
        client_id=user.id,
        description=text,
        subject_type=data.get("subject_type", "new_or_other"),
        related_case_id=data.get("related_case_id"),
    )
    await db.commit()
    await state.clear()
    await message.answer(
        "✅ Вопрос сохранён и будет передан юристу до встречи.\n\n"
        "При необходимости добавьте документы, которые помогут подготовиться к консультации.",
        reply_markup=one(
            ("📄 Добавить документы", "documents_open"),
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        ),
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
