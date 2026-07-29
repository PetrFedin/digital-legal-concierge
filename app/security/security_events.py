from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionLocal
from app.models.audit_log import AuditLog
from app.security.keyring import active_hmac_digest

logger = logging.getLogger(__name__)

VALID_SEVERITIES = {"info", "warning", "critical"}
SENSITIVE_KEY_MARKERS = (
    "password",
    "passwd",
    "secret",
    "token",
    "authorization",
    "cookie",
    "credential",
    "private_key",
    "recovery_code",
    "totp",
)
MAX_STRING_LENGTH = 500
_FALLBACK_PSEUDONYM_KEY = os.urandom(32)
_RECENT_EVENTS: dict[str, float] = {}


def _is_sensitive_key(key: object) -> bool:
    normalized = str(key or "").strip().lower().replace("-", "_")
    return any(marker in normalized for marker in SENSITIVE_KEY_MARKERS)


def sanitize_security_details(value: Any, *, depth: int = 0) -> Any:
    """Return JSON-safe security evidence without credentials or unbounded payloads."""

    if depth > 5:
        return "[truncated]"
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:50]:
            name = str(key)[:100]
            result[name] = (
                "[redacted]"
                if _is_sensitive_key(name)
                else sanitize_security_details(item, depth=depth + 1)
            )
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize_security_details(item, depth=depth + 1) for item in list(value)[:50]]
    if isinstance(value, bytes):
        return f"[bytes:{len(value)}]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = str(value)
    return text if len(text) <= MAX_STRING_LENGTH else text[:MAX_STRING_LENGTH] + "…"


def pseudonymize_security_value(namespace: str, value: object | None) -> str | None:
    """Create a stable keyed reference without storing usernames, IPs or origins."""

    if value is None:
        return None
    normalized = str(value).strip().lower()
    if not normalized:
        return None
    message = f"security-event:{namespace}:{normalized}".encode("utf-8")
    try:
        key_id, digest = active_hmac_digest(message)
    except RuntimeError:
        digest = hmac.new(_FALLBACK_PSEUDONYM_KEY, message, hashlib.sha256).hexdigest()
        key_id = "ephemeral"
    return f"{key_id}:{digest[:24]}"


def security_event_severity(action: str, new_value: dict | None = None) -> str:
    action = str(action or "")
    details = new_value or {}
    explicit = str(details.get("severity") or "").lower()
    if explicit in VALID_SEVERITIES:
        return explicit
    if action in {
        "security.admin_login_locked",
        "security.mfa_locked",
        "security.mfa_reset_by_superadmin",
        "security.audit_integrity_failed",
    }:
        return "critical"
    if action == "security.mfa_login" and details.get("method") == "recovery_code":
        return "warning"
    if action in {
        "security.origin_blocked",
        "security.mfa_recovery_codes_rotated",
        "DOCUMENT_UPLOAD_REJECTED",
    }:
        return "warning"
    return "info"


async def record_security_event(
    db: AsyncSession,
    *,
    action: str,
    severity: str,
    source: str,
    actor_id: int | None = None,
    principal: object | None = None,
    client_address: object | None = None,
    resource_type: str = "security_event",
    resource_id: int | None = None,
    details: dict[str, Any] | None = None,
    comment: str | None = None,
) -> AuditLog:
    normalized_severity = str(severity or "warning").lower()
    if normalized_severity not in VALID_SEVERITIES:
        raise ValueError("Недопустимый уровень события безопасности")
    if not str(action or "").startswith("security."):
        raise ValueError("Событие безопасности должно начинаться с security.")

    evidence = {
        "severity": normalized_severity,
        "source": str(source or "application")[:100],
        "principal_ref": pseudonymize_security_value("principal", principal),
        "client_ref": pseudonymize_security_value("client", client_address),
        "details": sanitize_security_details(details or {}),
    }
    event = AuditLog(
        actor_type="admin_user" if actor_id else "security_system",
        actor_id=actor_id,
        action=str(action)[:100],
        entity_type=str(resource_type or "security_event")[:100],
        entity_id=resource_id,
        old_value=None,
        new_value=evidence,
        comment=sanitize_security_details(comment) if comment else None,
    )
    db.add(event)
    await db.flush()
    return event


def _sample_allowed(fingerprint: str, sample_seconds: int) -> bool:
    now = time.monotonic()
    cutoff = now - max(1, int(sample_seconds))
    if len(_RECENT_EVENTS) > 2048:
        for key, seen_at in list(_RECENT_EVENTS.items()):
            if seen_at < cutoff:
                _RECENT_EVENTS.pop(key, None)
    if _RECENT_EVENTS.get(fingerprint, 0.0) >= cutoff:
        return False
    _RECENT_EVENTS[fingerprint] = now
    return True


async def record_security_event_best_effort(
    *,
    action: str,
    severity: str,
    source: str,
    actor_id: int | None = None,
    principal: object | None = None,
    client_address: object | None = None,
    resource_type: str = "security_event",
    resource_id: int | None = None,
    details: dict[str, Any] | None = None,
    comment: str | None = None,
    sample_seconds: int = 60,
) -> bool:
    principal_ref = pseudonymize_security_value("principal", principal) or "-"
    client_ref = pseudonymize_security_value("client", client_address) or "-"
    fingerprint = "|".join(
        [
            str(action),
            str(actor_id or 0),
            principal_ref,
            client_ref,
            str(resource_type),
            str(resource_id or 0),
            str((details or {}).get("reason") or ""),
            str((details or {}).get("path") or ""),
        ]
    )
    if not _sample_allowed(fingerprint, sample_seconds):
        return False
    try:
        async with AsyncSessionLocal() as db:
            await record_security_event(
                db,
                action=action,
                severity=severity,
                source=source,
                actor_id=actor_id,
                principal=principal,
                client_address=client_address,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                comment=comment,
            )
            await db.commit()
        return True
    except Exception as error:  # pragma: no cover - logging must never block a request
        logger.warning("Security event could not be persisted: %s", error)
        return False
