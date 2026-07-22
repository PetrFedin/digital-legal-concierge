from __future__ import annotations

from collections.abc import Iterable

from fastapi import Header, HTTPException

from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_OPERATOR,
    decode_access_token,
    has_any_role,
    has_role,
)


def parse_access_payload(token: str | None) -> dict:
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="bad token")
    try:
        payload["uid"] = int(payload.get("uid", 0))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=401, detail="bad token") from exc
    return payload


def require_roles(token: str | None, roles: Iterable[str]) -> dict:
    payload = parse_access_payload(token)
    if not has_any_role(payload.get("roles"), roles):
        raise HTTPException(status_code=403, detail="forbidden")
    return payload


async def require_admin(
    x_admin_token: str | None = Header(default=None),
) -> dict:
    payload = parse_access_payload(x_admin_token)
    if not has_role(payload.get("roles"), ROLE_ADMIN):
        raise HTTPException(status_code=403, detail="forbidden")
    return payload


async def require_crm_reader(
    x_admin_token: str | None = Header(default=None),
) -> dict:
    return require_roles(x_admin_token, (ROLE_ADMIN, ROLE_OPERATOR))
