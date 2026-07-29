from fastapi import APIRouter, Header, Request
from fastapi.responses import RedirectResponse

from app.api.backup_center import backup_inventory
from app.api.security_event_center import require_security_superadmin
from app.config import settings

router = APIRouter(tags=["backup-manager"])


def _token(request: Request, header_token: str | None) -> str | None:
    return header_token or request.cookies.get(settings.admin_session_cookie)


@router.get("/backup-manager/ui")
async def ui():
    return RedirectResponse(url="/backup-center/ui", status_code=307)


@router.get("/backup-manager/status")
async def status(
    request: Request,
    x_admin_token: str | None = Header(default=None),
):
    require_security_superadmin(_token(request, x_admin_token))
    result = backup_inventory()
    result["deprecated_endpoint"] = True
    result["canonical_endpoint"] = "/backup-center/status"
    return result
