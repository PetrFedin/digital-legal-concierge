from __future__ import annotations

import asyncio
import os

import pytest
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models.admin_user import AdminUser
from app.models.lawyer import Lawyer
from app.security.access_control import ROLE_ADMIN, ROLE_LAWYER, hash_password


BASE_URL = os.environ.get("BROWSER_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_USERNAME = "browser-e2e-admin"
ADMIN_PASSWORD = "Browser-E2E-Admin-2026!"
ADMIN_EMAIL = "browser-e2e-admin@example.test"
LAWYER_USERNAME = "browser-e2e-lawyer"
LAWYER_PASSWORD = "Browser-E2E-Lawyer-2026!"
LAWYER_EMAIL = "browser-e2e-lawyer@example.test"

pytestmark = pytest.mark.skipif(
    os.environ.get("BROWSER_E2E") != "1",
    reason="Real browser contract requires the dedicated browser E2E runtime",
)


async def _upsert_staff() -> None:
    async with AsyncSessionLocal() as db:
        admin = await db.scalar(
            select(AdminUser).where(AdminUser.username == ADMIN_USERNAME)
        )
        if admin is None:
            admin = AdminUser(
                full_name="Browser E2E Admin",
                username=ADMIN_USERNAME,
                email=ADMIN_EMAIL,
                password_hash=hash_password(ADMIN_PASSWORD),
                role=ROLE_ADMIN,
                is_active=True,
                mfa_enabled=False,
                session_version=1,
            )
            db.add(admin)
        else:
            admin.email = ADMIN_EMAIL
            admin.password_hash = hash_password(ADMIN_PASSWORD)
            admin.role = ROLE_ADMIN
            admin.is_active = True
            admin.mfa_enabled = False

        lawyer_account = await db.scalar(
            select(AdminUser).where(AdminUser.username == LAWYER_USERNAME)
        )
        if lawyer_account is None:
            lawyer_account = AdminUser(
                full_name="Browser E2E Lawyer",
                username=LAWYER_USERNAME,
                email=LAWYER_EMAIL,
                password_hash=hash_password(LAWYER_PASSWORD),
                role=ROLE_LAWYER,
                is_active=True,
                mfa_enabled=False,
                session_version=1,
            )
            db.add(lawyer_account)
        else:
            lawyer_account.email = LAWYER_EMAIL
            lawyer_account.password_hash = hash_password(LAWYER_PASSWORD)
            lawyer_account.role = ROLE_LAWYER
            lawyer_account.is_active = True
            lawyer_account.mfa_enabled = False

        lawyer = await db.scalar(select(Lawyer).where(Lawyer.email == LAWYER_EMAIL))
        if lawyer is None:
            lawyer = Lawyer(
                full_name="Browser E2E Lawyer",
                email=LAWYER_EMAIL,
                is_active=True,
            )
            db.add(lawyer)
        else:
            lawyer.full_name = "Browser E2E Lawyer"
            lawyer.is_active = True

        await db.commit()


@pytest.fixture(scope="module", autouse=True)
def browser_staff_seed() -> None:
    asyncio.run(_upsert_staff())


def _login(page: Page, username: str, password: str) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.get_by_role("button", name="Войти в кабинет").click()
    page.wait_for_url(f"{BASE_URL}/operator")
    expect(page.get_by_role("heading", name="Digital Legal Concierge")).to_be_visible()


def _assert_html_surface(page: Page, path: str) -> None:
    response = page.goto(f"{BASE_URL}{path}", wait_until="domcontentloaded")
    assert response is not None
    assert response.status == 200
    assert page.url.startswith(f"{BASE_URL}{path}")


def test_admin_browser_login_staff_surfaces_and_logout_revoke() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        # Anonymous navigation must never reveal the daily staff Workdesk.
        page.goto(f"{BASE_URL}/admin/workdesk/ui", wait_until="domcontentloaded")
        assert page.url.startswith(f"{BASE_URL}/login")

        _login(page, ADMIN_USERNAME, ADMIN_PASSWORD)

        cookies = context.cookies(BASE_URL)
        session_cookie = next(
            (cookie for cookie in cookies if cookie["name"] == "dlc_admin_session"),
            None,
        )
        assert session_cookie is not None
        assert session_cookie["httpOnly"] is True

        session = page.evaluate(
            """async () => {
                const response = await fetch('/auth/session', {
                    credentials: 'same-origin', cache: 'no-store'
                });
                return {status: response.status, body: await response.json()};
            }"""
        )
        assert session["status"] == 200
        assert session["body"]["session_transport"] == "httponly_cookie"
        assert session["body"]["api_token"]
        assert session["body"]["api_token"] != session_cookie["value"]

        _assert_html_surface(page, "/admin/workdesk/ui")
        expect(page).to_have_title("Digital Legal Concierge — рабочий стол")
        expect(page.get_by_role("heading", name="⚖ Единый рабочий стол")).to_be_visible()

        # Existing daily admin work surfaces must render through the same
        # authenticated browser session; no query token is supplied.
        for path in (
            "/admin/payment-reviews/ui",
            "/admin/refunds/ui",
            "/document-access/review/ui",
            "/admin/sla/ui",
            "/admin/consultation-outcomes/ui",
        ):
            _assert_html_surface(page, path)

        # Logout revokes the session token, not merely the browser cookie.
        page.goto(f"{BASE_URL}/operator", wait_until="domcontentloaded")
        page.get_by_role("button", name="Выйти").click()
        page.wait_for_url(f"{BASE_URL}/login")
        page.goto(f"{BASE_URL}/admin/workdesk/ui", wait_until="domcontentloaded")
        assert page.url.startswith(f"{BASE_URL}/login")

        context.close()
        browser.close()


def test_lawyer_browser_is_role_scoped_and_cannot_enter_admin_workdesk() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        _login(page, LAWYER_USERNAME, LAWYER_PASSWORD)

        _assert_html_surface(page, "/lawyer/workspace/ui")
        _assert_html_surface(page, "/lawyer/consultation-desk/ui")
        _assert_html_surface(page, "/message-center/ui")
        _assert_html_surface(page, "/document-access/review/ui")

        # Lawyer is a staff user, but not an admin. The canonical Workdesk must
        # redirect away rather than relying on a hidden earlier guard route.
        page.goto(f"{BASE_URL}/admin/workdesk/ui", wait_until="domcontentloaded")
        assert page.url != f"{BASE_URL}/admin/workdesk/ui"
        assert page.url.startswith((f"{BASE_URL}/admin-ui", f"{BASE_URL}/operator"))

        context.close()
        browser.close()
