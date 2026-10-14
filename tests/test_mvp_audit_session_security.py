"""Audit Center must obey real personal MFA revocation and role changes."""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import audit_center
from app.config import settings
from app.models import Base
from app.models.admin_user import AdminUser
from app.security.access_control import create_access_token
from app.security.token_revocation import revoke_token


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'audit-session-security.db'}"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def create_actor(factory):
    suffix = uuid4().hex
    async with factory() as db:
        account = AdminUser(
            full_name="Synthetic named MFA owner",
            username="audit-" + suffix[:18],
            email="audit-" + suffix[:18] + "@example.invalid",
            password_hash="test-not-a-real-password",
            role="superadmin,admin",
            is_active=True,
            mfa_enabled=True,
            mfa_confirmed_at=datetime.now(timezone.utc),
            session_version=2,
        )
        db.add(account)
        await db.flush()
        uid, username = account.id, account.username
        await db.commit()
    token = create_access_token(
        uid, username, ["superadmin", "admin"],
        session_version=2, mfa_verified=True,
    )
    return uid, token


@pytest.mark.asyncio
async def test_personal_mfa_superadmin_can_read_audit_chain(factory):
    uid, token = await create_actor(factory)
    async with factory() as db:
        auth = await audit_center.require_audit_superadmin(db, token)
        assert auth["uid"] == uid
        assert auth["mfa"] is True
        result = await audit_center.audit_integrity_status(
            db=db, x_admin_token=token,
        )
        assert result["ok"] is True
        status = await audit_center.audit_center_status(
            db=db, x_admin_token=token,
        )
        assert status["count"] == 0
        assert status["integrity"]["ok"] is True


@pytest.mark.asyncio
async def test_logout_revokes_audit_center_token_immediately(factory):
    _uid, token = await create_actor(factory)
    async with factory() as db:
        assert await audit_center.require_audit_superadmin(db, token)
        assert await revoke_token(db, token, reason="logout") is True
        await db.commit()

    async with factory() as db:
        with pytest.raises(HTTPException) as denied_status:
            await audit_center.audit_center_status(
                db=db, x_admin_token=token,
            )
        assert denied_status.value.status_code == 401
        with pytest.raises(HTTPException) as denied_integrity:
            await audit_center.audit_integrity_status(
                db=db, x_admin_token=token,
            )
        assert denied_integrity.value.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation", ("inactive", "mfa_disabled", "demoted", "new_roles", "rotated"),
)
async def test_account_state_is_rechecked_for_every_audit_request(
    factory, mutation,
):
    uid, token = await create_actor(factory)
    async with factory() as db:
        account = await db.get(AdminUser, uid)
        if mutation == "inactive":
            account.is_active = False
        elif mutation == "mfa_disabled":
            account.mfa_enabled = False
        elif mutation == "demoted":
            account.role = "admin"
        elif mutation == "new_roles":
            account.role = "superadmin,admin,operator"
        elif mutation == "rotated":
            account.session_version += 1
        await db.commit()

    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await audit_center.require_audit_superadmin(db, token)
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_legacy_api_token_is_not_personal_mfa_session(factory, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "admin_api_token", "synthetic-nonpersonal-legacy-token")
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await audit_center.require_audit_superadmin(
                db, "synthetic-nonpersonal-legacy-token",
            )
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_signed_superadmin_token_without_verified_mfa_is_denied(factory):
    uid, _token = await create_actor(factory)
    no_mfa = create_access_token(
        uid, "synthetic", ["superadmin", "admin"],
        session_version=2, mfa_verified=False,
    )
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await audit_center.require_audit_superadmin(db, no_mfa)
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_token_with_missing_real_person_is_denied(factory):
    token = create_access_token(
        987654321, "absent-admin", ["superadmin", "admin"],
        session_version=1, mfa_verified=True,
    )
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await audit_center.require_audit_superadmin(db, token)
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_audit_requires_signed_session_identifier(factory, monkeypatch):
    _uid, token = await create_actor(factory)
    from app.security.access_control import decode_access_token
    payload = decode_access_token(token)
    assert payload and payload["jti"]
    payload.pop("jti")
    monkeypatch.setattr(audit_center, "decode_access_token", lambda _token: payload)
    async with factory() as db:
        with pytest.raises(HTTPException) as denied:
            await audit_center.require_audit_superadmin(db, token)
        assert denied.value.status_code == 403
