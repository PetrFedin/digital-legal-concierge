from __future__ import annotations

import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.states import ConsultationDescriptionStates
from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotService, SlotUnavailableError
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation

router = Router()
logger = logging.getLogger(__name__)

_M2_REUSABLE = {
    CaseStatus.M2_CONSULTATION_ROUTE,
    CaseStatus.M2_DESCRIPTION_PENDING,
    CaseStatus.M2_DOCUMENTS_OPTIONAL,
    CaseStatus.M2_SLOT_PENDING,
    CaseStatus.M2_PAYMENT_PENDING,
    CaseStatus.M2_CONSULTATION_BOOKED,
    CaseStatus.M2_CONSULTATION_DONE,
}


def _case_status(case) -> CaseStatus:
    if isinstance(case.status, CaseStatus):
        return case.status
    return CaseStatus(str(case.status))


def _slot_text(slot) -> str:
    return (
        f"{slot.starts_at.strftime('%d.%m.%Y')} · "
        f"{slot.starts_at.strftime('%H:%M')}–{slot.ends_at.strftime('%H:%M')}"
    )


async def _context(callback: CallbackQuery, db):
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    case = await ctx.case_service.get_active_case_for_user(user.id)
    return ctx, user, case


async def _consultation_case(ctx: BotContextService, user):
    case = await ctx.case_service.get_active_case_for_user(user.id)
    if case is None:
        return await ctx.case_service.create_case(
            client=user,
            route=RouteCode.M2,
            status=CaseStatus.M2_SLOT_PENDING,
            title="Юридическая консультация",
        )
    if _case_status(case) in _M2_REUSABLE:
        return case
    raise ValueError(
        "У вас уже есть активное дело другого маршрута. "
        "Напишите юристу по этому делу; отдельная запись не будет скрывать текущее дело."
    )


async def _prepare_case_for_booking(ctx, case, user_id: int) -> None:
    status = _case_status(case)
    if status == CaseStatus.M2_CONSULTATION_ROUTE:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_DESCRIPTION_PENDING,
            actor_type="client",
            actor_id=user_id,
            comment="Клиент перешёл к записи на консультацию",
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
        raise ValueError("Для текущего этапа выбор времени недоступен")


async def _book_without_payment(*, db, ctx, user_id: int, case, consultation):
    if consultation.status == ConsultationStatus.BOOKED:
        if not consultation.slot_id:
            raise ValueError("У подтверждённой консультации отсутствует слот")
        slot = await SlotService(db).get_slot(consultation.slot_id)
        if (
            not slot
            or slot.status != "booked"
            or slot.consultation_id != consultation.id
        ):
            raise ValueError("Подтверждённый слот не найден")
        return consultation, slot

    await _prepare_case_for_booking(ctx, case, user_id)
    if not consultation.slot_id:
        raise SlotUnavailableError("Сначала выберите свободное время")

    if _case_status(case) == CaseStatus.M2_SLOT_PENDING:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_PAYMENT_PENDING,
            actor_type="system",
            actor_id=None,
            comment=(
                "Слот зарезервирован; в пилотном режиме онлайн-оплата отключена"
            ),
        )

    slot = await SlotService(db).confirm_booking(
        consultation.slot_id,
        consultation.id,
    )
    consultation.status = ConsultationStatus.BOOKED
    consultation.lawyer_id = slot.lawyer_id
    consultation.scheduled_at = slot.starts_at

    if _case_status(case) != CaseStatus.M2_CONSULTATION_BOOKED:
        await ctx.case_service.change_status(
            case=case,
            next_status=CaseStatus.M2_CONSULTATION_BOOKED,
            actor_type="system",
            actor_id=None,
            comment="Консультация подтверждена без онлайн-оплаты",
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
        dedupe_key=f"consultation-booked-no-payment:{consultation.id}:{slot.id}",
    )
    await db.flush()
    return consultation, slot


