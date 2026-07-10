from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Iterable

from app.config import settings

ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"
ROLE_LAWYER = "lawyer"
VALID_ROLES = {ROLE_ADMIN, ROLE_SUPERADMIN, ROLE_LAWYER}


def normalize_roles(value: str | Iterable[str] | None) -> list[str]:
    if value is None:
        return []
    raw = value if not isinstance(value, str) else value.replace(";", ",").split(",")
    roles: list[str] = []
    for item in raw:
        role = str(item).strip().lower()
        if role in VALID_ROLES and role not in roles:
            roles.append(role)
    if ROLE_SUPERADMIN in roles and ROLE_ADMIN not in roles:
        roles.append(ROLE_ADMIN)
    return roles


def serialize_roles(value: str | Iterable[str] | None) -> str:
    roles = normalize_roles(value)
    return ",".join(roles or [ROLE_ADMIN])


def has_role(value: str | Iterable[str] | None, role: str) -> bool:
    roles = set(normalize_roles(value))
    if role == ROLE_ADMIN:
        return bool(roles & {ROLE_ADMIN, ROLE_SUPERADMIN})
    return role in roles


def hash_password(password: str, salt: str | None = None) -> str:
    if not password or len(password) < 8:
        raise ValueError("Пароль должен содержать не менее 8 символов")
    salt_bytes = bytes.fromhex(salt) if salt else os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, 210_000)
    return f"pbkdf2_sha256${salt_bytes.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    if not encoded or not encoded.startswith("pbkdf2_sha256$"):
        return hmac.compare_digest(password or "", encoded or "")
    try:
        _, salt, expected = encoded.split("$", 2)
        actual = hash_password(password, salt).split("$", 2)[2]
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def create_access_token(user_id: int, username: str, roles: str | Iterable[str], ttl_seconds: int = 12 * 60 * 60) -> str:
    normalized = normalize_roles(roles)
    if not normalized:
        raise ValueError("Не назначено ни одной допустимой роли")
    payload = {
        "uid": int(user_id),
        "username": username,
        "roles": normalized,
        "role": ROLE_SUPERADMIN if ROLE_SUPERADMIN in normalized else normalized[0],
        "exp": int(time.time()) + ttl_seconds,
        "v": 2,
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signature = hmac.new(settings.admin_api_token.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"dlc1.{body}.{signature}"


def decode_access_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    if hmac.compare_digest(str(token), str(settings.admin_api_token)):
        return {"uid": 0, "username": settings.admin_username or "admin", "roles": [ROLE_SUPERADMIN, ROLE_ADMIN], "role": ROLE_SUPERADMIN, "legacy": True}
    try:
        prefix, body, signature = token.split(".", 2)
        if prefix != "dlc1":
            return None
        expected = hmac.new(settings.admin_api_token.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        payload["roles"] = normalize_roles(payload.get("roles") or payload.get("role"))
        if not payload["roles"]:
            return None
        payload["role"] = ROLE_SUPERADMIN if ROLE_SUPERADMIN in payload["roles"] else payload["roles"][0]
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def verify_access_token(token: str | None, required_role: str = ROLE_ADMIN) -> bool:
    payload = decode_access_token(token)
    return bool(payload and has_role(payload.get("roles"), required_role))
