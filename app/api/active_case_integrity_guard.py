"""Compatibility module for the retired client-wide active-case guard.

Multiple active Cases for one client are valid product data. The canonical
Workdesk integrity endpoint remains in ``workdesk_integrity_guard`` and checks
actual contradictions such as duplicate live consultations/payment attempts.
This module intentionally registers no shadow HTTP path; keeping the empty
router preserves imports while route consolidation removes precedence-based
behaviour.
"""

from fastapi import APIRouter

router = APIRouter(tags=["active-case-integrity-guard-retired"])

__all__ = ["router"]
