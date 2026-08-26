from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import func, select

from app.db.session import AsyncSessionLocal
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.consultation import Consultation
from app.models.consultation_slot import ConsultationSlot
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
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


async def _seed_payment_review_conflict() -> tuple[int, int, int, int]:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=8_900_000_000_000 + (uuid.uuid4().int % 1_000_000_000),
            full_name="Browser Payment Review Client",
        )
        lawyer = Lawyer(
            full_name="Browser Payment Review Lawyer",
            email=f"browser-review-{uuid.uuid4().hex}@example.test",
            is_active=True,
        )
        db.add_all([user, lawyer])
        await db.flush()

        case = Case(
            case_number=f"BROWSER-REVIEW-{uuid.uuid4().hex[:16]}",
            client_id=user.id,
            route="M2",
            status=CaseStatus.M2_CONSULTATION_BOOKED,
            title="Browser Payment Review stale conflict",
        )
        db.add(case)
        await db.flush()

        consultation = Consultation(
            case_id=case.id,
            lawyer_id=lawyer.id,
            status=ConsultationStatus.BOOKED,
            consultation_type="online",
            subject_type="new_or_other",
            client_description="Browser stale Payment Review verification",
        )
        db.add(consultation)
        await db.flush()

        starts_at = now + timedelta(days=2)
        slot = ConsultationSlot(
            lawyer_id=lawyer.id,
            starts_at=starts_at,
            ends_at=starts_at + timedelta(hours=1),
            status="booked",
            held_by_user_id=user.id,
            consultation_id=consultation.id,
        )
        db.add(slot)
        await db.flush()
        consultation.slot_id = slot.id
        consultation.scheduled_at = starts_at

        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Оплата консультации — browser review",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PAID_REVIEW,
            provider="browser-e2e",
            provider_payment_id=f"browser-review-{uuid.uuid4().hex}",
            reservation_key=PaymentService.consultation_reservation_key(
                consultation.id,
                slot.id,
            ),
            paid_at=now - timedelta(minutes=1),
        )
        db.add(payment)
        await db.flush()

        db.add(
            AuditLog(
                actor_type="system",
                actor_id=None,
                action="CONSULTATION_PAYMENT_REVIEW_REQUIRED",
                entity_type="case",
                entity_id=case.id,
                old_value={"status": PaymentStatus.WAITING_CONFIRMATION.value},
                new_value={
                    "payment_id": payment.id,
                    "reason": "Browser stale Payment Review verification",
                },
                comment="Payment held for administrator review",
            )
        )
        result = int(case.id), int(consultation.id), int(slot.id), int(payment.id)
        await db.commit()
        return result


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


def _accept_review_dialogs(page: Page, *, comment: str) -> None:
    def handle(dialog) -> None:  # noqa: ANN001
        if dialog.type == "prompt":
            dialog.accept(comment)
        else:
            dialog.accept()

    page.on("dialog", handle)


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


def test_payment_review_stale_tab_gets_409_server_truth_without_overwrite() -> None:
    case_id, consultation_id, slot_id, payment_id = asyncio.run(
        _seed_payment_review_conflict()
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context()
        winner = context.new_page()
        stale = context.new_page()
        _login(winner, ADMIN_USERNAME, ADMIN_PASSWORD)

        review_url = (
            f"{BASE_URL}/admin/payment-reviews/ui"
            f"?payment_id={payment_id}&case_id={case_id}"
        )
        winner.goto(review_url, wait_until="domcontentloaded")
        stale.goto(review_url, wait_until="domcontentloaded")

        winner_confirm = winner.get_by_role(
            "button",
            name="Подтвердить связь с текущей бронью",
        )
        stale_refund = stale.get_by_role(
            "button",
            name="Вернуть этот платёж, запись сохранить",
        )
        expect(winner_confirm).to_be_visible()
        expect(stale_refund).to_be_visible()

        _accept_review_dialogs(
            winner,
            comment="Подтверждена текущая оплаченная бронь",
        )
        winner_confirm.click()
        expect(
            winner.get_by_text(
                f"Платёж #{payment_id} больше не требует сверки.",
                exact=False,
            )
        ).to_be_visible()

        # This page still has the old PAID_REVIEW card in its DOM. Its conflicting
        # refund command must not be replayed over the decision committed above.
        _accept_review_dialogs(
            stale,
            comment="Старая вкладка пытается вернуть платёж",
        )
        stale_refund.click()
        expect(
            stale.get_by_text(
                f"Платёж #{payment_id} больше не требует сверки.",
                exact=False,
            )
        ).to_be_visible()
        conflict = stale.locator("#message")
        expect(conflict).to_contain_text("решение не применено")
        expect(conflict).to_contain_text("Текущий статус оплаты: PAID")
        expect(conflict).to_contain_text(
            "На сервере уже сохранено решение: подтвердить связь с бронью"
        )
        expect(conflict).to_contain_text(
            "Ваш допустимый выбор и комментарий сохранены в этой вкладке"
        )

        context.close()
        browser.close()

    async def verify_server_truth() -> None:
        async with AsyncSessionLocal() as db:
            payment = await db.get(Payment, payment_id)
            consultation = await db.get(Consultation, consultation_id)
            slot = await db.get(ConsultationSlot, slot_id)
            assert payment is not None
            assert consultation is not None
            assert slot is not None
            assert str(payment.status) == PaymentStatus.PAID.value
            assert str(consultation.status) == ConsultationStatus.BOOKED.value
            assert int(consultation.slot_id or 0) == slot_id
            assert str(slot.status) == "booked"
            assert int(slot.consultation_id or 0) == consultation_id

            resolutions = await db.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case_id,
                    AuditLog.action == "CONSULTATION_PAYMENT_REVIEW_RESOLVED",
                )
            )
            assert int(resolutions or 0) == 1

    asyncio.run(verify_server_truth())
