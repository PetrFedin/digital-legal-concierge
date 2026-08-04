from __future__ import annotations

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import transition_allowed
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation

router = Router()

_REUSABLE_M2_STATUSES = {
    CaseStatus.M2_CONSULTATION_ROUTE,
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
    CaseStatus.M2_PAYMENT_PENDING,
    CaseStatus.M2_CONSULTATION_BOOKED,
    CaseStatus.M2_CONSULTATION_DONE,
}


def _status(case) -> CaseStatus:
    return case.status if isinstance(case.status, CaseStatus) else CaseStatus(str(case.status))


def _format_slot(slot) -> str:
    return (
        f"{slot.starts_at.strftime('%d.%m.%Y')} · "
        f"{slot.starts_at.strftime('%H:%M')}–{slot.ends_at.strftime('%H:%M')}"
    )


async def _active_case(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _ensure_consultation_case(ctx: BotContextService, user):
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case and _status(case) in _REUSABLE_M2_STATUSES:
        return case

    if case and transition_allowed(case.status, CaseStatus.M2_DESCRIPTION_PENDING):
        await ctx.case_service.transfer_to_m2(
            case=case,
            actor_type="client",
            actor_id=user.id,
            reason="Клиент выбрал отдельную консультацию",
        )
        return case

    return await ctx.case_service.create_case(
        client=user,
        route=RouteCode.M2,
        status=CaseStatus.M2_SLOT_PENDING,
        title="Юридическая консультация",
    )


async def _prepare_case_for_slot(ctx: BotContextService, case, user_id: int) -> None:
    status = _status(case)
    if status == CaseStatus.M2_CONSULTATION_ROUTE:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_DESCRIPTION_PENDING,
            actor_type="client",
            actor_id=user_id,
            comment="Клиент перешёл к выбору консультации",
        )
        status = CaseStatus.M2_DESCRIPTION_PENDING

    if status in {
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_DOCUMENTS_OPTIONAL,
        CaseStatus.M2_CONSULTATION_DONE,
    }:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_SLOT_PENDING,
            actor_type="client",
            actor_id=user_id,
            comment="Клиент перешёл к выбору даты и времени",
        )
        status = CaseStatus.M2_SLOT_PENDING

    if status not in {
        CaseStatus.M2_SLOT_PENDING,
        CaseStatus.M2_PAYMENT_PENDING,
        CaseStatus.M2_CONSULTATION_BOOKED,
    }:
        raise ValueError(
            "Текущий этап дела нельзя совместить с записью на консультацию. "
            "Создайте новое обращение через главное меню."
        )


async def _confirm_without_payment(
    *,
    db,
    ctx: BotContextService,
    user_id: int,
    case,
    consultation,
):
    if consultation.status == ConsultationStatus.BOOKED:
        if not consultation.slot_id:
            raise ValueError("У подтверждённой консультации отсутствует слот")
        slot = await SlotService(db).get_slot(consultation.slot_id)
        if not slot or slot.status != "booked":
            raise ValueError("Подтверждённый слот не найден")
        return consultation, slot

    await _prepare_case_for_slot(ctx, case, user_id)
    if not consultation.slot_id:
        raise SlotUnavailableError("Сначала выберите свободное время")

    slot = await SlotService(db).confirm_booking(
        consultation.slot_id,
        consultation.id,
    )
    consultation.status = ConsultationStatus.BOOKED
    consultation.lawyer_id = slot.lawyer_id
    consultation.scheduled_at = slot.starts_at

    if _status(case) != CaseStatus.M2_CONSULTATION_BOOKED:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_CONSULTATION_BOOKED,
            actor_type="system",
            actor_id=None,
            comment=(
                "Консультация подтверждена без онлайн-оплаты: "
                "PAYMENT_PROVIDER=disabled"
            ),
        )

    await add_case_history_event(
        db,
        actor_type="system",
        actor_id=None,
        case_id=case.id,
        action="CONSULTATION_BOOKED_WITHOUT_PAYMENT",
        new_value={
            "consultation_id": consultation.id,
            "slot_id": slot.id,
            "lawyer_id": slot.lawyer_id,
            "scheduled_at": slot.starts_at.isoformat(),
            "payment_provider": "disabled",
        },
        comment="Пилотный режим: платёжная ссылка не создавалась",
    )
    await NotificationEngine(db).emit(
        event_code="M2_CONSULTATION_BOOKED",
        case_id=case.id,
        user_id=user_id,
        payload={
            "case_number": case.case_number,
            "date": slot.starts_at.strftime("%d.%m.%Y %H:%M"),
        },
    )
    await db.flush()
    return consultation, slot


