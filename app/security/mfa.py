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

from app.config import settings
from app.models.admin_user import AdminUser

RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.admin_api_token.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(secret: str) -> str:
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


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


def _recovery_digest(user_id: int, code: str) -> str:
    normalized = str(code or "").replace("-", "").replace(" ", "").upper()
    message = f"mfa-recovery:{int(user_id)}:{normalized}".encode("utf-8")
    return hmac.new(
        settings.admin_api_token.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def generate_recovery_codes(user_id: int) -> tuple[list[str], str]:
    codes: list[str] = []
    hashes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = "".join(secrets.choice(RECOVERY_ALPHABET) for _ in range(12))
        code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        codes.append(code)
        hashes.append(_recovery_digest(user_id, code))
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
    candidate = _recovery_digest(user.id, str(code or ""))
    for index, value in enumerate(hashes):
        if hmac.compare_digest(str(value), candidate):
            hashes.pop(index)
            user.mfa_recovery_codes = json.dumps(hashes, separators=(",", ":"))
            return True
    return False


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
