from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.db.session import get_db
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.login_security_state import LoginSecurityState
from app.models.revoked_access_token import RevokedAccessToken
from app.security.access_control import hash_password
from app.security.login_throttle import LoginRateLimitError, LoginThrottleService
from app.security.token_revocation import cleanup_revoked_tokens


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

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    return app


async def create_admin(factory, suffix: int = 1):
    async with factory() as session:
        user = AdminUser(
            full_name=f"Администратор {suffix}",
            username=f"MixedAdmin{suffix}",
            email=f"MixedAdmin{suffix}@Example.com",
            password_hash=hash_password("StrongPassword2026!"),
            role="admin",
            is_active=True,
            session_version=1,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user.id, user.username


@pytest.mark.asyncio
async def test_login_throttle_persists_across_sessions(tmp_path):
    engine, factory = await create_database(tmp_path, "persistent-throttle.db")
    for _ in range(5):
        async with factory() as session:
            service = LoginThrottleService(session)
            await service.register_failure(
                principal="admin@example.com",
                client_address="203.0.113.7",
            )
            await session.commit()

    async with factory() as new_process_session:
        with pytest.raises(LoginRateLimitError) as caught:
            await LoginThrottleService(new_process_session).check(
                principal="admin@example.com",
                client_address="203.0.113.7",
            )
        assert caught.value.retry_after > 0
        rows = (
            await new_process_session.execute(select(LoginSecurityState))
        ).scalars().all()
        assert len(rows) == 3
        assert all("admin@example.com" not in row.key_hash for row in rows)
        assert all("203.0.113.7" not in row.key_hash for row in rows)
    await engine.dispose()


@pytest.mark.asyncio
async def test_login_blocks_after_five_failures_and_is_case_insensitive(tmp_path):
    engine, factory = await create_database(tmp_path, "endpoint-throttle.db")
    _, username = await create_admin(factory, 2)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("198.51.100.9", 55000)),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        unknown = await client.post(
            "/login",
            data={"username": "does-not-exist", "password": "wrong-password"},
        )
        assert unknown.status_code == 401
        assert unknown.json()["detail"] == "Неверный логин или пароль"

        for _ in range(4):
            response = await client.post(
                "/login",
                data={"username": username.lower(), "password": "wrong-password"},
            )
            assert response.status_code == 401
        fifth = await client.post(
            "/login",
            data={"username": username.upper(), "password": "wrong-password"},
        )
        assert fifth.status_code == 429
        assert int(fifth.headers["retry-after"]) > 0

        locked_correct_password = await client.post(
            "/login",
            data={
                "username": username.swapcase(),
                "password": "StrongPassword2026!",
            },
        )
        assert locked_correct_password.status_code == 429

    async with factory() as session:
        rows = (
            await session.execute(select(LoginSecurityState))
        ).scalars().all()
        assert rows
    await engine.dispose()


@pytest.mark.asyncio
async def test_successful_login_clears_principal_throttle(tmp_path):
    engine, factory = await create_database(tmp_path, "success-reset.db")
    _, username = await create_admin(factory, 3)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=("192.0.2.5", 50000)),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        for _ in range(2):
            failed = await client.post(
                "/login",
                data={"username": username, "password": "wrong-password"},
            )
            assert failed.status_code == 401
        success = await client.post(
            "/login",
            data={
                "username": username.lower(),
                "password": "StrongPassword2026!",
            },
        )
        assert success.status_code == 303
        assert "dlc_admin_session" in client.cookies

    async with factory() as session:
        rows = (
            await session.execute(select(LoginSecurityState))
        ).scalars().all()
        # Aggregate address protection is intentionally retained; account and
        # account+address states are cleared after a valid login.
        assert len(rows) == 1
    await engine.dispose()


@pytest.mark.asyncio
async def test_logout_revokes_token_server_side(tmp_path):
    engine, factory = await create_database(tmp_path, "logout-revocation.db")
    user_id, username = await create_admin(factory, 4)
    app = build_app(factory)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        login = await client.post(
            "/login",
            data={
                "username": username,
                "password": "StrongPassword2026!",
            },
        )
        assert login.status_code == 303
        old_token = client.cookies["dlc_admin_session"]
        active = await client.get("/auth/session")
        assert active.status_code == 200

        logged_out = await client.post("/logout")
        assert logged_out.status_code == 303
        assert "dlc_admin_session" not in client.cookies

    async with factory() as session:
        revoked = (
            await session.execute(select(RevokedAccessToken))
        ).scalar_one()
        assert revoked.user_id == user_id
        assert revoked.reason == "logout"
        assert old_token not in revoked.token_hash
        assert len(revoked.token_hash) == 64

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        cookies={"dlc_admin_session": old_token},
    ) as replay_client:
        replay = await replay_client.get("/auth/session")
        assert replay.status_code == 401

    await engine.dispose()


@pytest.mark.asyncio
async def test_cleanup_removes_only_expired_revocations(tmp_path):
    engine, factory = await create_database(tmp_path, "revocation-cleanup.db")
    async with factory() as session:
        session.add_all(
            [
                RevokedAccessToken(
                    token_hash="a" * 64,
                    expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
                    reason="test",
                ),
                RevokedAccessToken(
                    token_hash="b" * 64,
                    expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
                    reason="test",
                ),
            ]
        )
        await session.commit()
        removed = await cleanup_revoked_tokens(session)
        await session.commit()
        assert removed == 1
        remaining = (
            await session.execute(select(RevokedAccessToken.token_hash))
        ).scalars().all()
        assert remaining == ["b" * 64]
    await engine.dispose()
