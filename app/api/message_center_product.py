"""Single runtime owner for the existing staff Message Center surface.

The mature responsibility-aware implementation historically shadowed the base
Message Center by being mounted first. Staff/client correspondence is part of
the legal Case record, so public behavior must not depend on FastAPI include
order. This router owns the already-shipped endpoints while reusing the current
implementations; no new messaging route or business capability is introduced.
"""

from fastapi import APIRouter

from app.api.guided_message_center import (
    guided_case_messages,
    guided_message_center_status,
    guided_message_center_ui,
    guided_reply_to_client,
)
from app.api.message_center import mark_message_read

router = APIRouter(tags=["message-center-product"])

router.add_api_route(
    "/message-center/status",
    guided_message_center_status,
    methods=["GET"],
    name="message_center_status",
)
router.add_api_route(
    "/message-center/cases/{case_id}/messages",
    guided_case_messages,
    methods=["GET"],
    name="case_messages",
)
router.add_api_route(
    "/message-center/cases/{case_id}/reply",
    guided_reply_to_client,
    methods=["POST"],
    name="reply_to_client",
)
router.add_api_route(
    "/message-center/{message_id}/read",
    mark_message_read,
    methods=["POST"],
    name="mark_message_read",
)
router.add_api_route(
    "/message-center/ui",
    guided_message_center_ui,
    methods=["GET"],
    name="message_center_ui",
)

__all__ = ["router"]
