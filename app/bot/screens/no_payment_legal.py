from aiogram import Router
from aiogram.types import CallbackQuery

from app.domain.payments.mode import payments_disabled

router = Router()


@router.callback_query(lambda c: payments_disabled() and c.data == "court_status")
async def court_status_without_side_effects(callback: CallbackQuery, db):
    # Use the same read-only legal milestones in every payment mode.
    from app.bot.screens import m1_stages

    await m1_stages.court_status(callback, db)
