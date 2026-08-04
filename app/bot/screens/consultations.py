import logging

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
logger = logging.getLogger(__name__)

CONSULTATION_STATUS_LABELS = {
    ConsultationStatus.DESCRIPTION_PENDING: "Нужно описать вопрос",
    ConsultationStatus.DOCUMENTS_OPTIONAL: "Можно добавить документы",
    ConsultationStatus.SLOT_PENDING: "Нужно выбрать время",
    ConsultationStatus.SLOT_RESERVED: "Время временно зарезервировано",
    ConsultationStatus.PAYMENT_PENDING: "Ожидается подтверждение записи",
    ConsultationStatus.BOOKED: "Консультация подтверждена",
    ConsultationStatus.DONE: "Консультация проведена",
    ConsultationStatus.CLIENT_NO_SHOW: "Клиент не подключился",
    ConsultationStatus.LAWYER_NO_SHOW: "Юрист не подключился",
    ConsultationStatus.CANCELLED: "Консультация отменена",
    ConsultationStatus.RESCHEDULED: "Консультация перенесена",
    ConsultationStatus.CLOSED: "Консультация закрыта",
}


def format_date(value):
    return value.strftime("%d.%m.%Y")


def format_time(value):
    return value.strftime("%H:%M")


def format_datetime(value):
    return value.strftime("%d.%m.%Y %H:%M")


def consultation_status_label(status) -> str:
    try:
        normalized = ConsultationStatus(str(status))
    except ValueError:
        return "Статус уточняется"
    return CONSULTATION_STATUS_LABELS.get(normalized, "Статус уточняется")


def booking_recovery_buttons() -> tuple[tuple[str, str], ...]:
    return (
        ("📅 Выбрать дату и время", "consult_booking_start"),
        ("💬 Связаться с юристом", "contact_lawyer"),
        ("🏠 Главная", "nav_home"),
    )


async def ensure_booking_case(ctx, user):
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case:
        return case
    return await ctx.case_service.create_case(
        client=user,
        route=None,
        title="Запись на юридическую консультацию",
    )


def date_buttons(slots, callback_prefix: str):
    buttons = []
    seen = set()
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key not in seen:
            seen.add(key)
            buttons.append(
                (f"📅 {format_date(slot.starts_at)}", f"{callback_prefix}:{key}")
            )
    return buttons


