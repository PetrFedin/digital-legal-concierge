from aiogram import Router
from aiogram.types import CallbackQuery, Message
from aiogram.fsm.context import FSMContext

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService
from app.domain.statuses.case_statuses import CaseStatus

router = Router()


@router.callback_query(lambda c: c.data == "consult_description_start")
async def start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите ситуацию свободным текстом.",
        reply_markup=one(("Отмена", "nav_home")),
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def desc(message: Message, state: FSMContext, db):
    if len(message.text.strip()) < 10:
        await message.answer("Опишите чуть подробнее.")
        return
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await ctx.get_or_create_active_case_for_user(user)
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    await ConsultationService(db).save_description(
        consultation=consultation,
        case=case,
        client_id=user.id,
        description=message.text.strip(),
    )
    await ctx.case_service.change_status(
        case=case,
        next_status=CaseStatus.M2_DOCUMENTS_OPTIONAL,
        actor_type="client",
        actor_id=user.id,
        force=True,
    )
    await db.commit()
    await state.clear()
    await message.answer(
        "✅ Описание сохранено.",
        reply_markup=one(
            ("📄 Загрузить документы", "documents_open"),
            ("Пропустить документы", "doc_skip_m2"),
            ("📅 Выбрать время", "consult_slot_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_slot_open")
async def slots(callback: CallbackQuery):
    slots_list = SlotService().get_available_slots()
    await callback.message.edit_text(
        "📅 Выберите удобное время консультации.",
        reply_markup=one(*[(slot.title, f"consult_slot_select:{slot.code}") for slot in slots_list], ("📁 Мое дело", "my_case_open")),
    )


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def choose(callback: CallbackQuery, db):
    slot = SlotService().find_slot(callback.data.split(":")[1])
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    await ConsultationService(db).reserve_slot(
        consultation=consultation,
        case=case,
        client_id=user.id,
        scheduled_at=slot.starts_at,
    )
    await ctx.case_service.change_status(
        case=case,
        next_status=CaseStatus.M2_PAYMENT_PENDING,
        actor_type="client",
        actor_id=user.id,
        force=True,
    )
    await db.commit()
    await callback.message.edit_text(
        f"✅ Слот зарезервирован: {slot.title}\n\nДля подтверждения оплатите консультацию.",
        reply_markup=one(("💳 Оплатить консультацию", "consult_pay"), ("Выбрать другое время", "consult_slot_open")),
    )

@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def consultation_booked_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text('Нет активного обращения.', reply_markup=one(('🏠 Главная', 'nav_home')))
        return
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    date_text = consultation.scheduled_at.strftime('%d.%m.%Y %H:%M') if consultation.scheduled_at else 'уточняется'
    await callback.message.edit_text(
        f'👨‍⚖ Консультация назначена\n\nДата и время: {date_text}\nФормат: онлайн\n\nПеред консультацией можно добавить документы или написать вопрос.',
        reply_markup=one(('📄 Добавить документы', 'documents_open'), ('💬 Написать вопрос', 'message_create'), ('Перенести консультацию', 'consult_reschedule'), ('Отменить консультацию', 'consult_cancel'), ('📁 Мое дело', 'my_case_open')),
    )

@router.callback_query(lambda c: c.data == "consult_reschedule")
async def consult_reschedule(callback: CallbackQuery):
    await callback.message.edit_text('Выберите новое время консультации.', reply_markup=one(('📅 Выбрать время', 'consult_slot_open'), ('📁 Мое дело', 'my_case_open')))

@router.callback_query(lambda c: c.data == "consult_cancel")
async def consult_cancel(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        await ctx.case_service.change_status(case=case, next_status=CaseStatus.M2_CLOSED, actor_type='client', actor_id=user.id, force=True, comment='Клиент отменил консультацию')
        await db.commit()
    await callback.message.edit_text('Консультация отменена. Обращение закрыто.', reply_markup=one(('🏠 Главная', 'nav_home')))
