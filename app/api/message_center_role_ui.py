"""Route-free facade for Message Center presentation helpers.

Runtime ownership of ``GET /message-center/ui`` belongs to
``app.api.message_center_product``. The role-safe HTML renderer remains reusable
without publishing a competing FastAPI route.
"""

from fastapi import APIRouter

from app.api import message_center_role_ui_impl as _impl

role_safe_message_center_html = _impl.role_safe_message_center_html
role_safe_message_center_ui = _impl.role_safe_message_center_ui

router = APIRouter(tags=["message-center-role-ui-retired"])


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "role_safe_message_center_html",
    "role_safe_message_center_ui",
    "router",
]
