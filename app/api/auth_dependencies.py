from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException, Request

from app.config import settings
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    decode_access_token,
    has_any_role,
    has_role,
)


def resolve_access_token(
    request: Request,
    *,
    x_admin_token: str | None = None,
    authorization: str | None = None,
) -> str | None:
    """Resolve credentials without exposing an HttpOnly session to JavaScript."""

    auth = str(authorization or "").strip()
    if auth.lower().startswith("bearer "):
        bearer = auth[7:].strip()
        if bearer:
            return bearer

    header_token = str(x_admin_token or "").strip()
    if header_token:
        return header_token

    cookie_token = request.cookies.get(settings.admin_session_cookie)
    return str(cookie_token).strip() if cookie_token else None


def parse_access_payload(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="bad token")
    try:
        uid = int(payload.get("uid", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="bad token") from exc

    if uid < 0 or (uid == 0 and not payload.get("legacy")):
        raise HTTPException(status_code=401, detail="bad token")

    payload["uid"] = uid
    return payload


def require_roles(token: str | None, roles: Iterable[str]) -> dict:
    payload = parse_access_payload(token)
    if not has_any_role(payload.get("roles"), roles):
        raise HTTPException(status_code=403, detail="forbidden")
    return payload


async def require_admin(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    token = resolve_access_token(
        request,
        x_admin_token=x_admin_token,
        authorization=authorization,
    )
    payload = parse_access_payload(token)
    if not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="forbidden")
    return payload


async def require_crm_reader(
    request: Request,
    x_admin_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict:
    token = resolve_access_token(
        request,
        x_admin_token=x_admin_token,
        authorization=authorization,
    )
    return require_roles(token, (ROLE_ADMIN, ROLE_OPERATOR))
