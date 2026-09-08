"""Explicit runtime facade for unique administrative API paths.

The historical implementation is preserved verbatim in ``admin_impl``. The
three compatibility paths that require stricter production behavior
(`/queue`, `/lawyers`, `/scheduler/run-once`) are owned by
``admin_queue_guard`` and are intentionally not registered here.
"""

from fastapi import APIRouter

from app.api import admin_impl as _impl

router = APIRouter(prefix="/admin", tags=["admin"])

router.add_api_route("/dashboard", _impl.dashboard, methods=["GET"], name="admin_dashboard")
router.add_api_route("/settings", _impl.settings_list, methods=["GET"], name="admin_settings")
router.add_api_route(
    "/settings/{key:path}",
    _impl.set_setting,
    methods=["POST"],
    name="admin_set_setting",
)
router.add_api_route("/cases", _impl.cases, methods=["GET"], name="admin_cases")
router.add_api_route(
    "/cases/{case_id}/assign/{lawyer_id}",
    _impl.assign,
    methods=["POST"],
    name="admin_assign_case",
)
router.add_api_route(
    "/cases/{case_id}/auto-assign",
    _impl.auto_assign,
    methods=["POST"],
    name="admin_auto_assign_case",
)
router.add_api_route(
    "/notifications",
    _impl.notifications,
    methods=["GET"],
    name="admin_notifications",
)
router.add_api_route(
    "/cases/{case_id}",
    _impl.case_detail,
    methods=["GET"],
    name="admin_case_detail",
)
router.add_api_route(
    "/cases/{case_id}/status",
    _impl.manual_status,
    methods=["POST"],
    name="admin_manual_status",
)
router.add_api_route(
    "/payments/{payment_id}/confirm",
    _impl.manual_confirm_payment,
    methods=["POST"],
    name="admin_manual_confirm_payment",
)
router.add_api_route(
    "/payments/{payment_id}/confirm-offline",
    _impl.confirm_offline_payment,
    methods=["POST"],
    name="admin_confirm_offline_payment",
)
router.add_api_route("/payments", _impl.all_payments, methods=["GET"], name="admin_payments")
router.add_api_route("/documents", _impl.all_documents, methods=["GET"], name="admin_documents")
router.add_api_route("/statuses", _impl.statuses, methods=["GET"], name="admin_statuses")

# Preserve public Python imports while preventing the historical router from
# entering the application assembly.
require_admin = _impl.require_admin
actor_id_from_token = _impl.actor_id_from_token
manual_payment_confirmation_enabled = _impl.manual_payment_confirmation_enabled
payment_can_be_manually_confirmed = _impl.payment_can_be_manually_confirmed
offline_payment_confirmation_enabled = _impl.offline_payment_confirmation_enabled
payment_can_be_confirmed_offline = _impl.payment_can_be_confirmed_offline
M1_OFFLINE_CONFIRMABLE_CODES = _impl.M1_OFFLINE_CONFIRMABLE_CODES


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "M1_OFFLINE_CONFIRMABLE_CODES",
    "actor_id_from_token",
    "manual_payment_confirmation_enabled",
    "offline_payment_confirmation_enabled",
    "payment_can_be_confirmed_offline",
    "payment_can_be_manually_confirmed",
    "require_admin",
    "router",
]
