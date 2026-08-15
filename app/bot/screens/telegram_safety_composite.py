from __future__ import annotations

from aiogram import Router

from app.bot.screens.consultation_navigation_guard import router as consultation_navigation_router
from app.bot.screens.payment_stage_binding_guard import router as payment_stage_binding_router

router = Router()
router.include_router(consultation_navigation_router)
router.include_router(payment_stage_binding_router)

__all__ = ["router"]
