"""Retired Workdesk compatibility shim.

Runtime ownership moved to ``workdesk_product``. Browser rendering is composed
structurally by ``workdesk_runtime_ui`` and server-side projections live in
``workdesk_projections``. This module intentionally publishes no routes and no
HTML mutation helpers.
"""

from fastapi import APIRouter

router = APIRouter(tags=["workdesk-ui-guard-retired"])

__all__ = ["router"]
