from __future__ import annotations

from fastapi import APIRouter

# Retired compatibility shim.
#
# Runtime ownership of every /admin/consultation-outcomes path now belongs to
# app.api.consultation_outcomes_product.router. This module intentionally owns
# no routes and performs no import-time mutation of other routers. It remains
# temporarily importable so historical tests/bookmarks/modules fail cleanly
# while the repository is consolidated.
router = APIRouter(tags=["consultation-outcomes-ui-retired"])

__all__ = ["router"]
