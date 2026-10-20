"""CSV personally identifiable data and independently mounted staff routes are gated.

Synthetic isolated FastAPI + SQLite. No real Telegram, payments or user records.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.access_management import router as access_router
from app.api.backup_center import router as backup_router
from app.api.consultation_outcomes import router as outcome_router
from app.api.exports import router as export_router
from app.api.payment_review_center import router as review_router
from app.api.security_event_center import router as security_router
from app.api.workdesk import router as workdesk_router
from app.config import settings
from app.db.session import get_db
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.user import User
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN, create_access_token
from app.security.token_revocation import revoke_token


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'export_privacy_p0.db'}"
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


def application(factory):
    app = FastAPI()
    for router in (
        export_router,
        workdesk_router,
        review_router,
        outcome_router,
        access_router,
        security_router,
        backup_router,
    ):
        app.include_router(router)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return app


async def actor(factory, *, uid=99771, role="admin", mfa=False):
    async with factory() as db:
        db.add(AdminUser(
            id=uid, full_name="Synthetic export staff", username=f"export-{uid}",
            email=f"export-{uid}@example.test",
            password_hash="non-login-test",
            role=role, is_active=True, session_version=2, mfa_enabled=mfa,
        ))
        await db.commit()
    return create_access_token(
        uid, f"export-{uid}", role.split(","),
        session_version=2, mfa_verified=mfa,
    )


@pytest.mark.asyncio
async def test_query_token_cannot_download_private_client_csv(factory):
    app = application(factory)
    async with factory() as db:
        db.add(User(
            telegram_id=9971100,
            full_name="Synthetic client export",
            email="private-customer@example.test",
            phone="+70000000111",
        ))
        await db.commit()
    token = await actor(factory)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test", follow_redirects=False,
    ) as client:
        direct = await client.get("/admin/export/clients.csv")
        assert direct.status_code == 401
        assert "private-customer@example.test" not in direct.text

        query = await client.get(
            "/admin/export/clients.csv",
            params={"token": settings.admin_api_token},
        )
        assert query.status_code == 401
        assert "private-customer@example.test" not in query.text

        signed = await client.get(
            "/admin/export/clients.csv",
            headers={"x-admin-token": token},
        )
        assert signed.status_code == 200, signed.text
        assert "private-customer@example.test" in signed.text
        assert "attachment" in signed.headers.get("content-disposition", "")

        async with factory() as db:
            assert await revoke_token(db, token, reason="logout")
            await db.commit()

        stale = await client.get(
            "/admin/export/clients.csv",
            headers={"x-admin-token": token},
        )
        assert stale.status_code == 401
        assert "private-customer@example.test" not in stale.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "/admin/export/clients.csv",
        "/admin/payment-reviews",
        "/admin/consultation-outcomes",
        "/admin/workdesk/attention",
        "/access/users",
        "/security-events/status",
        "/backup-center/status",
    ],
)
async def test_independent_staff_routers_reject_anonymous_reads(factory, url):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(factory)),
        base_url="http://test", follow_redirects=False,
    ) as client:
        response = await client.get(url)
        assert response.status_code == 401, (url, response.text)


@pytest.mark.asyncio
async def test_revoked_superadmin_cannot_manage_users(factory):
    token = await actor(factory, uid=99772, role="superadmin,admin", mfa=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=application(factory)),
        base_url="http://test", follow_redirects=False,
    ) as client:
        authorized = await client.get(
            "/access/users", headers={"x-admin-token": token},
        )
        assert authorized.status_code == 200, authorized.text

        async with factory() as db:
            assert await revoke_token(db, token, reason="logout")
            await db.commit()

        for url in ("/access/users", "/security-events/status", "/backup-center/status"):
            response = await client.get(url, headers={"x-admin-token": token})
            assert response.status_code == 401, (url, response.text)
