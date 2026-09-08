"""Retired superadmin UI compatibility facade.

Access, audit, security, retention and recovery surfaces now authenticate at
their canonical runtime owners (with the global personal-session boundary where
needed). The historical helper implementation remains importable but publishes
no competing routes here.
"""

from fastapi import APIRouter

from app.api import superadmin_ui_guards_impl as _impl

router = APIRouter(tags=["superadmin-ui-retired"])


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = ["router"]
