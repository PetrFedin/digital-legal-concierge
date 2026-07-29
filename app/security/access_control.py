from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Any, Iterable
from uuid import uuid4

from app.config import settings

ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"
ROLE_LAWYER = "lawyer"
ROLE_OPERATOR = "operator"
ROLE_TESTER = "tester"

VALID_ROLES = {
    ROLE_ADMIN,
    ROLE_SUPERADMIN,
    ROLE_LAWYER,
    ROLE_OPERATOR,
    ROLE_TESTER,
}

ROLE_LABELS = {
    ROLE_ADMIN: "Администратор",
    ROLE_SUPERADMIN: "Суперадминистратор",
    ROLE_LAWYER: "Юрист",
    ROLE_OPERATOR: "Оператор",
    ROLE_TESTER: "Тестировщик",
}


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


def has_any_role(value: str | Iterable[str] | None, required: Iterable[str]) -> bool:
    roles = set(normalize_roles(value))
    return any(has_role(roles, role) for role in required)


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


def _sign(prefix: str, payload: dict[str, Any]) -> str:
    body = _b64encode(
        json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    )
    signature = hmac.new(
        settings.admin_api_token.encode("utf-8"),
        f"{prefix}.{body}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{prefix}.{body}.{signature}"


def _decode_signed(token: str | None, prefix: str) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        actual_prefix, body, signature = token.split(".", 2)
        if actual_prefix != prefix:
            return None
        expected = hmac.new(
            settings.admin_api_token.encode("utf-8"),
            f"{prefix}.{body}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        payload = json.loads(_b64decode(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def create_access_token(
    user_id: int,
    username: str,
    roles: str | Iterable[str],
    ttl_seconds: int = 12 * 60 * 60,
    *,
    session_version: int = 1,
    mfa_verified: bool = False,
) -> str:
    normalized = normalize_roles(roles)
    if not normalized:
        raise ValueError("Не назначено ни одной допустимой роли")
    now = int(time.time())
    payload = {
        "uid": int(user_id),
        "username": username,
        "roles": normalized,
        "role": ROLE_SUPERADMIN if ROLE_SUPERADMIN in normalized else normalized[0],
        "iat": now,
        "exp": now + ttl_seconds,
        "jti": uuid4().hex,
        "sv": int(session_version or 1),
        "mfa": bool(mfa_verified),
        "v": 3,
    }
    return _sign("dlc1", payload)


def create_mfa_challenge_token(
    user_id: int,
    purpose: str,
    *,
    ttl_seconds: int = 10 * 60,
) -> str:
    if purpose not in {"setup", "verify"}:
        raise ValueError("Недопустимое назначение MFA challenge")
    now = int(time.time())
    return _sign(
        "dlcmfa1",
        {
            "uid": int(user_id),
            "purpose": purpose,
            "iat": now,
            "exp": now + ttl_seconds,
            "jti": uuid4().hex,
        },
    )


def decode_mfa_challenge_token(token: str | None) -> dict[str, Any] | None:
    payload = _decode_signed(token, "dlcmfa1")
    if not payload or payload.get("purpose") not in {"setup", "verify"}:
        return None
    return payload


def decode_access_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    if hmac.compare_digest(str(token), str(settings.admin_api_token)):
        if settings.app_env == "production":
            return None
        return {
            "uid": 0,
            "username": settings.admin_username or "admin",
            "roles": [ROLE_SUPERADMIN, ROLE_ADMIN],
            "role": ROLE_SUPERADMIN,
            "legacy": True,
            "mfa": True,
            "sv": 0,
        }
    payload = _decode_signed(token, "dlc1")
    if not payload:
        return None
    payload["roles"] = normalize_roles(payload.get("roles") or payload.get("role"))
    if not payload["roles"]:
        return None
    payload["role"] = (
        ROLE_SUPERADMIN if ROLE_SUPERADMIN in payload["roles"] else payload["roles"][0]
    )
    return payload


def verify_access_token(token: str | None, required_role: str = ROLE_ADMIN) -> bool:
    payload = decode_access_token(token)
    return bool(payload and has_role(payload.get("roles"), required_role))
