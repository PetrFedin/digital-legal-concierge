"""Route-focused facade for technical Case recovery.

Technical recovery keeps its existing API/mutation endpoints. Historical
Workdesk and technical-card HTML routes are not registered here: the canonical
Workdesk owns ``/admin/workdesk/ui`` and ``technical_cases_compat`` owns the
legacy ``/admin/technical-cases/ui`` redirect.
"""

from fastapi import APIRouter

from app.api import technical_case_recovery_impl as _impl

router = APIRouter(tags=["technical-case-recovery"])

router.add_api_route(
    "/admin/technical-cases",
    _impl.technical_cases,
    methods=["GET"],
    name="technical_cases",
)
router.add_api_route(
    "/admin/technical-cases/{case_id}/context",
    _impl.technical_case_context,
    methods=["GET"],
    name="technical_case_context",
)
router.add_api_route(
    "/admin/technical-cases/{case_id}/recover",
    _impl.recover_technical_case,
    methods=["POST"],
    name="recover_technical_case",
)

technical_cases = _impl.technical_cases
technical_case_context = _impl.technical_case_context
recover_technical_case = _impl.recover_technical_case
_error_rows = _impl._error_rows


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "technical_cases",
    "technical_case_context",
    "recover_technical_case",
    "router",
]
