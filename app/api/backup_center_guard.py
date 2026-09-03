"""Retired backup-center guard compatibility facade.

``backup_manager`` is the single runtime owner for the existing backup-center
status/UI/verification paths. The historical guard implementation remains
importable for diagnostics but this module intentionally owns no HTTP routes.
"""

from fastapi import APIRouter

from app.api import backup_center_guard_impl as _impl

router = APIRouter(tags=["backup-center-guard-retired"])


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = ["router"]
