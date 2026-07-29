import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.models import Base
from app.models.admin_user import AdminUser
from app.security.key_rotation import reencrypt_mfa_secrets
from app.security.mfa import encrypt_secret

MFA_OLD = "mfa-old-" + "e" * 40
MFA_NEW = "mfa-new-" + "f" * 40


@pytest.mark.asyncio
async def test_mfa_rotation_batches_progress_past_updated_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "allow_legacy_security_key_fallback", False)
    monkeypatch.setattr(settings, "mfa_encryption_key_id", "mfa-old")
    monkeypatch.setattr(settings, "mfa_encryption_key", MFA_OLD)
    monkeypatch.setattr(settings, "mfa_encryption_previous_keys", "")
    first_secret = encrypt_secret("JBSWY3DPEHPK3PXP")
    second_secret = encrypt_secret("KRSXG5DSNFXGOIDB")

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'batch.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all(
            [
                AdminUser(
                    full_name="First Admin",
                    username="first",
                    email="first@example.com",
                    password_hash="test",
                    role="superadmin,admin",
                    mfa_secret_encrypted=first_secret,
                ),
                AdminUser(
                    full_name="Second Admin",
                    username="second",
                    email="second@example.com",
                    password_hash="test",
                    role="superadmin,admin",
                    mfa_secret_encrypted=second_secret,
                ),
            ]
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
        assert await reencrypt_mfa_secrets(db, limit=1) == 1
        await db.commit()
    async with factory() as db:
        assert await reencrypt_mfa_secrets(db, limit=1) == 1
        await db.commit()
    async with factory() as db:
        assert await reencrypt_mfa_secrets(db, limit=1) == 0

    await engine.dispose()