async def _show_confirmed_consultation(callback: CallbackQuery, slot) -> None:
    await callback.message.edit_text(
        "✅ Консультация подтверждена.\n\n"
        f"Дата и время: {_format_slot(slot)}\n"
        "Онлайн-оплата временно отключена, поэтому платёжная ссылка не требуется.\n\n"
        "Теперь укажите конкретный вопрос и при необходимости добавьте документы.",
        reply_markup=one(
            ("📝 Указать дело и вопрос", "consult_subject_start"),
            ("📄 Добавить документы", "documents_open"),
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "contact_lawyer"
)
async def contact_lawyer_without_payment(
    callback: CallbackQuery,
    db,
    state: FSMContext,
):
    await state.clear()
    _ctx, _user, case = await _active_case(callback, db)
    if case:
        text = (
            "💬 Связаться с юристом\n\n"
            "Можно написать по текущему делу, открыть переписку или выбрать "
            "время отдельной консультации. Онлайн-оплата временно не требуется."
        )
        buttons = (
            ("✉️ Написать по текущему делу", "message_create"),
            ("🗂 Открыть переписку", "message_history"),
            ("📅 Выбрать время консультации", "consult_booking_start"),
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        )
    else:
        text = (
            "💬 Юридическая консультация\n\n"
            "Опишите вопрос и выберите свободное время. "
            "В пилотном режиме онлайн-оплата временно не требуется."
        )
        buttons = (
            ("📝 Сначала описать вопрос", "consult_description_start"),
            ("📅 Сначала выбрать время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        )
    await callback.message.edit_text(text, reply_markup=one(*buttons))


@router.message(
    ConsultationDescriptionStates.waiting_description,
    lambda _message: payments_disabled(),
)
async def save_description_without_payment(
    message: Message,
    state: FSMContext,
    db,
):
    text = (message.text or "").strip()
    if len(text) < 20:
        await message.answer("Опишите вопрос подробнее — минимум 20 символов.")
        return
    if len(text) > 4000:
        await message.answer("Сократите описание до 4000 символов.")
        return

    data = await state.get_data()
    ctx = BotContextService(db)
    user = await ctx.get_user_from_message(message)
    case = await _ensure_consultation_case(ctx, user)
    consultation = await ConsultationService(db).get_or_create_for_case(case)
    await ConsultationService(db).save_description(
        consultation=consultation,
        case=case,
        client_id=user.id,
        description=text,
        subject_type=data.get("subject_type", "new_or_other"),
        related_case_id=data.get("related_case_id"),
    )

    if _status(case) == CaseStatus.M2_DESCRIPTION_PENDING:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_DOCUMENTS_OPTIONAL,
            actor_type="client",
            actor_id=user.id,
            comment="Клиент сохранил вопрос для консультации",
        )

    await db.commit()
    await state.clear()
    if consultation.status == ConsultationStatus.BOOKED:
        buttons = (
            ("📄 Добавить документы", "documents_open"),
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        )
        next_text = "Вопрос сохранён и будет передан юристу до встречи."
    else:
        buttons = (
            ("📅 Выбрать дату и время", "consult_booking_start"),
            ("📄 Добавить документы", "documents_open"),
            ("🏠 Главная", "nav_home"),
        )
        next_text = "Вопрос сохранён. Следующий шаг — выбрать дату и время."

    await message.answer(
        f"✅ {next_text}\n\nОнлайн-оплата временно не требуется.",
        reply_markup=one(*buttons),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data.startswith("consult_slot_select:")
)
async def choose_slot_without_payment(callback: CallbackQuery, db):
    slot_id = int(callback.data.split(":", 1)[1])
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await _ensure_consultation_case(ctx, user)
    service = ConsultationService(db)
    consultation = await service.get_or_create_for_case(case)

    if consultation.status == ConsultationStatus.BOOKED:
        await db.rollback()
        await callback.answer(
            "У вас уже есть подтверждённая консультация. Используйте перенос.",
            show_alert=True,
        )
        return

    try:
        consultation, _held_slot = await service.reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
        )
        consultation, slot = await _confirm_without_payment(
            db=db,
            ctx=ctx,
            user_id=user.id,
            case=case,
            consultation=consultation,
        )
        await db.commit()
    except (SlotUnavailableError, ValueError) as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return

    await _show_confirmed_consultation(callback, slot)


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_pay"
)
async def stale_consult_pay_without_payment(callback: CallbackQuery, db):
    ctx, user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Сначала выберите дату и время консультации.",
            reply_markup=one(
                ("📅 Выбрать дату и время", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Активная запись не найдена. Выберите свободное время.",
            reply_markup=one(
                ("📅 Выбрать дату и время", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    try:
        consultation, slot = await _confirm_without_payment(
            db=db,
            ctx=ctx,
            user_id=user.id,
            case=case,
            consultation=consultation,
        )
        await db.commit()
    except (SlotUnavailableError, ValueError) as error:
        await db.rollback()
        await callback.message.edit_text(
            f"Время больше недоступно: {error}",
            reply_markup=one(
                ("📅 Выбрать другое время", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    await _show_confirmed_consultation(callback, slot)


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_reschedule"
)
async def reschedule_without_payment(callback: CallbackQuery, db):
    _ctx, _user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Нет активной консультации для переноса.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation or consultation.status != ConsultationStatus.BOOKED:
        await callback.message.edit_text(
            "Перенести можно только подтверждённую консультацию.",
            reply_markup=one(("Назад", "consultation_booked_open")),
        )
        return
    slots = await SlotService(db).get_available_slots(limit=100)
    if not slots:
        await callback.message.edit_text(
            "Сейчас нет свободного времени. Текущая запись сохранена.",
            reply_markup=one(("Назад", "consultation_booked_open")),
        )
        return

    seen: set[str] = set()
    buttons = []
    for slot in slots:
        key = slot.starts_at.date().isoformat()
        if key not in seen:
            seen.add(key)
            buttons.append(
                (
                    f"📅 {slot.starts_at.strftime('%d.%m.%Y')}",
                    f"consult_reschedule_date:{key}",
                )
            )
    await callback.message.edit_text(
        "🔄 Выберите новую дату. Оплата не требуется.\n\n"
        "Текущий слот сохранится до успешного выбора нового времени.",
        reply_markup=one(
            *buttons,
            ("Назад", "consultation_booked_open"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_cancel"
)
async def cancel_prompt_without_payment(callback: CallbackQuery, db):
    _ctx, _user, case = await _active_case(callback, db)
    consultation = (
        await ConsultationService(db).get_current_for_case(case.id)
        if case
        else None
    )
    if not consultation:
        await callback.message.edit_text(
            "Активная консультация уже отсутствует.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    await callback.message.edit_text(
        "⚠️ Отменить консультацию?\n\n"
        "Слот будет освобождён. Возврат не требуется, поскольку платёж "
        "в пилотном режиме не создавался.",
        reply_markup=one(
            ("Да, отменить", "consult_cancel_confirm"),
            ("Нет, сохранить запись", "consultation_booked_open"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_cancel_confirm"
)
async def cancel_without_payment(callback: CallbackQuery, db):
    ctx, user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активная консультация уже отсутствует.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    current = await ConsultationService(db).get_current_for_case(case.id)
    if not current:
        await callback.message.edit_text(
            "Активная консультация уже отсутствует.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

    try:
        consultation = (
            await db.execute(
                select(Consultation)
                .where(Consultation.id == current.id)
                .with_for_update()
            )
        ).scalar_one()
        if consultation.slot_id:
            await SlotService(db).release_slot(
                consultation.slot_id,
                consultation.id,
            )
        old_status = consultation.status
        consultation.slot_id = None
        consultation.scheduled_at = None
        consultation.status = ConsultationStatus.CANCELLED
        await add_case_history_event(
            db,
            actor_type="client",
            actor_id=user.id,
            case_id=case.id,
            action="CONSULTATION_CANCELLED_WITHOUT_PAYMENT",
            old_value={"status": old_status},
            new_value={
                "status": consultation.status,
                "payment_provider": "disabled",
            },
            comment="Клиент отменил консультацию; возврат не требуется",
        )
        if _status(case) in {
            CaseStatus.M2_CONSULTATION_BOOKED,
            CaseStatus.M2_PAYMENT_PENDING,
        }:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент отменил консультацию и может выбрать новое время",
            )
        await db.commit()
    except (ValueError, LookupError) as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return

    await callback.message.edit_text(
        "Консультация отменена. Слот снова доступен, возврат не требуется.",
        reply_markup=one(
            ("📅 Выбрать другое время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "payments_open"
)
async def payments_disabled_screen(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 Онлайн-оплата временно отключена.\n\n"
        "Платёжные ссылки и тестовые платежи не создаются. "
        "Доступные пилотные сценарии продолжаются без оплаты.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _advance_initial_stage(ctx, case, user_id: int) -> None:
    status = _status(case)
    if status == CaseStatus.M1_CONTRACT_READY:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_WAITING_PAYMENT_30000,
            actor_type="client",
            actor_id=user_id,
            comment="Клиент подписал договор",
        )
        status = CaseStatus.M1_WAITING_PAYMENT_30000
    if status == CaseStatus.M1_WAITING_PAYMENT_30000:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_PAYMENT_30000_RECEIVED,
            actor_type="system",
            actor_id=None,
            comment="Первый платёж пропущен: онлайн-оплата отключена",
        )
        status = CaseStatus.M1_PAYMENT_30000_RECEIVED
    if status == CaseStatus.M1_PAYMENT_30000_RECEIVED:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M1_POWER_OF_ATTORNEY,
            actor_type="system",
            actor_id=None,
            comment="Открыт этап доверенности в пилотном режиме",
        )


@router.callback_query(
    lambda c: payments_disabled() and c.data in {"contract_sign", "pay_start_30000"}
)
async def initial_stage_without_payment(callback: CallbackQuery, db):
    ctx, user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text(
            "Активное дело не найдено.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return
    try:
        await _advance_initial_stage(ctx, case, user.id)
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "✅ Договор подписан. Онлайн-оплата временно не требуется.\n\n"
        "Следующий этап — оформление доверенности.",
        reply_markup=one(
            ("📑 Открыть инструкцию", "poa_instruction"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "court_status"
)
async def court_stage_without_payment(callback: CallbackQuery, db):
    ctx, user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text("Активное дело не найдено.")
        return
    try:
        if _status(case) == CaseStatus.M1_WAITING_30_DAYS:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_COURT_STAGE,
                actor_type="system",
                actor_id=None,
                comment="Открыт судебный этап",
            )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "🏛 Судебный этап\n\n"
        "Юрист сопровождает процесс. Онлайн-оплата временно не требуется. "
        "Продолжение этапа фиксируется отдельно, без создания платёжной ссылки.",
        reply_markup=one(
            ("Продолжить к исполнению", "pay_court_70000"),
            ("📁 Мое дело", "my_case_open"),
            ("💬 Задать вопрос", "message_create"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "pay_court_70000"
)
async def court_payment_stage_without_payment(callback: CallbackQuery, db):
    ctx, _user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text("Активное дело не найдено.")
        return
    try:
        status = _status(case)
        if status == CaseStatus.M1_COURT_STAGE:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_PAYMENT_70000,
                actor_type="system",
                actor_id=None,
                comment="Открыт второй договорный этап без онлайн-оплаты",
            )
            status = CaseStatus.M1_WAITING_PAYMENT_70000
        if status == CaseStatus.M1_WAITING_PAYMENT_70000:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_PAYMENT_70000_RECEIVED,
                actor_type="system",
                actor_id=None,
                comment="Второй платёж пропущен: онлайн-оплата отключена",
            )
            status = CaseStatus.M1_PAYMENT_70000_RECEIVED
        if status == CaseStatus.M1_PAYMENT_70000_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_ENFORCEMENT,
                actor_type="system",
                actor_id=None,
                comment="Открыт этап исполнения решения",
            )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "✅ Этап оплаты пропущен в пилотном режиме.\n\n"
        "Дело переведено на этап исполнения решения.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("💬 Задать вопрос", "message_create"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "pay_success_fee"
)
async def success_fee_stage_without_payment(callback: CallbackQuery, db):
    ctx, _user, case = await _active_case(callback, db)
    if not case:
        await callback.message.edit_text("Активное дело не найдено.")
        return
    status = _status(case)
    if status == CaseStatus.M1_ENFORCEMENT:
        await callback.message.edit_text(
            "Финальный этап пока недоступен: сначала нужно зафиксировать получение денег.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("💬 Задать вопрос", "message_create"),
            ),
        )
        return
    try:
        if status == CaseStatus.M1_MONEY_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_WAITING_SUCCESS_FEE,
                actor_type="system",
                actor_id=None,
                comment="Открыт финальный финансовый этап",
            )
            status = CaseStatus.M1_WAITING_SUCCESS_FEE
        if status == CaseStatus.M1_WAITING_SUCCESS_FEE:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
                actor_type="system",
                actor_id=None,
                comment="Финальный платёж пропущен: онлайн-оплата отключена",
            )
            status = CaseStatus.M1_SUCCESS_FEE_RECEIVED
        if status == CaseStatus.M1_SUCCESS_FEE_RECEIVED:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_CLOSED,
                actor_type="system",
                actor_id=None,
                comment="Финансовый этап завершён в пилотном режиме",
            )
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    await callback.message.edit_text(
        "✅ Финальный этап завершён без онлайн-оплаты. Дело закрыто.",
        reply_markup=one(("🏠 Главная", "nav_home")),
    )
