from __future__ import annotations

import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import Iterable

from app.config import settings

KEY_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
MIN_PRODUCTION_SECRET_LENGTH = 32
LEGACY_KEY_ID = "legacy-admin"


@dataclass(frozen=True)
class KeyEntry:
    key_id: str
    secret: str
    legacy: bool = False


@dataclass(frozen=True)
class KeyRing:
    purpose: str
    active: KeyEntry | None
    verification: tuple[KeyEntry, ...]

    def by_id(self, key_id: str) -> KeyEntry | None:
        for entry in self.verification:
            if hmac.compare_digest(entry.key_id, str(key_id or "")):
                return entry
        return None

    def require_active(self) -> KeyEntry:
        if self.active is None:
            raise RuntimeError(
                f"Не настроен активный ключ безопасности для контура {self.purpose}"
            )
        if (
            settings.app_env == "production"
            and len(self.active.secret) < MIN_PRODUCTION_SECRET_LENGTH
        ):
            raise RuntimeError(
                f"Ключ {self.purpose} короче {MIN_PRODUCTION_SECRET_LENGTH} символов"
            )
        return self.active


def _validate_key_id(value: str, purpose: str) -> str:
    key_id = str(value or "").strip()
    if not KEY_ID_RE.fullmatch(key_id):
        raise RuntimeError(
            f"Некорректный key id для {purpose}: допустимы A-Z, a-z, 0-9, _ и -"
        )
    return key_id


def _parse_previous(raw: str, purpose: str) -> list[KeyEntry]:
    entries: list[KeyEntry] = []
    for chunk in str(raw or "").split(","):
        value = chunk.strip()
        if not value:
            continue
        if ":" not in value:
            raise RuntimeError(
                f"Предыдущие ключи {purpose} должны иметь формат key_id:secret"
            )
        key_id, secret = value.split(":", 1)
        key_id = _validate_key_id(key_id, purpose)
        secret = secret.strip()
        if not secret:
            raise RuntimeError(f"Пустой предыдущий ключ {purpose}:{key_id}")
        entries.append(KeyEntry(key_id=key_id, secret=secret))
    return entries


def _deduplicate(entries: Iterable[KeyEntry]) -> tuple[KeyEntry, ...]:
    result: list[KeyEntry] = []
    seen_ids: set[str] = set()
    for entry in entries:
        if entry.key_id in seen_ids:
            continue
        seen_ids.add(entry.key_id)
        result.append(entry)
    return tuple(result)


def _allow_legacy_fallback() -> bool:
    return settings.app_env != "production" or bool(
        settings.allow_legacy_security_key_fallback
    )


def _build_ring(
    *,
    purpose: str,
    active_id: str,
    active_secret: str,
    previous: str,
) -> KeyRing:
    previous_entries = _parse_previous(previous, purpose)
    active: KeyEntry | None = None
    if str(active_secret or "").strip():
        active = KeyEntry(
            key_id=_validate_key_id(active_id, purpose),
            secret=str(active_secret).strip(),
        )
    elif _allow_legacy_fallback():
        active = KeyEntry(
            key_id=LEGACY_KEY_ID,
            secret=str(settings.admin_api_token),
            legacy=True,
        )

    verification: list[KeyEntry] = []
    if active:
        verification.append(active)
    verification.extend(previous_entries)
    if _allow_legacy_fallback() and all(
        entry.secret != str(settings.admin_api_token) for entry in verification
    ):
        verification.append(
            KeyEntry(
                key_id=LEGACY_KEY_ID,
                secret=str(settings.admin_api_token),
                legacy=True,
            )
        )
    return KeyRing(
        purpose=purpose,
        active=active,
        verification=_deduplicate(verification),
    )


def session_signing_ring() -> KeyRing:
    return _build_ring(
        purpose="session-signing",
        active_id=settings.session_signing_key_id,
        active_secret=settings.session_signing_key,
        previous=settings.session_signing_previous_keys,
    )


def security_hmac_ring() -> KeyRing:
    return _build_ring(
        purpose="security-hmac",
        active_id=settings.security_hmac_key_id,
        active_secret=settings.security_hmac_key,
        previous=settings.security_hmac_previous_keys,
    )


def mfa_encryption_ring() -> KeyRing:
    return _build_ring(
        purpose="mfa-encryption",
        active_id=settings.mfa_encryption_key_id,
        active_secret=settings.mfa_encryption_key,
        previous=settings.mfa_encryption_previous_keys,
    )


def hmac_digest(entry: KeyEntry, message: bytes) -> str:
    return hmac.new(entry.secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def hmac_candidates(message: bytes) -> list[tuple[str, str]]:
    return [
        (entry.key_id, hmac_digest(entry, message))
        for entry in security_hmac_ring().verification
    ]


def active_hmac_digest(message: bytes) -> tuple[str, str]:
    entry = security_hmac_ring().require_active()
    return entry.key_id, hmac_digest(entry, message)


def security_key_status() -> dict[str, object]:
    rings = {
        "session_signing": session_signing_ring(),
        "security_hmac": security_hmac_ring(),
        "mfa_encryption": mfa_encryption_ring(),
    }
    configured = {
        "session_signing": bool(str(settings.session_signing_key or "").strip()),
        "security_hmac": bool(str(settings.security_hmac_key or "").strip()),
        "mfa_encryption": bool(str(settings.mfa_encryption_key or "").strip()),
    }
    active_entries = [ring.active for ring in rings.values() if ring.active]
    strong = {
        name: bool(ring.active and len(ring.active.secret) >= MIN_PRODUCTION_SECRET_LENGTH)
        for name, ring in rings.items()
    }
    active_secrets = [entry.secret for entry in active_entries]
    distinct = len(active_secrets) == len(set(active_secrets)) == len(rings)
    separate_from_admin_token = all(
        entry.secret != str(settings.admin_api_token) for entry in active_entries
    ) and len(active_entries) == len(rings)
    previous_counts = {
        name: max(0, len(ring.verification) - (1 if ring.active else 0))
        for name, ring in rings.items()
    }
    production_ready = all(configured.values()) and all(strong.values()) and distinct
    production_ready = production_ready and separate_from_admin_token
    production_ready = production_ready and not settings.allow_legacy_security_key_fallback
    if settings.app_env != "production":
        production_ready = True
    return {
        "ok": production_ready,
        "configured": configured,
        "strong": strong,
        "distinct": distinct,
        "separate_from_admin_token": separate_from_admin_token,
        "legacy_fallback_enabled": bool(settings.allow_legacy_security_key_fallback),
        "active_key_ids": {
            name: ring.active.key_id if ring.active else None
            for name, ring in rings.items()
        },
        "previous_key_counts": previous_counts,
    }
