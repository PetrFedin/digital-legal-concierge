import base64
import hashlib
import hmac
import json
import time

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.admin_user import AdminUser
from app.security.access_control import create_access_token, decode_access_token
from app.security.key_rotation import reencrypt_mfa_secrets
from app.security.keyring import security_key_status
from app.security.login_throttle import LoginThrottleService
from app.security.mfa import (
    consume_recovery_code,
    decrypt_secret,
    encrypt_secret,
    generate_recovery_codes,
    reencrypt_secret,
)

SESSION_OLD = "session-old-" + "a" * 40
SESSION_NEW = "session-new-" + "b" * 40
HMAC_OLD = "hmac-old-" + "c" * 40
HMAC_NEW = "hmac-new-" + "d" * 40
MFA_OLD = "mfa-old-" + "e" * 40
MFA_NEW = "mfa-new-" + "f" * 40
ADMIN_TOKEN = "admin-api-" + "z" * 40


def configure_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "admin_api_token", ADMIN_TOKEN)
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "session_signing_key_id", "session-old")
    monkeypatch.setattr(settings, "session_signing_key", SESSION_OLD)
    monkeypatch.setattr(settings, "session_signing_previous_keys", "")
    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-old")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_OLD)
    monkeypatch.setattr(settings, "security_hmac_previous_keys", "")
    monkeypatch.setattr(settings, "mfa_encryption_key_id", "mfa-old")
    monkeypatch.setattr(settings, "mfa_encryption_key", MFA_OLD)
    monkeypatch.setattr(settings, "mfa_encryption_previous_keys", "")


def legacy_signed_token(payload: dict, secret: str) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii").rstrip("=")
    signature = hmac.new(
        secret.encode("utf-8"),
        f"dlc1.{body}".encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"dlc1.{body}.{signature}"


def test_session_signing_rotation_uses_key_id_and_grace_ring(monkeypatch):
    configure_production(monkeypatch)
    token = create_access_token(7, "root", "superadmin,admin", mfa_verified=True)
    assert decode_access_token(token)["kid"] == "session-old"

    monkeypatch.setattr(settings, "session_signing_key_id", "session-new")
    monkeypatch.setattr(settings, "session_signing_key", SESSION_NEW)
    monkeypatch.setattr(
        settings,
        "session_signing_previous_keys",
        f"session-old:{SESSION_OLD}",
    )
    assert decode_access_token(token)["uid"] == 7

    monkeypatch.setattr(settings, "session_signing_previous_keys", "")
    assert decode_access_token(token) is None


def test_pre_keyring_session_requires_explicit_legacy_grace(monkeypatch):
    configure_production(monkeypatch)
    payload = {
        "uid": 9,
        "username": "legacy-root",
        "roles": ["superadmin", "admin"],
        "role": "superadmin",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "sv": 1,
        "mfa": True,
        "v": 3,
    }
    token = legacy_signed_token(payload, ADMIN_TOKEN)
    assert decode_access_token(token) is None

    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", True)
    assert decode_access_token(token)["uid"] == 9

    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    assert decode_access_token(token) is None


def test_mfa_encryption_rotation_and_legacy_ciphertext(monkeypatch):
    configure_production(monkeypatch)
    encrypted = encrypt_secret("JBSWY3DPEHPK3PXP")
    assert encrypted.startswith("v2:mfa-old:")

    legacy_ciphertext = encrypted.split(":", 2)[2]
    monkeypatch.setattr(settings, "mfa_encryption_key_id", "mfa-new")
    monkeypatch.setattr(settings, "mfa_encryption_key", MFA_NEW)
    monkeypatch.setattr(
        settings,
        "mfa_encryption_previous_keys",
        f"mfa-old:{MFA_OLD}",
    )

    assert decrypt_secret(encrypted) == "JBSWY3DPEHPK3PXP"
    assert decrypt_secret(legacy_ciphertext) == "JBSWY3DPEHPK3PXP"
    rotated = reencrypt_secret(encrypted)
    assert rotated.startswith("v2:mfa-new:")

    monkeypatch.setattr(settings, "mfa_encryption_previous_keys", "")
    assert decrypt_secret(encrypted) is None
    assert decrypt_secret(rotated) == "JBSWY3DPEHPK3PXP"


def test_recovery_codes_survive_hmac_rotation_during_grace(monkeypatch):
    configure_production(monkeypatch)
    codes, encoded = generate_recovery_codes(11)
    assert "hmac-old$" in encoded
    user = AdminUser(id=11, username="root", mfa_recovery_codes=encoded)

    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-new")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_NEW)
    monkeypatch.setattr(
        settings,
        "security_hmac_previous_keys",
        f"hmac-old:{HMAC_OLD}",
    )
    assert consume_recovery_code(user, codes[0]) is True
    assert consume_recovery_code(user, codes[0]) is False


def test_security_readiness_requires_three_distinct_keys(monkeypatch):
    configure_production(monkeypatch)
    assert security_key_status()["ok"] is True

    monkeypatch.setattr(settings, "security_hmac_key", SESSION_OLD)
    status = security_key_status()
    assert status["ok"] is False
    assert status["distinct"] is False

    monkeypatch.setattr(settings, "security_hmac_key", HMAC_OLD)
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", True)
    assert security_key_status()["ok"] is False


@pytest.mark.asyncio
async def test_scheduler_reencrypts_existing_mfa_secrets(tmp_path, monkeypatch):
    configure_production(monkeypatch)
    old_value = encrypt_secret("KRSXG5DSNFXGOIDB")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'rotation.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(
            AdminUser(
                username="root",
                password_hash="test",
                role="superadmin,admin",
                is_active=True,
                mfa_secret_encrypted=old_value,
            )
        )
        await db.commit()

    monkeypatch.setattr(settings, "mfa_encryption_key_id", "mfa-new")
    monkeypatch.setattr(settings, "mfa_encryption_key", MFA_NEW)
    monkeypatch.setattr(
        settings,
        "mfa_encryption_previous_keys",
        f"mfa-old:{MFA_OLD}",
    )
    async with factory() as db:
        assert await reencrypt_mfa_secrets(db) == 1
        await db.commit()
        user = await db.get(AdminUser, 1)
        assert user.mfa_secret_encrypted.startswith("v2:mfa-new:")
        assert decrypt_secret(user.mfa_secret_encrypted) == "KRSXG5DSNFXGOIDB"
    await engine.dispose()


@pytest.mark.asyncio
async def test_login_throttle_count_survives_hmac_rotation(tmp_path, monkeypatch):
    configure_production(monkeypatch)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'throttle.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as db:
        service = LoginThrottleService(db)
        for _ in range(4):
            await service.register_failure(
                principal="root@example.com",
                client_address="127.0.0.1",
            )
        await db.commit()

    monkeypatch.setattr(settings, "security_hmac_key_id", "hmac-new")
    monkeypatch.setattr(settings, "security_hmac_key", HMAC_NEW)
    monkeypatch.setattr(
        settings,
        "security_hmac_previous_keys",
        f"hmac-old:{HMAC_OLD}",
    )
    async with factory() as db:
        service = LoginThrottleService(db)
        lock_seconds = await service.register_failure(
            principal="root@example.com",
            client_address="127.0.0.1",
        )
        assert lock_seconds == 15 * 60
        await db.commit()
    await engine.dispose()
