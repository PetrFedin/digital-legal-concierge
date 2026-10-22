"""P0 settings boundary: no anonymous changes, typed runtime values."""
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.settings_ui import router as settings_router
from app.api.admin import router as admin_router
from app.db.session import get_db
from app.models import Base
from app.models.admin_user import AdminUser
from app.security.access_control import ROLE_ADMIN, ROLE_SUPERADMIN, create_access_token
from app.security.token_revocation import revoke_token
from app.system.settings_service import SettingsService, validate_setting_value


@pytest.mark.parametrize(
    "key,value,normalized",
    [
        ("payments.m1_initial_payment", "30000", 30000),
        ("payments.m1_court_payment", "70000.50", "70000.50"),
        ("payments.m1_success_fee_percent", "10", 10),
        ("deadlines.claim_waiting_days", "30", 30),
        ("notifications.payment_reminder_hours", "[24,72,168]", [24, 72, 168]),
        ("texts.bot_welcome", "Привет", "Привет"),
    ],
)
def test_settings_typed_normalization(key, value, normalized):
    assert validate_setting_value(key, value) == normalized


@pytest.mark.parametrize(
    "key,value",
    [
        ("unknown.injected_key", "1"),
        ("payments.m1_initial_payment", "-1"),
        ("payments.m1_initial_payment", "NaN"),
        ("payments.m1_initial_payment", "Infinity"),
        ("payments.m1_court_payment", "10.999"),
        ("payments.m1_success_fee_percent", "101"),
        ("payments.m1_success_fee_percent", "-1"),
        ("deadlines.claim_waiting_days", "0"),
        ("deadlines.claim_waiting_days", "3.5"),
        ("notifications.payment_reminder_hours", "[72,24]"),
        ("notifications.payment_reminder_hours", "[24,24]"),
        ("notifications.payment_reminder_hours", "oops"),
        ("texts.bot_welcome", ""),
    ],
)
def test_settings_invalid_values_rejected(key, value):
    with pytest.raises(ValueError):
        validate_setting_value(key, value)


@pytest.mark.asyncio
async def test_legacy_settings_reject_unauthorized_reads_and_writes(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'settings_security.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = FastAPI()
    app.include_router(settings_router)
    app.include_router(admin_router)

    async def db_override():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = db_override
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        get = await client.get("/settings-ui")
        post = await client.post(
            "/settings-ui/update",
            data={"key": "payments.m1_initial_payment", "value": "1"},
        )
        assert get.status_code == 403
        assert post.status_code == 403
        # REST requires authentication: anonymous requests use HTTP 401.
        assert (await client.get("/admin/settings")).status_code == 401
        assert (await client.post(
            "/admin/settings/payments.m1_initial_payment", json={"value": 1}
        )).status_code == 401

        async with factory() as seed_db:
            seed_db.add(AdminUser(
                id=901, full_name="Settings test admin", username="settings-review",
                email="settings-review@example.test", password_hash="not-used-for-login",
                role="admin", is_active=True, session_version=2,
            ))
            await seed_db.commit()
        token = create_access_token(
            user_id=901, username="settings-review", roles=[ROLE_ADMIN],
            session_version=2,
        )
        # Legacy form honours a personal cookie session, not a fake global token.
        client.cookies.set("dlc_admin_session", token)
        get = await client.get("/settings-ui")
        assert get.status_code == 200
        assert "30000" in get.text
        assert (await client.get(
            "/admin/settings", headers={"x-admin-token": token}
        )).status_code == 200
        assert (await client.post(
            "/admin/settings/payments.m1_initial_payment",
            headers={"x-admin-token": token}, json={"value": "-100"}
        )).status_code == 409
        wrong = await client.post(
            "/settings-ui/update",
            data={"key": "payments.m1_initial_payment", "value": "-100"},
        )
        assert wrong.status_code == 422
        unknown = await client.post(
            "/settings-ui/update",
            data={"key": "attacker.key", "value": "1"},
        )
        assert unknown.status_code == 422
        accepted = await client.post(
            "/settings-ui/update",
            data={"key": "payments.m1_initial_payment", "value": "31500"},
        )
        assert accepted.status_code == 303
    async with factory() as db:
        assert await SettingsService(db).get_value(
            "payments.m1_initial_payment"
        ) == 31500
    await engine.dispose()



@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change",
    ["revoked", "disabled", "role_demoted", "session_rotated", "superadmin_no_mfa"],
)
async def test_settings_recheck_live_account_and_revocation(tmp_path, change):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / ('settings_revocation_' + change + '.db')}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        uid = 991
        superadmin = change == "superadmin_no_mfa"
        original_role = "superadmin,admin" if superadmin else "admin"
        async with factory() as session:
            session.add(AdminUser(
                id=uid, full_name="Synthetic settings staff", username="settings-tester",
                email="settings-tester@example.test", role=original_role,
                password_hash="not-used-for-login", is_active=True,
                session_version=2, mfa_enabled=superadmin,
            ))
            await session.commit()
        roles = [ROLE_SUPERADMIN, ROLE_ADMIN] if superadmin else [ROLE_ADMIN]
        token = create_access_token(
            uid, "settings-tester", roles, session_version=2,
            mfa_verified=False,
        )
        app = FastAPI()
        app.include_router(settings_router)
        app.include_router(admin_router)

        async def db_override():
            async with factory() as session:
                yield session

        app.dependency_overrides[get_db] = db_override
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test", follow_redirects=False,
        ) as client:
            client.cookies.set("dlc_admin_session", token)
            if not superadmin:
                initial = await client.get("/settings-ui")
                assert initial.status_code == 200
            async with factory() as session:
                account = await session.get(AdminUser, uid)
                if change == "revoked":
                    assert await revoke_token(session, token, reason="logout")
                elif change == "disabled":
                    account.is_active = False
                elif change == "role_demoted":
                    account.role = "operator"
                elif change == "session_rotated":
                    account.session_version += 1
                await session.commit()
            get = await client.get("/settings-ui")
            post = await client.post(
                "/settings-ui/update",
                data={"key": "payments.m1_initial_payment", "value": "1"},
            )
            expected = 401 if change == "revoked" else 403
            assert get.status_code == expected
            assert post.status_code == expected
            assert (await client.get(
                "/admin/settings", headers={"x-admin-token": token}
            )).status_code == expected
            assert (await client.post(
                "/admin/settings/payments.m1_initial_payment",
                headers={"x-admin-token": token}, json={"value": 1}
            )).status_code == expected
        async with factory() as session:
            assert (
                await SettingsService(session).get_value("payments.m1_initial_payment")
            ) == 30000
    finally:
        await engine.dispose()