async def _show_booked(callback: CallbackQuery, slot) -> None:
    await callback.message.edit_text(
        "✅ Консультация подтверждена.\n\n"
        f"Дата и время: {_slot_text(slot)}\n"
        "Онлайн-оплата временно не требуется.\n\n"
        "Теперь укажите конкретный вопрос и при необходимости добавьте документы.",
        reply_markup=one(
            ("📝 Указать дело и вопрос", "consult_subject_start"),
            ("📄 Добавить документы", "documents_open"),
            ("👨‍⚖ Открыть запись", "consultation_booked_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _show_error(callback: CallbackQuery, error: Exception) -> None:
    await callback.message.edit_text(
        f"Действие не выполнено: {error}",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: payments_disabled() and c.data == "contact_lawyer")
async def contact_lawyer(callback: CallbackQuery, db, state: FSMContext):
    await state.clear()
    _ctx, _user, case = await _context(callback, db)
    if case:
        buttons = [
            ("✉️ Написать по текущему делу", "message_create"),
            ("🗂 Открыть переписку", "message_history"),
        ]
        if _case_status(case) in _M2_REUSABLE:
            buttons.append(("📅 Выбрать время консультации", "consult_booking_start"))
        buttons.extend(
            [
                ("📁 Мое дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ]
        )
        await callback.message.edit_text(
            "💬 Связаться с юристом\n\n"
            "Напишите по текущему делу или откройте переписку. "
            "Для консультационного маршрута можно также выбрать время без оплаты.",
            reply_markup=one(*buttons),
        )
        return

    await callback.message.edit_text(
        "💬 Юридическая консультация\n\n"
        "Опишите вопрос и выберите свободное время. "
        "В пилотном режиме онлайн-оплата не требуется.",
        reply_markup=one(
            ("📝 Сначала описать вопрос", "consult_description_start"),
            ("📅 Сначала выбрать время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.message(
    ConsultationDescriptionStates.waiting_description,
    lambda _message: payments_disabled(),
)
async def save_description(message: Message, state: FSMContext, db):
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
    try:
        case = await _consultation_case(ctx, user)
        service = ConsultationService(db)
        consultation = await service.get_or_create_for_case(case)
        await service.save_description(
            consultation=consultation,
            case=case,
            client_id=user.id,
            description=text,
            subject_type=data.get("subject_type", "new_or_other"),
            related_case_id=data.get("related_case_id"),
        )
        if _case_status(case) == CaseStatus.M2_DESCRIPTION_PENDING:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M2_DOCUMENTS_OPTIONAL,
                actor_type="client",
                actor_id=user.id,
                comment="Клиент сохранил вопрос для консультации",
            )
        await db.commit()
    except (ValueError, SlotUnavailableError) as error:
        await db.rollback()
        await state.clear()
        await message.answer(
            f"Вопрос не сохранён: {error}",
            reply_markup=one(
                ("💬 Написать по текущему делу", "message_create"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось сохранить вопрос консультации без оплаты")
        await message.answer(
            "Вопрос временно не сохранён. Повторите позже или напишите по делу.",
            reply_markup=one(("🏠 Главная", "nav_home")),
        )
        return

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
        f"✅ {next_text}\n\nОнлайн-оплата не требуется.",
        reply_markup=one(*buttons),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data.startswith("consult_slot_select:")
)
async def choose_slot(callback: CallbackQuery, db):
    slot_id = int(callback.data.split(":", 1)[1])
    ctx = BotContextService(db)
    user = await ctx.get_user_from_callback(callback)
    try:
        case = await _consultation_case(ctx, user)
        service = ConsultationService(db)
        consultation = await service.get_or_create_for_case(case)
        if consultation.status == ConsultationStatus.BOOKED:
            raise ValueError(
                "У вас уже есть подтверждённая консультация. Используйте перенос."
            )
        consultation, _held_slot = await service.reserve_slot(
            consultation=consultation,
            case=case,
            client_id=user.id,
            slot_id=slot_id,
        )
        consultation, slot = await _book_without_payment(
            db=db,
            ctx=ctx,
            user_id=user.id,
            case=case,
            consultation=consultation,
        )
        await db.commit()
    except (ValueError, SlotUnavailableError) as error:
        await db.rollback()
        await callback.answer(str(error), show_alert=True)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось подтвердить консультацию без оплаты")
        await callback.answer("Запись временно недоступна.", show_alert=True)
        return
    await _show_booked(callback, slot)


@router.callback_query(lambda c: payments_disabled() and c.data == "consult_pay")
async def stale_consult_pay(callback: CallbackQuery, db):
    ctx, user, case = await _context(callback, db)
    if not case:
        await callback.message.edit_text(
            "Сначала выберите дату и время консультации.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    consultation = await ConsultationService(db).get_current_for_case(case.id)
    if not consultation:
        await callback.message.edit_text(
            "Активная запись не найдена. Выберите свободное время.",
            reply_markup=one(
                ("📅 Выбрать время", "consult_booking_start"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    try:
        consultation, slot = await _book_without_payment(
            db=db,
            ctx=ctx,
            user_id=user.id,
            case=case,
            consultation=consultation,
        )
        await db.commit()
    except (ValueError, SlotUnavailableError) as error:
        await db.rollback()
        await _show_error(callback, error)
        return
    except Exception:
        await db.rollback()
        logger.exception("Не удалось завершить устаревший платёжный callback")
        await callback.answer("Запись временно недоступна.", show_alert=True)
        return
    await _show_booked(callback, slot)


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_reschedule"
)
async def reschedule(callback: CallbackQuery, db):
    _ctx, _user, case = await _context(callback, db)
    consultation = (
        await ConsultationService(db).get_current_for_case(case.id)
        if case
        else None
    )
    if not consultation or consultation.status != ConsultationStatus.BOOKED:
        await callback.message.edit_text(
            "Перенести можно только подтверждённую консультацию.",
            reply_markup=one(("📁 Мое дело", "my_case_open")),
        )
        return
    slots = await SlotService(db).get_available_slots(limit=100)
    if not slots:
        await callback.message.edit_text(
            "Свободного времени пока нет. Текущая запись сохранена.",
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
        reply_markup=one(*buttons, ("Назад", "consultation_booked_open")),
    )


@router.callback_query(lambda c: payments_disabled() and c.data == "consult_cancel")
async def cancel_prompt(callback: CallbackQuery, db):
    _ctx, _user, case = await _context(callback, db)
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
        "Слот будет освобождён. Возврат не требуется, поскольку платёж не создавался.",
        reply_markup=one(
            ("Да, отменить", "consult_cancel_confirm"),
            ("Нет, сохранить запись", "consultation_booked_open"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "consult_cancel_confirm"
)
async def cancel_confirm(callback: CallbackQuery, db):
    ctx, user, case = await _context(callback, db)
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
        if _case_status(case) in {
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
    except Exception:
        await db.rollback()
        logger.exception("Не удалось отменить консультацию без оплаты")
        await callback.answer("Отмена временно недоступна.", show_alert=True)
        return
    await callback.message.edit_text(
        "Консультация отменена. Слот снова доступен, возврат не требуется.",
        reply_markup=one(
            ("📅 Выбрать другое время", "consult_booking_start"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: payments_disabled() and c.data == "payments_open")
async def payments_screen(callback: CallbackQuery):
    await callback.message.edit_text(
        "💳 Онлайн-оплата временно отключена.\n\n"
        "Платёжные ссылки и тестовые платежи не создаются. "
        "Доступные пилотные сценарии продолжаются без оплаты.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


async def _advance_path(ctx, case, path: tuple[CaseStatus, ...], comments: dict) -> str:
    current = _case_status(case)
    if current == path[-1]:
        return "already"
    if current not in path:
        return "invalid"
    start = path.index(current)
    for target in path[start + 1 :]:
        await ctx.case_service.change_status(
            case=case,
            next_status=target,
            actor_type="system",
            actor_id=None,
            comment=comments[target],
        )
    return "advanced"


_INITIAL_PATH = (
    CaseStatus.M1_CONTRACT_READY,
    CaseStatus.M1_WAITING_PAYMENT_30000,
    CaseStatus.M1_PAYMENT_30000_RECEIVED,
    CaseStatus.M1_POWER_OF_ATTORNEY,
)
_COURT_PATH = (
    CaseStatus.M1_COURT_STAGE,
    CaseStatus.M1_WAITING_PAYMENT_70000,
    CaseStatus.M1_PAYMENT_70000_RECEIVED,
    CaseStatus.M1_ENFORCEMENT,
)
_SUCCESS_PATH = (
    CaseStatus.M1_MONEY_RECEIVED,
    CaseStatus.M1_WAITING_SUCCESS_FEE,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED,
    CaseStatus.M1_CLOSED,
)


@router.callback_query(
    lambda c: payments_disabled()
    and c.data in {"contract_sign", "pay_start_30000"}
)
async def initial_stage(callback: CallbackQuery, db):
    ctx, _user, case = await _context(callback, db)
    if not case:
        await _show_error(callback, ValueError("Активное дело не найдено"))
        return
    comments = {
        CaseStatus.M1_WAITING_PAYMENT_30000: "Клиент подтвердил подписание договора",
        CaseStatus.M1_PAYMENT_30000_RECEIVED: (
            "Первый платёж пропущен: онлайн-оплата отключена"
        ),
        CaseStatus.M1_POWER_OF_ATTORNEY: "Открыт этап доверенности",
    }
    try:
        result = await _advance_path(ctx, case, _INITIAL_PATH, comments)
        if result == "invalid":
            raise ValueError("Кнопка не соответствует текущему этапу дела")
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _show_error(callback, error)
        return
    await callback.message.edit_text(
        "✅ Договор подтверждён. Онлайн-оплата не требуется.\n\n"
        "Следующий этап — оформление доверенности.",
        reply_markup=one(
            ("📑 Открыть инструкцию", "poa_instruction"),
            ("📁 Мое дело", "my_case_open"),
        ),
    )


@router.callback_query(lambda c: payments_disabled() and c.data == "court_status")
async def court_status(callback: CallbackQuery, db):
    ctx, _user, case = await _context(callback, db)
    if not case:
        await _show_error(callback, ValueError("Активное дело не найдено"))
        return
    try:
        status = _case_status(case)
        if status == CaseStatus.M1_WAITING_30_DAYS:
            await ctx.case_service.change_status(
                case=case,
                next_status=CaseStatus.M1_COURT_STAGE,
                actor_type="system",
                actor_id=None,
                comment="Открыт судебный этап",
            )
        elif status not in _COURT_PATH:
            raise ValueError("Судебный этап для текущего статуса недоступен")
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _show_error(callback, error)
        return
    await callback.message.edit_text(
        "🏛 Судебный этап\n\n"
        "Юрист сопровождает процесс. Онлайн-оплата не требуется. "
        "Переход к исполнению фиксируется отдельным действием.",
        reply_markup=one(
            ("Продолжить к исполнению", "pay_court_70000"),
            ("📁 Мое дело", "my_case_open"),
            ("💬 Задать вопрос", "message_create"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "pay_court_70000"
)
async def court_stage(callback: CallbackQuery, db):
    ctx, _user, case = await _context(callback, db)
    if not case:
        await _show_error(callback, ValueError("Активное дело не найдено"))
        return
    comments = {
        CaseStatus.M1_WAITING_PAYMENT_70000: "Открыт второй договорный этап",
        CaseStatus.M1_PAYMENT_70000_RECEIVED: (
            "Второй платёж пропущен: онлайн-оплата отключена"
        ),
        CaseStatus.M1_ENFORCEMENT: "Открыт этап исполнения решения",
    }
    try:
        result = await _advance_path(ctx, case, _COURT_PATH, comments)
        if result == "invalid":
            raise ValueError("Кнопка не соответствует текущему этапу дела")
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _show_error(callback, error)
        return
    await callback.message.edit_text(
        "✅ Дело переведено на этап исполнения решения. "
        "Онлайн-оплата не создавалась.",
        reply_markup=one(
            ("📁 Мое дело", "my_case_open"),
            ("💬 Задать вопрос", "message_create"),
        ),
    )


@router.callback_query(
    lambda c: payments_disabled() and c.data == "pay_success_fee"
)
async def success_stage(callback: CallbackQuery, db):
    ctx, _user, case = await _context(callback, db)
    if not case:
        await _show_error(callback, ValueError("Активное дело не найдено"))
        return
    if _case_status(case) == CaseStatus.M1_ENFORCEMENT:
        await callback.message.edit_text(
            "Финальный этап пока недоступен: сначала администратор должен "
            "зафиксировать получение денег.",
            reply_markup=one(
                ("📁 Мое дело", "my_case_open"),
                ("💬 Задать вопрос", "message_create"),
            ),
        )
        return
    comments = {
        CaseStatus.M1_WAITING_SUCCESS_FEE: "Открыт финальный финансовый этап",
        CaseStatus.M1_SUCCESS_FEE_RECEIVED: (
            "Финальный платёж пропущен: онлайн-оплата отключена"
        ),
        CaseStatus.M1_CLOSED: "Финансовый этап завершён в пилотном режиме",
    }
    try:
        result = await _advance_path(ctx, case, _SUCCESS_PATH, comments)
        if result == "invalid":
            raise ValueError("Кнопка не соответствует текущему этапу дела")
        await db.commit()
    except ValueError as error:
        await db.rollback()
        await _show_error(callback, error)
        return
    await callback.message.edit_text(
        "✅ Финальный этап завершён без онлайн-оплаты. Дело закрыто.",
        reply_markup=one(("🏠 Главная", "nav_home")),
    )
