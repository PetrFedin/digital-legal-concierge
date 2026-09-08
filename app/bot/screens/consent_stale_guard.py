"""Retired compatibility module for the old consent stale-button guard.

``consent_decision_guard`` now owns both current and historical consent buttons,
including v2/unbound recovery. It resolves the exact legal-text contract before
any mutation, so router precedence is no longer part of the consent safety
model.
"""

from aiogram import Router

router = Router()

__all__ = ["router"]
