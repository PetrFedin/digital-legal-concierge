"""Compatibility mount for staff guards while route consolidation continues.

The canonical authenticated ``GET /operator`` now lives in ``app.api.operator``.
This module intentionally does not register another /operator endpoint. The
remaining nested guard routers are temporary compatibility mounts and are being
migrated one public path at a time into their canonical/product modules.
"""

from fastapi import APIRouter

from app.api.active_case_integrity_guard import router as active_case_integrity_guard_router
from app.api.backup_center_guard import router as backup_center_guard_router
from app.api.staff_ui_guards import router as staff_ui_guards_router
from app.api.staff_ui_shell_guard import router as staff_ui_shell_guard_router
from app.api.superadmin_ui_guards import router as superadmin_ui_guards_router
from app.api.workdesk_ui_guard import router as workdesk_ui_guard_router

router = APIRouter(tags=["operator-guard-compat"])

# Keep only compatibility guards that have not yet been consolidated into their
# canonical endpoint modules. Do not add business endpoints here. Refunds now
# have one owner in app.api.refund_product.
router.include_router(active_case_integrity_guard_router)
router.include_router(backup_center_guard_router)
router.include_router(superadmin_ui_guards_router)
router.include_router(workdesk_ui_guard_router)
router.include_router(staff_ui_shell_guard_router)
router.include_router(staff_ui_guards_router)

__all__ = ["router"]