@router.callback_query(
    lambda c: c.data in {"consult_booking_start", "consult_slot_open"}
)
async def booking_start(callback: CallbackQuery, db):
    slots = await SlotService(db).get_available_slots(limit=60)
    if not slots:
        await callback.message.edit_text(
            "Сейчас свободных слотов нет. Новые даты появятся здесь после добавления юристами.\n\n"
            "Можно вернуться позже или написать юристу.",
            reply_markup=one(
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "📅 Выберите доступную дату консультации.",
        reply_markup=one(
            *date_buttons(slots, "consult_date"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_date:"))
async def choose_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    slots = await SlotService(db).get_available_slots(limit=100)
    selected = [
        slot
        for slot in slots
        if slot.starts_at.date().isoformat() == date_key
    ]
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
            ("← Другие даты", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_slot_select:"))
async def choose_slot(callback: CallbackQuery, db):
    try:
        slot_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.edit_text(
            "Эта кнопка выбора времени больше не актуальна.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case = await ensure_booking_case(ctx, user)
        consultation = await ConsultationService(db).get_or_create_for_case(case)
        consultation, slot = await ConsultationService(db).reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
        )
        await db.commit()
    except SlotUnavailableError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        await booking_start(callback, db)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось зарезервировать консультацию")
        await callback.message.edit_text(
            "Не удалось зарезервировать время. Данные не изменены.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return

    hold_until = (
        format_datetime(slot.hold_expires_at)
        if slot.hold_expires_at
        else "в течение 10 минут"
    )
    await callback.message.edit_text(
        "✅ Время временно удерживается за вами.\n\n"
        f"Дата: {format_date(slot.starts_at)}\n"
        f"Время: {format_time(slot.starts_at)}–{format_time(slot.ends_at)}\n"
        f"Резерв до: {hold_until}\n\n"
        "Подтвердите запись оплатой в течение 10 минут. "
        "После истечения таймера слот автоматически снова станет доступен.",
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
        buttons.append(
            (
                f"📁 {case.case_number}: {label[:35]}",
                f"consult_subject_case:{case.id}",
            )
        )
    buttons.append(("➕ Другое или новое дело", "consult_subject_new"))
    await state.set_state(ConsultationDescriptionStates.waiting_subject_choice)
    await callback.message.edit_text(
        "К какому вопросу относится консультация?\n\n"
        "Можно выбрать своё существующее дело либо описать новую ситуацию.",
        reply_markup=one(
            *buttons,
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data.startswith("consult_subject_case:"))
async def subject_existing_case(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    try:
        case_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        case_id = 0
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    related_case = (
        await db.execute(
            select(Case)
            .where(Case.id == case_id)
            .where(Case.client_id == user.id)
        )
    ).scalar_one_or_none()
    if not related_case:
        await state.clear()
        await callback.message.edit_text(
            "Выбранное дело больше недоступно. Откройте актуальный список.",
            reply_markup=one(
                ("🔄 Выбрать дело", "consult_subject_start"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.update_data(
        subject_type="existing_case",
        related_case_id=related_case.id,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        f"📝 Вопрос по делу {related_case.case_number}\n\n"
        "Напишите, что именно хотите обсудить с юристом. "
        "Сформулируйте конкретные вопросы, сомнения или новые обстоятельства.",
        reply_markup=one(
            ("Выбрать другое дело", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_subject_new")
async def subject_new_case(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        subject_type="new_or_other",
        related_case_id=None,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите новую или другую ситуацию и укажите, что именно хотите обсудить с юристом.",
        reply_markup=one(
            ("Выбрать существующее дело", "consult_subject_start"),
            ("Отменить действие", "nav_cancel"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_description_start")
async def legacy_description_start(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.update_data(
        subject_type="new_or_other",
        related_case_id=None,
    )
    await state.set_state(ConsultationDescriptionStates.waiting_description)
    await callback.message.edit_text(
        "📝 Опишите ситуацию и конкретный вопрос для юриста.",
        reply_markup=one(
            ("Отменить действие", "nav_cancel"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(ConsultationDescriptionStates.waiting_description)
async def save_description(message: Message, state: FSMContext, db):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer(
            "Опишите вопрос подробнее — минимум 20 символов.",
            reply_markup=one(("Отменить действие", "nav_cancel")),
        )
        return
    if len(text) > 4000:
        await message.answer(
            "Сократите описание до 4000 символов.",
            reply_markup=one(("Отменить действие", "nav_cancel")),
        )
        return

    data = await state.get_data()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    try:
        case = await ensure_booking_case(ctx, user)
        service = ConsultationService(db)
        consultation = await service.get_or_create_for_case(case)
        if consultation.status != ConsultationStatus.BOOKED:
            await db.rollback()
            await message.answer(
                "Сначала выберите и подтвердите время консультации.\n\n"
                "После подтверждения вернитесь к описанию вопроса.",
                reply_markup=one(
                    ("📅 Выбрать дату и время", "consult_booking_start"),
                    ("🏠 Главная", "nav_home"),
                ),
            )
            return
        await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description=text,
            subject_type=data.get("subject_type", "new_or_other"),
            related_case_id=data.get("related_case_id"),
        )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await state.clear()
        await message.answer(
            f"Вопрос не сохранён: {error}",
            reply_markup=one(
                ("🔄 Выбрать дело заново", "consult_subject_start"),
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить описание консультации")
        await message.answer(
            "Вопрос временно не сохранён. Текст можно отправить повторно.",
            reply_markup=one(
                ("Отменить действие", "nav_cancel"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await state.clear()
    await message.answer(
        "✅ Вопрос сохранён и будет передан юристу до встречи.\n\n"
        "При необходимости добавьте документы, которые помогут подготовиться к консультации.",
        reply_markup=one(
            ("📄 Добавить документы", "documents_open"),
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consultation_booked_open")
async def consultation_booked_open(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Активная запись не найдена. Возможно, она уже завершена или отменена.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Активная запись не найдена. Выберите новое время или свяжитесь с юристом.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return

    date_text = (
        consultation.scheduled_at.strftime("%d.%m.%Y %H:%M")
        if consultation.scheduled_at
        else "ещё не выбраны"
    )
    subject_text = consultation.client_description or "вопрос ещё не указан"
    status_text = consultation_status_label(consultation.status)

    buttons = []
    if consultation.status == ConsultationStatus.BOOKED:
        buttons.extend(
            [
                ("📝 Указать/изменить вопрос", "consult_subject_start"),
                ("📄 Добавить документы", "documents_open"),
                ("🔄 Перенести консультацию", "consult_reschedule"),
                ("Отменить консультацию", "consult_cancel"),
            ]
        )
    elif consultation.status == ConsultationStatus.PAYMENT_PENDING:
        buttons.extend(
            [
                ("Продолжить подтверждение", "consult_pay"),
                ("Выбрать другое время", "consult_booking_start"),
            ]
        )
    else:
        buttons.extend(
            [
                ("📅 Выбрать дату и время", "consult_booking_start"),
                ("📝 Указать вопрос", "consult_subject_start"),
                ("📄 Добавить документы", "documents_open"),
            ]
        )
    buttons.extend(
        [
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ]
    )

    await callback.message.edit_text(
        "👨‍⚖ Консультация\n\n"
        f"Дата и время: {date_text}\n"
        f"Статус: {status_text}\n"
        f"Вопрос: {subject_text[:500]}",
        reply_markup=one(*buttons),
    )


@router.callback_query(lambda c: c.data == "consult_reschedule")
async def consult_reschedule(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Нет активной консультации для переноса.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation or consultation.status != ConsultationStatus.BOOKED:
        await callback.message.edit_text(
            "Перенести можно только подтверждённую консультацию.",
            reply_markup=one(
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    slots = await SlotService(db).get_available_slots(limit=100)
    if not slots:
        await callback.message.edit_text(
            "Сейчас нет свободного времени для переноса. Текущая запись сохранена.",
            reply_markup=one(
                ("Назад", "consultation_booked_open"),
                ("💬 Связаться с юристом", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "🔄 Выберите новую дату. Повторная оплата не потребуется.\n\n"
        "Текущий слот останется за вами до успешного выбора нового времени.",
        reply_markup=one(
            *date_buttons(slots, "consult_reschedule_date"),
            ("Назад", "consultation_booked_open"),
        ),
    )


@router.callback_query(
    lambda c: c.data.startswith("consult_reschedule_date:")
)
async def choose_reschedule_date(callback: CallbackQuery, db):
    date_key = callback.data.split(":", 1)[1]
    slots = await SlotService(db).get_available_slots(limit=100)
    selected = [
        slot
        for slot in slots
        if slot.starts_at.date().isoformat() == date_key
    ]
    if not selected:
        await callback.answer(
            "На эту дату свободное время уже закончилось.",
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
        f"🕐 Выберите новое время на {format_date(selected[0].starts_at)}.",
        reply_markup=one(
            *buttons,
            ("← Другие даты", "consult_reschedule"),
            ("Отмена", "consultation_booked_open"),
        ),
    )


@router.callback_query(
    lambda c: c.data.startswith("consult_reschedule_slot:")
)
async def choose_reschedule_slot(callback: CallbackQuery, db):
    try:
        new_slot_id = int(callback.data.split(":", 1)[1])
    except (TypeError, ValueError):
        await callback.message.edit_text(
            "Эта кнопка переноса больше не актуальна.",
            reply_markup=one(
                ("🔄 Выбрать новую дату", "consult_reschedule"),
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ),
        )
        return

    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Активная консультация не найдена.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    service = ConsultationService(db)
    consultation = await service.get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Активная консультация не найдена.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    try:
        consultation, new_slot = await service.reschedule_booked(
            consultation=consultation,
            case=case,
            client_id=user.id,
            new_slot_id=new_slot_id,
        )
        await db.commit()
    except (SlotUnavailableError, ValueError) as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        await consult_reschedule(callback, db)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось перенести консультацию")
        await callback.message.edit_text(
            "Перенос временно не выполнен. Текущая запись сохранена.",
            reply_markup=one(
                ("🔄 Повторить перенос", "consult_reschedule"),
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    await callback.message.edit_text(
        "✅ Консультация перенесена без повторной оплаты.\n\n"
        f"Новая дата: {format_date(new_slot.starts_at)}\n"
        f"Новое время: {format_time(new_slot.starts_at)}–"
        f"{format_time(new_slot.ends_at)}\n\n"
        "Предыдущий слот освобождён.",
        reply_markup=one(
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel")
async def consult_cancel(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Нет активной консультации для отмены.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Нет активной консультации для отмены.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return

    if consultation.status == ConsultationStatus.BOOKED:
        text = (
            "⚠️ Подтвердите отмену подтверждённой консультации.\n\n"
            "После подтверждения слот будет освобождён, а платёж перейдёт "
            "в статус ожидания возврата. Администратор выполнит возврат "
            "через платёжного провайдера и зафиксирует результат в системе."
        )
    else:
        text = (
            "⚠️ Подтвердите отмену консультации.\n\n"
            "Текущий резерв времени будет освобождён."
        )
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("Да, отменить", "consult_cancel_confirm"),
            ("Нет, сохранить запись", "consultation_booked_open"),
        ),
    )


@router.callback_query(lambda c: c.data == "consult_cancel_confirm")
async def consult_cancel_confirm(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if not case:
        await callback.message.edit_text(
            "Активная консультация уже отсутствует.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    service = ConsultationService(db)
    consultation = await service.get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Активная консультация уже отсутствует.",
            reply_markup=one(*booking_recovery_buttons()),
        )
        return
    was_booked = consultation.status == ConsultationStatus.BOOKED
    try:
        await service.cancel(
            consultation=consultation,
            case=case,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент подтвердил отмену консультации в Telegram",
        )
        await db.commit()
    except (LookupError, ValueError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Отмена не выполнена: {error}",
            reply_markup=one(
                ("🔄 Повторить отмену", "consult_cancel_confirm"),
                ("Нет, сохранить запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось отменить консультацию")
        await callback.message.edit_text(
            "Отмена временно не выполнена. Текущая запись сохранена.",
            reply_markup=one(
                ("🔄 Повторить отмену", "consult_cancel_confirm"),
                ("👨‍⚖ Открыть запись", "consultation_booked_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if was_booked:
        text = (
            "🧾 Консультация отменена, слот освобождён.\n\n"
            "Заявка на возврат оплаты передана администратору. "
            "После фактического возврата вы получите отдельное уведомление."
        )
    else:
        text = "Консультация отменена. Слот снова доступен для записи."
    await callback.message.edit_text(
        text,
        reply_markup=one(
            ("💳 Мои оплаты", "payments_open"),
            ("📅 Выбрать другое время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )
