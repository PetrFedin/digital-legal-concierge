"""Retired compatibility module for the pre-versioned consent flow.

All consent callbacks are owned by ``consent_decision_guard`` and
``ConsentDecisionService``. Keeping a second set of handlers here would make
legal evidence depend on aiogram router order and could bypass exact text
version/hash recording. The module remains importable while the dispatcher and
historical tests are migrated away from the old name.
"""

from aiogram import Router

router = Router()

__all__ = ["router"]
