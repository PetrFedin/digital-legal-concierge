"""P0: signed staff JWTs must lose access after revocation or role changes.

All cases and identities are synthetic; no Telegram or payment provider calls.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin import router as admin_router
from app.api.auth import router as auth_router
from app.api.message_center import router as messages_router
from app.config import settings
from app.db.session import get_db
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    create_access_token,
)
from app.security.lawyer_access import require_lawyer_actor
from app.security.token_revocation import revoke_token


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'staff_session_p0.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def add_actor(factory, *, role="admin", uid=99871, mfa_enabled=False):
    async with factory() as db:
        db.add(AdminUser(
            id=uid, full_name="Synthetic staff P0", username=f"staff-p0-{uid}",
            email=f"staff-p0-{uid}@example.test", role=role,
            is_active=True, password_hash="non-login-test-hash",
            session_version=2, mfa_enabled=mfa_enabled,
        ))
        await db.commit()
    roles = [x.strip() for x in role.split(",")]
    return create_access_token(
        uid, f"staff-p0-{uid}", roles, session_version=2,
        mfa_verified=mfa_enabled,
    )


def application(factory):
    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(messages_router)
    app.include_router(auth_router)

    async def override_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["revoked", "inactive", "demoted", "rotated"])
async def test_admin_message_and_auth_reads_recheck_current_identity(factory, mutation):
    token = await add_actor(factory)
    app = application(factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test", follow_redirects=False,
    ) as client:
        client.cookies.set(settings.admin_session_cookie, token)
        hdr = {"x-admin-token": token}
        assert (await client.get("/admin/cases", headers=hdr)).status_code == 200
        assert (await client.get("/message-center/status", headers=hdr)).status_code == 200
        assert (await client.get("/auth/session")).status_code == 200

        async with factory() as db:
            account = await db.get(AdminUser, 99871)
            if mutation == "revoked":
                assert await revoke_token(db, token, reason="logout")
            elif mutation == "inactive":
                account.is_active = False
            elif mutation == "demoted":
                account.role = "operator"
            else:
                account.session_version += 1
            await db.commit()

        expected = 401 if mutation == "revoked" else 403
        for url in ("/admin/cases", "/message-center/status"):
            resp = await client.get(url, headers=hdr)
            assert resp.status_code == expected, (url, resp.text)
        assert (await client.get("/auth/session")).status_code == expected
        # Mutating administrative API must be blocked before executing a handler.
        resp = await client.post(
            "/admin/cases/1/assign/1", headers=hdr,
        )
        assert resp.status_code == expected


@pytest.mark.asyncio
async def test_lawyer_actor_access_is_revoked_immediately(factory):
    token = await add_actor(factory, role="lawyer", uid=99872)
    async with factory() as db:
        db.add(Lawyer(
            full_name="Synthetic lawyer P0", email="staff-p0-99872@example.test",
            is_active=True, workload_limit=20,
        ))
        await db.commit()

    async with factory() as db:
        actor = await require_lawyer_actor(db, token)
        assert actor.account.id == 99872
        assert await revoke_token(db, token, reason="logout")
        await db.commit()

    async with factory() as db:
        with pytest.raises(Exception) as denied:
            await require_lawyer_actor(db, token)
        assert getattr(denied.value, "status_code", None) == 401


@pytest.mark.asyncio
async def test_superadmin_without_verified_mfa_cannot_read_staff_settings(factory):
    token = await add_actor(factory, role="superadmin,admin", uid=99873, mfa_enabled=True)
    from app.security.access_control import create_access_token
    token = create_access_token(
        99873, "staff-p0-99873", [ROLE_SUPERADMIN, ROLE_ADMIN],
        session_version=2, mfa_verified=False,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(factory)),
        base_url="http://test", follow_redirects=False,
    ) as client:
        response = await client.get(
            "/admin/cases", headers={"x-admin-token": token},
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_missing_session_is_unauthorized_in_message_and_auth(factory):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(factory)),
        base_url="http://test", follow_redirects=False,
    ) as client:
        assert (await client.get("/message-center/status")).status_code == 401
        assert (await client.get("/auth/session")).status_code == 401
