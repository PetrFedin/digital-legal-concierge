"""Explicit runtime owner for initial setup/readiness endpoints.

The historical implementation is preserved verbatim in
``initial_setup_wizard_impl``. This facade registers only setup-owned paths and
contains no compatibility router assembly.
"""

from fastapi import APIRouter

from app.api import initial_setup_wizard_impl as _impl

router = APIRouter(tags=["initial-setup"])

router.add_api_route(
    "/launch-check",
    _impl.launch_check,
    methods=["GET"],
    name="launch_check",
)
router.add_api_route(
    "/initial-setup-wizard/status",
    _impl.initial_setup_status,
    methods=["GET"],
    name="initial_setup_status",
)
router.add_api_route(
    "/initial-setup-wizard/ui",
    _impl.initial_setup_ui,
    methods=["GET"],
    name="initial_setup_ui",
)

VERSION = _impl.VERSION


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = ["VERSION", "router"]
