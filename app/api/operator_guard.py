"""Retired compatibility shim for historical operator guard imports.

Runtime ownership of staff/admin surfaces is explicit in ``app.main`` and the
corresponding product routers. This module intentionally owns no HTTP routes and
must not assemble other routers by import order.
"""

from fastapi import APIRouter

router = APIRouter(tags=["operator-guard-retired"])

__all__ = ["router"]
