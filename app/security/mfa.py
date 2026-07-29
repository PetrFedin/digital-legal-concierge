from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from datetime import datetime, timezone

import pyotp
from cryptography.fernet import Fernet, InvalidToken

from app.models.admin_user import AdminUser
from app.security.keyring import (
    KeyEntry,
    active_hmac_digest,
    hmac_candidates,
    hmac_digest,
    mfa_encryption_ring,
    security_hmac_ring,
)

RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MFA_CIPHERTEXT_VERSION = "v2"


def _fernet(entry: KeyEntry) -> Fernet:
    digest = hashlib.sha256(entry.secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    entry = mfa_encryption_ring().require_active()
    encrypted = _fernet(entry).encrypt(secret.encode("utf-8")).decode("ascii")
    return f"{MFA_CIPHERTEXT_VERSION}:{entry.key_id}:{encrypted}"


def _decrypt_with(entry: KeyEntry, encrypted: str) -> str | None:
    try:
        return _fernet(entry).decrypt(encrypted.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
        return None


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    ring = mfa_encryption_ring()
    raw = str(value)
    if raw.startswith(f"{MFA_CIPHERTEXT_VERSION}:"):
        try:
            _, key_id, encrypted = raw.split(":", 2)
        except ValueError:
            return None
        entry = ring.by_id(key_id)
        return _decrypt_with(entry, encrypted) if entry else None

    # Pre-keyring ciphertext had no envelope. It is accepted only by keys kept
    # in the configured decryption grace ring.
    for entry in ring.verification:
        decrypted = _decrypt_with(entry, raw)
        if decrypted is not None:
            return decrypted
    return None


def secret_needs_reencryption(value: str | None) -> bool:
    if not value:
        return False
    active = mfa_encryption_ring().require_active()
    prefix = f"{MFA_CIPHERTEXT_VERSION}:{active.key_id}:"
    return not str(value).startswith(prefix)


def reencrypt_secret(value: str | None) -> str | None:
    secret = decrypt_secret(value)
    if secret is None:
        return None
    if secret_needs_reencryption(value):
        return encrypt_secret(secret)
    return str(value)


def generate_totp_secret() -> str:
    return pyotp.random_base32(length=32)


def provisioning_uri(user: AdminUser, secret: str) -> str:
    label = user.email or user.username or f"admin-{user.id}"
    return pyotp.TOTP(secret).provisioning_uri(
        name=label,
        issuer_name="Digital Legal Concierge",
    )


def verify_totp_counter(secret: str | None, code: str | None) -> int | None:
    normalized = str(code or "").replace(" ", "").strip()
    if not secret or not normalized.isdigit() or len(normalized) != 6:
        return None
    totp = pyotp.TOTP(secret)
    current_counter = int(time.time()) // int(totp.interval)
    for counter in (current_counter - 1, current_counter, current_counter + 1):
        if hmac.compare_digest(totp.generate_otp(counter), normalized):
            return counter
    return None


def verify_totp(secret: str | None, code: str | None) -> bool:
    return verify_totp_counter(secret, code) is not None


def consume_totp(user: AdminUser, secret: str | None, code: str | None) -> bool:
    counter = verify_totp_counter(secret, code)
    if counter is None:
        return False
    last_counter = user.mfa_last_totp_step
    if last_counter is not None and counter <= int(last_counter):
        return False
    user.mfa_last_totp_step = counter
    return True


def _recovery_message(user_id: int, code: str) -> bytes:
    normalized = str(code or "").replace("-", "").replace(" ", "").upper()
    return f"mfa-recovery:{int(user_id)}:{normalized}".encode("utf-8")


def _new_recovery_digest(user_id: int, code: str) -> str:
    key_id, digest = active_hmac_digest(_recovery_message(user_id, code))
    return f"{key_id}${digest}"


def _matches_recovery_digest(user_id: int, code: str, stored: str) -> bool:
    value = str(stored or "")
    message = _recovery_message(user_id, code)
    if "$" in value:
        key_id, expected = value.split("$", 1)
        entry = security_hmac_ring().by_id(key_id)
        return bool(
            entry
            and hmac.compare_digest(hmac_digest(entry, message), expected)
        )
    # Existing installations stored only the digest. Compare against the
    # verification keyring while the old HMAC key remains in the grace set.
    return any(
        hmac.compare_digest(candidate, value)
        for _, candidate in hmac_candidates(message)
    )


def generate_recovery_codes(user_id: int) -> tuple[list[str], str]:
    codes: list[str] = []
    hashes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        codes.append(code)
        hashes.append(_new_recovery_digest(user_id, code))
    return codes, json.dumps(hashes, separators=(",", ":"))


def recovery_code_count(value: str | None) -> int:
    try:
        decoded = json.loads(value or "[]")
        return len(decoded) if isinstance(decoded, list) else 0
    except (TypeError, ValueError, json.JSONDecodeError):
        return 0


def consume_recovery_code(user: AdminUser, code: str | None) -> bool:
    try:
        hashes = json.loads(user.mfa_recovery_codes or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        hashes = []
    if not isinstance(hashes, list):
        return False
    for index, value in enumerate(hashes):
        if _matches_recovery_digest(user.id, str(code or ""), str(value)):
            hashes.pop(index)
            user.mfa_recovery_codes = json.dumps(hashes, separators=(",", ":"))
            return True
    return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
