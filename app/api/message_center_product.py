"""Single runtime owner for the existing staff Message Center surface.

Business handlers stay in the preserved implementation module. This facade
registers every shipped Message Center path exactly once and uses the role-safe
UI renderer for both admin and lawyer workspaces.
"""

from fastapi import APIRouter

from app.api import message_center_product_impl as _impl
from app.api.message_center_role_ui import role_safe_message_center_ui

router = APIRouter(tags=["message-center-product"])

router.add_api_route(
    "/message-center/status",
    _impl.message_center_status,
    methods=["GET"],
    name="message_center_status",
)
router.add_api_route(
    "/message-center/cases/{case_id}/messages",
    _impl.case_messages,
    methods=["GET"],
    name="case_messages",
)
router.add_api_route(
    "/message-center/cases/{case_id}/reply",
    _impl.reply_to_client,
    methods=["POST"],
    name="reply_to_client",
)
router.add_api_route(
    "/message-center/{message_id}/read",
    _impl.mark_message_read,
    methods=["POST"],
    name="mark_message_read",
)
router.add_api_route(
    "/message-center/ui",
    role_safe_message_center_ui,
    methods=["GET"],
    name="message_center_ui",
)


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = ["router"]
