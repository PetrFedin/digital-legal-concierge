from __future__ import annotations

from aiogram import Router

from app.bot.screens.client_message_recovery import router as client_message_recovery_router
from app.bot.screens.consultation_mutation_entry_guard import (
    router as consultation_mutation_entry_router,
)
from app.bot.screens.consultation_navigation_guard import router as consultation_navigation_router
from app.bot.screens.message_history_guard import router as message_history_guard_router
from app.bot.screens.payment_stage_binding_guard import router as payment_stage_binding_router

router = Router()
router.include_router(consultation_navigation_router)
router.include_router(consultation_mutation_entry_router)
router.include_router(client_message_recovery_router)
# Own message-history navigation before messages.router. The legacy handler is
# left only as backward-compatible implementation detail until the Telegram
# routers are physically consolidated; it must never receive runtime history
# callbacks because it is not Case-bound and reads Case ORM after rollback.
router.include_router(message_history_guard_router)
router.include_router(payment_stage_binding_router)

__all__ = ["router"]
