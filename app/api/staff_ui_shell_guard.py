"""Retired staff UI shell compatibility facade.

Canonical staff/product routers perform their own server-side authorization.
The historical implementation remains importable from ``staff_ui_shell_guard_impl``
for diagnostics while this module intentionally owns no HTTP routes.
"""

from fastapi import APIRouter

from app.api import staff_ui_shell_guard_impl as _impl

router = APIRouter(tags=["staff-ui-shell-retired"])


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = ["router"]
