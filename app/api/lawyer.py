"""Explicit facade for unique lawyer API paths.

The preserved implementation module still contains historical handlers and UI,
but runtime ownership of consultation completion/no-show and lawyer UI belongs
to ``app.api.lawyer_product``. This facade registers only the existing unique
M1 lawyer endpoints so public behavior no longer depends on include order.
"""

from fastapi import APIRouter

from app.api import lawyer_impl as _impl

router = APIRouter(prefix="/lawyer", tags=["lawyer"])

router.add_api_route("/cases", _impl.lawyer_cases, methods=["GET"], name="lawyer_cases")
router.add_api_route(
    "/consultations",
    _impl.lawyer_consultations,
    methods=["GET"],
    name="lawyer_consultations",
)
router.add_api_route(
    "/cases/{case_id}/accept",
    _impl.accept,
    methods=["POST"],
    name="lawyer_accept_case",
)
router.add_api_route(
    "/cases/{case_id}/request-documents",
    _impl.request_docs,
    methods=["POST"],
    name="lawyer_request_documents",
)
router.add_api_route(
    "/cases/{case_id}/transfer-to-m2",
    _impl.transfer_to_m2,
    methods=["POST"],
    name="lawyer_transfer_to_m2",
)

assigned_case = _impl.assigned_case
assert_case_snapshot = _impl.assert_case_snapshot
lawyer_cases = _impl.lawyer_cases
lawyer_consultations = _impl.lawyer_consultations
accept = _impl.accept
request_docs = _impl.request_docs
transfer_to_m2 = _impl.transfer_to_m2
complete_consultation = _impl.complete_consultation
client_no_show = _impl.client_no_show
lawyer_ui = _impl.lawyer_ui
LAWYER_HTML = _impl.LAWYER_HTML


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "assigned_case",
    "assert_case_snapshot",
    "lawyer_cases",
    "lawyer_consultations",
    "accept",
    "request_docs",
    "transfer_to_m2",
    "complete_consultation",
    "client_no_show",
    "lawyer_ui",
    "LAWYER_HTML",
    "router",
]
