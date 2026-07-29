import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.bot.context import BotContextService
from app.bot.keyboards import one
from app.bot.screens.consultation_hold import router as hold_router
from app.bot.screens.consultation_payment import router as payment_router
from app.bot.screens.consultation_reservation import router as reservation_router
from app.bot.screens.consultation_selection import router as selection_router
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations.consultation_service import (
    ActiveConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


router = Router()
router.include_router(hold_router)
router.include_router(payment_router)
router.include_router(reservation_router)
router.include_router(selection_router)
logger = logging.getLogger(__name__)

ENTRY_CALLBACKS = {
    "consultation_start",
    "consult_description_start",
    "calc_to_m2",
}

DESCRIPTION_PROMPT = (
    "👨‍⚖️ Юридическая консультация · шаг 1 из 4\n\n"
    "Опишите ситуацию и сформулируйте главный вопрос одним сообщением. "
    "После этого можно будет приложить документы и выбрать свободное время.\n\n"
    "Не указывайте данные банковских карт, пароли, коды из SMS и другие "
    "секретные сведения."
)


def _resume_screen(status: ConsultationStatus):
    if status == ConsultationStatus.DOCUMENTS_OPTIONAL:
        return (
            "👨‍⚖️ Юридическая консультация · шаг 2 из 4\n\n"
            "Описание уже сохранено. Приложите документы, которые помогут "
            "юристу, или пропустите этот шаг.",
            one(
                ("📎 Приложить документы", "m2_documents_open"),
                ("⏭ Пропустить", "m2_documents_skip"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
    if status == ConsultationStatus.SLOT_PENDING:
        return (
            "👨‍⚖️ Юридическая консультация · шаг 3 из 4\n\n"
            "Описание и документы обработаны. Выберите удобный способ записи.",
            one(
                ("📅 Выбрать консультацию", "consult_slot_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
    if status == ConsultationStatus.SLOT_RESERVED:
        return (
            "👨‍⚖️ Юридическая консультация · шаг 4 из 4\n\n"
            "Время временно удерживается за вами. Проверьте резерв и завершите "
            "оплату до окончания срока удержания.",
            one(
                ("🕐 Проверить выбранное время", "consult_slot_reserved_open"),
                ("💳 Перейти к оплате", "consult_pay"),
                ("🔄 Выбрать другое время", "consult_reservation_change"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
    if status == ConsultationStatus.PAYMENT_PENDING:
        return (
            "💳 Консультация ожидает оплаты. Повторно создавать запись не нужно.",
            one(
                ("💳 Открыть оплату", "consult_pay"),
                ("🕐 Проверить резерв", "consult_slot_reserved_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
    if status == ConsultationStatus.PAID_PENDING_CONFIRMATION:
        return (
            "✅ Оплата получена. Юрист подтверждает консультацию. "
            "Новых действий от вас сейчас не требуется.",
            one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
    if status in {ConsultationStatus.CONFIRMED, ConsultationStatus.BOOKED}:
        return (
            "✅ Консультация назначена. Откройте карточку, чтобы проверить "
            "дату, время и формат.",
            one(
                ("📅 Открыть консультацию", "consultation_booked_open"),
                ("📁 Моё дело", "my_case_open"),
            ),
        )
    return (
        "Текущий этап консультации доступен в разделе «Моё дело».",
        one(
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главная", "nav_home"),
        ),
    )


@router.callback_query(lambda c: c.data in ENTRY_CALLBACKS)
async def start_or_resume_consultation(
    callback: CallbackQuery,
    state: FSMContext,
    db,
):
    await state.clear()
    try:
        ctx = BotContextService(db)
        user = await ctx.get_user_from_callback(callback)
        if user.is_blocked:
            raise ConsultationNotFoundError("Доступ клиента ограничен.")
        case = await ctx.case_service.get_or_create_m2_case_for_user(user)
        consultation = await ConsultationService(db).create_or_get_m2_consultation(
            case=case,
            actor_type="client",
            actor_id=user.id,
            source="telegram",
        )
        status = ConsultationStatus(consultation.status)
        await db.commit()
    except (
        ConsultationNotFoundError,
        ActiveConsultationConflictError,
        ValueError,
    ):
        await db.rollback()
        await callback.message.edit_text(
            "Не удалось безопасно открыть консультацию. "
            "Откройте «Моё дело» или обратитесь к менеджеру.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("💬 Связаться с менеджером", "contact_lawyer"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return
    except Exception:
        await db.rollback()
        logger.exception("Unexpected error while starting Telegram consultation")
        await callback.message.edit_text(
            "Сервис консультаций временно недоступен. "
            "Ваши ранее сохранённые данные не потеряны.",
            reply_markup=one(
                ("📁 Моё дело", "my_case_open"),
                ("🏠 Главная", "nav_home"),
            ),
        )
        return

    if status == ConsultationStatus.DESCRIPTION_PENDING:
        await state.update_data(
            case_id=case.id,
            consultation_id=consultation.id,
        )
        await state.set_state(ConsultationDescriptionStates.waiting_description)
        await callback.message.edit_text(
            DESCRIPTION_PROMPT,
            reply_markup=one(("Отменить", "nav_cancel")),
        )
        return

    text, keyboard = _resume_screen(status)
    await callback.message.edit_text(text, reply_markup=keyboard)
