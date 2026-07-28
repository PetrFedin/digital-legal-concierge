from __future__ import annotations

from aiogram import Router
from aiogram.types import CallbackQuery

from app.bot.keyboards import one


router = Router()


LEGACY_BOOKING_PREFIXES = (
    "consult_slot_select:",
)
LEGACY_RESCHEDULE_PREFIXES = (
    "consult_reschedule_date:",
    "consult_reschedule_slot:",
)


@router.callback_query(
    lambda callback: callback.data.startswith(LEGACY_BOOKING_PREFIXES)
)
async def reject_legacy_slot_reference(callback: CallbackQuery):
    await callback.message.edit_text(
        "Эта кнопка выбора времени устарела. "
        "Откройте актуальное расписание: свободные варианты будут повторно "
        "проверены сервером.",
        reply_markup=one(
            ("📅 Открыть расписание", "consult_slot_open"),
            ("📁 Моё дело", "my_case_open"),
            ("🏠 Главное меню", "nav_home"),
        ),
    )


@router.callback_query(
    lambda callback: callback.data.startswith(LEGACY_RESCHEDULE_PREFIXES)
)
async def reject_legacy_reschedule_reference(callback: CallbackQuery):
    await callback.message.edit_text(
        "Эта кнопка переноса устарела. Текущая запись не изменена.",
        reply_markup=one(
            ("🔄 Выбрать новое время", "consult_reschedule"),
            ("📋 Текущая запись", "consultation_booked_open"),
            ("📁 Моё дело", "my_case_open"),
        ),
    )
