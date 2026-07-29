import re
import time

import httpx
import pyotp
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.api.mfa import router as mfa_router
from app.db.session import get_db
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.security.access_control import decode_access_token, hash_password
from app.security.mfa import decrypt_secret, recovery_code_count


async def create_database(tmp_path, name: str):
    path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


def build_app(factory):
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(mfa_router)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return app


async def create_superadmin(factory, suffix: int = 1):
    async with factory() as session:
        user = AdminUser(
            full_name=f"Суперадминистратор {suffix}",
            username=f"root-{suffix}",
            email=f"root-{suffix}@example.com",
            password_hash=hash_password("StrongPassword2026!"),
            role="superadmin,admin",
            is_active=True,
            session_version=1,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id, user.username


async def current_secret(factory, user_id: int) -> str:
    async with factory() as session:
        user = await session.get(AdminUser, user_id)
        secret = decrypt_secret(user.mfa_secret_encrypted)
        assert secret
        return secret


@pytest.mark.asyncio
async def test_superadmin_must_setup_mfa_before_session(tmp_path):
    engine, factory = await create_database(tmp_path, "mfa-setup.db")
    user_id, username = await create_superadmin(factory, 1)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        login = await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/mfa/setup"
        assert "dlc_mfa_challenge" in client.cookies
        assert "dlc_admin_session" not in client.cookies

        setup = await client.get("/mfa/setup")
        assert setup.status_code == 200
        secret = await current_secret(factory, user_id)
        setup_code = pyotp.TOTP(secret).now()
        confirmation = await client.post(
            "/mfa/setup",
            data={"code": setup_code},
        )
        assert confirmation.status_code == 200
        assert "MFA включена" in confirmation.text
        assert "dlc_admin_session" in client.cookies
        token = client.cookies["dlc_admin_session"]
        payload = decode_access_token(token)
        assert payload["mfa"] is True
        assert payload["sv"] == 2

        recovery_codes = re.findall(
            r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}",
            confirmation.text,
        )
        assert len(recovery_codes) == 10

        await client.post("/logout")
        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        replay = await client.post("/mfa/verify", data={"code": setup_code})
        assert replay.status_code == 401

    async with factory() as session:
        user = await session.get(AdminUser, user_id)
        assert user.mfa_enabled is True
        assert user.mfa_confirmed_at is not None
        assert user.session_version == 2
        assert recovery_code_count(user.mfa_recovery_codes) == 10
        assert recovery_codes[0] not in (user.mfa_recovery_codes or "")
        actions = (
            await session.execute(
                select(AuditLog.action).where(AuditLog.entity_id == user_id)
            )
        ).scalars().all()
        assert "security.mfa_enabled" in actions
    await engine.dispose()


@pytest.mark.asyncio
async def test_recovery_code_is_single_use(tmp_path):
    engine, factory = await create_database(tmp_path, "mfa-recovery.db")
    user_id, username = await create_superadmin(factory, 2)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        await client.get("/mfa/setup")
        secret = await current_secret(factory, user_id)
        result = await client.post(
            "/mfa/setup",
            data={"code": pyotp.TOTP(secret).now()},
        )
        code = re.findall(
            r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", result.text
        )[0]

        await client.post("/logout")
        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        first = await client.post("/mfa/verify", data={"code": code})
        assert first.status_code == 303
        await client.post("/logout")

        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        repeated = await client.post("/mfa/verify", data={"code": code})
        assert repeated.status_code == 401

    async with factory() as session:
        user = await session.get(AdminUser, user_id)
        assert recovery_code_count(user.mfa_recovery_codes) == 9
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_rotation_invalidates_old_token(tmp_path):
    engine, factory = await create_database(tmp_path, "mfa-rotation.db")
    user_id, username = await create_superadmin(factory, 3)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        await client.get("/mfa/setup")
        secret = await current_secret(factory, user_id)
        await client.post("/mfa/setup", data={"code": pyotp.TOTP(secret).now()})
        old_token = client.cookies["dlc_admin_session"]

        next_code = pyotp.TOTP(secret).at(int(time.time()) + 30)
        rotated = await client.post(
            "/mfa/sessions/rotate",
            data={
                "password": "StrongPassword2026!",
                "code": next_code,
            },
        )
        assert rotated.status_code == 303
        new_token = client.cookies["dlc_admin_session"]
        assert new_token != old_token
        assert decode_access_token(new_token)["sv"] == 3

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
        cookies={"dlc_admin_session": old_token},
    ) as stale_client:
        stale = await stale_client.get("/mfa/manage")
        assert stale.status_code == 401

    async with factory() as session:
        user = await session.get(AdminUser, user_id)
        assert user.session_version == 3
    await engine.dispose()


@pytest.mark.asyncio
async def test_mfa_locks_after_repeated_invalid_codes(tmp_path):
    engine, factory = await create_database(tmp_path, "mfa-lock.db")
    user_id, username = await create_superadmin(factory, 4)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        await client.post(
            "/login",
            data={"username": username, "password": "StrongPassword2026!"},
        )
        await client.get("/mfa/setup")
        for _ in range(5):
            response = await client.post("/mfa/setup", data={"code": "000000"})
            assert response.status_code == 401
        locked = await client.post("/mfa/setup", data={"code": "000000"})
        assert locked.status_code == 429
        assert int(locked.headers["retry-after"]) > 0

    async with factory() as session:
        user = await session.get(AdminUser, user_id)
        assert user.mfa_failed_attempts == 5
        assert user.mfa_locked_until is not None
    await engine.dispose()
