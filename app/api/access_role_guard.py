"""Retired compatibility module for access-management shadow routes.

Role compatibility validation, superadmin/MFA authorization and historical
role-conflict warnings now live in the canonical ``app.api.access_management``
router. Registering the same /access paths twice would make authorization depend
on FastAPI route order, so this module intentionally exposes no endpoints.
"""

from fastapi import APIRouter

router = APIRouter(tags=["access-role-guard-retired"])

__all__ = ["router"]
