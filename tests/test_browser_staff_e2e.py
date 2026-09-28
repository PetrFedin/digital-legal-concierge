from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import func, select

from app.config import settings
from app.db.session import AsyncSessionLocal
from app.domain.calculator.rule_revision_service import rule_payload_sha256
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.payments.bank_requisites import (
    GAMZA_COLLEGIUM_REQUISITES,
    bank_requisites_snapshot,
)
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
from app.models.calculation_rule_revision import CalculationRuleRevision
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User
from app.security.access_control import (
    ROLE_ADMIN,
    ROLE_LAWYER,
    ROLE_SUPERADMIN,
    create_access_token,
    hash_password,
)


BASE_URL = os.environ.get("BROWSER_E2E_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_USERNAME = "browser-e2e-admin"
ADMIN_PASSWORD = "Browser-E2E-Admin-2026!"
ADMIN_EMAIL = "browser-e2e-admin@example.test"
LAWYER_USERNAME = "browser-e2e-lawyer"
LAWYER_PASSWORD = "Browser-E2E-Lawyer-2026!"
LAWYER_EMAIL = "browser-e2e-lawyer@example.test"
SUPERADMIN_USERNAME = "browser-e2e-superadmin"
SUPERADMIN_EMAIL = "browser-e2e-superadmin@example.test"

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

        superadmin = await db.scalar(
            select(AdminUser).where(AdminUser.username == SUPERADMIN_USERNAME)
        )
        if superadmin is None:
            superadmin = AdminUser(
                full_name="Browser E2E Superadmin",
                username=SUPERADMIN_USERNAME,
                email=SUPERADMIN_EMAIL,
                password_hash=hash_password("Browser-E2E-Superadmin-2026!"),
                role=f"{ROLE_SUPERADMIN},{ROLE_ADMIN}",
                is_active=True,
                mfa_enabled=True,
                mfa_confirmed_at=datetime.now(timezone.utc),
                session_version=1,
            )
            db.add(superadmin)
        else:
            superadmin.email = SUPERADMIN_EMAIL
            superadmin.role = f"{ROLE_SUPERADMIN},{ROLE_ADMIN}"
            superadmin.is_active = True
            superadmin.mfa_enabled = True
            superadmin.mfa_confirmed_at = datetime.now(timezone.utc)

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

        browser_rule = await db.scalar(
            select(CalculationRuleRevision).where(
                CalculationRuleRevision.revision_key == "BROWSER-PM016-DRAFT"
            )
        )
        browser_rules = {"schema_version": 2, "sources": {}}
        if browser_rule is None:
            browser_rule = CalculationRuleRevision(
                revision_key="BROWSER-PM016-DRAFT",
                status="DRAFT",
                effective_from=date(2199, 1, 1),
                effective_to=date(2199, 12, 31),
                rules=browser_rules,
                rules_sha256=rule_payload_sha256(browser_rules),
                note="Browser E2E guided rule editor fixture",
            )
            db.add(browser_rule)
        else:
            browser_rule.status = "DRAFT"
            browser_rule.effective_from = date(2199, 1, 1)
            browser_rule.effective_to = date(2199, 12, 31)
            browser_rule.rules = browser_rules
            browser_rule.rules_sha256 = rule_payload_sha256(browser_rules)
            browser_rule.note = "Browser E2E guided rule editor fixture"

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


async def _seed_self_filing_staff_case() -> tuple[int, int]:
    async with AsyncSessionLocal() as db:
        now = datetime.now(timezone.utc)
        lawyer = await db.scalar(select(Lawyer).where(Lawyer.email == LAWYER_EMAIL))
        assert lawyer is not None

        user = User(
            telegram_id=8_910_000_000_000 + (uuid.uuid4().int % 1_000_000_000),
            full_name="Browser Self Filing Client",
            email="browser-self-filing@example.test",
        )
        db.add(user)
        await db.flush()

        case = Case(
            case_number=f"BROWSER-SELF-{uuid.uuid4().hex[:16]}",
            client_id=int(user.id),
            route="M1",
            service_mode=M1ServiceMode.SELF_FILING_PACKAGE.value,
            status=CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
            title="Пакет для самостоятельной подачи",
            assigned_lawyer_id=int(lawyer.id),
            assigned_at=now - timedelta(hours=2),
        )
        db.add(case)
        await db.flush()

        package = SelfFilingPackage(
            case_id=int(case.id),
            status="PAYMENT_PENDING",
            version=5,
            client_region="Тверская область",
            client_address="г. Тверь, тестовый адрес",
            delivery_email=user.email,
            email_confirmed_at=now - timedelta(hours=3),
            documents_complete_at=now - timedelta(hours=2),
            documents_complete_by_lawyer_id=int(lawyer.id),
            court_name="Тестовый районный суд",
            court_address="г. Тверь, адрес суда",
            jurisdiction_basis="CLIENT_RESIDENCE_OR_STAY",
            jurisdiction_note="Подсудность подтверждена для browser E2E",
            jurisdiction_confirmed_at=now - timedelta(hours=2),
            jurisdiction_confirmed_by_lawyer_id=int(lawyer.id),
            transfer_act_signed=False,
            transfer_act_confirmed_at=now - timedelta(hours=2),
            transfer_act_confirmed_by_lawyer_id=int(lawyer.id),
        )
        payment = Payment(
            case_id=int(case.id),
            payment_code=PaymentCode.M1_SELF_FILING_PACKAGE,
            title="Подготовка пакета документов для самостоятельной подачи",
            amount=Decimal("15000.00"),
            currency="RUB",
            status=PaymentStatus.PENDING,
            provider="bank_transfer",
            payment_purpose=GAMZA_COLLEGIUM_REQUISITES.mandatory_purpose,
            payment_details_snapshot=bank_requisites_snapshot(),
        )
        db.add_all([package, payment])
        await db.commit()
        return int(case.id), int(payment.id)


@pytest.fixture(scope="module", autouse=True)
def browser_staff_seed() -> None:
    asyncio.run(_upsert_staff())


def _login(page: Page, username: str, password: str) -> None:
    page.goto(f"{BASE_URL}/login", wait_until="domcontentloaded")
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(password)
    page.get_by_role("button", name="Войти в кабинет").click()
    page.wait_for_url(f"{BASE_URL}/operator")
    expect(page.get_by_role("heading", name="⚖ Рабочий кабинет")).to_be_visible()


async def _superadmin_session_token() -> str:
    async with AsyncSessionLocal() as db:
        account = await db.scalar(
            select(AdminUser).where(AdminUser.username == SUPERADMIN_USERNAME)
        )
        assert account is not None
        return create_access_token(
            int(account.id),
            account.username,
            account.role,
            session_version=int(account.session_version or 1),
            mfa_verified=True,
        )


def _add_superadmin_session(context, token: str) -> None:  # noqa: ANN001
    context.add_cookies(
        [
            {
                "name": settings.admin_session_cookie,
                "value": token,
                "url": BASE_URL,
                "httpOnly": True,
                "sameSite": "Strict",
            }
        ]
    )


def _assert_no_horizontal_overflow(page: Page) -> None:
    fits = page.evaluate(
        """() => document.documentElement.scrollWidth <=
        document.documentElement.clientWidth + 1"""
    )
    assert fits, (
        f"Horizontal overflow on {page.url}: "
        + str(
            page.evaluate(
                """() => ({
                    scrollWidth: document.documentElement.scrollWidth,
                    clientWidth: document.documentElement.clientWidth
                })"""
            )
        )
    )


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
        expect(page).to_have_title("Рабочий кабинет — дела клиентов")
        expect(page.get_by_role("heading", name="⚖ Дела клиентов")).to_be_visible()

        _assert_html_surface(page, "/calculator-builder/ui")
        expect(
            page.get_by_role("heading", name="⚖️ Редактор юридических правил расчёта")
        ).to_be_visible()
        expect(page.get_by_text("BROWSER-PM016-DRAFT")).to_be_visible()
        expect(page.get_by_text("Код формулы")).to_be_visible()
        expect(page.get_by_role("button", name="+ Добавить источник")).to_be_visible()

        formula_code = page.get_by_label("Код формулы")
        formula_code.fill("ddu_delay_penalty_v2")
        formula_json = page.locator('textarea[name="formula_json"]').input_value()
        assert '"code": "ddu_delay_penalty_v2"' in formula_json
        assert '"delay_start_offset_days": null' in formula_json

        page.get_by_role("button", name="+ Добавить источник").click()
        expect(page.get_by_label("ID источника")).to_be_visible()
        expect(page.get_by_label("Точное основание: статья / пункт / раздел / таблица")).to_be_visible()

        page.goto(f"{BASE_URL}/operator", wait_until="domcontentloaded")
        expect(page.get_by_role("link", name="Расписание консультаций")).to_be_visible()
        assert page.get_by_role("link", name="SLA и просрочки").count() == 1
        assert page.get_by_role("link", name="Telegram-доставка").count() == 1

        # Existing daily admin work surfaces must render through the same
        # authenticated browser session; no query token is supplied.
        for path in (
            "/admin/payment-reviews/ui",
            "/admin/refunds/ui",
            "/document-access/review/ui",
            "/admin/sla/ui",
            "/admin/consultation-outcomes/ui",
            "/admin/notification-delivery/ui",
            "/diagnostic-center/ui",
            "/health-center/ui",
            "/settings-ui",
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
        _assert_html_surface(page, "/calculator-builder/ui")
        expect(page.get_by_role("button", name="Проверить контрольные примеры")).to_be_visible()
        expect(page.get_by_role("button", name="Юридически подтвердить SHA")).to_be_visible()
        assert page.get_by_role("button", name="Сохранить DRAFT").count() == 0

        # Lawyer is a staff user, but not an admin. The canonical Workdesk must
        # redirect away rather than relying on a hidden earlier guard route.
        page.goto(f"{BASE_URL}/admin/workdesk/ui", wait_until="domcontentloaded")
        assert page.url != f"{BASE_URL}/admin/workdesk/ui"
        assert page.url.startswith((f"{BASE_URL}/admin-ui", f"{BASE_URL}/operator"))

        context.close()
        browser.close()


def test_staff_landing_and_primary_surfaces_are_mobile_safe() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        admin_context = browser.new_context(viewport={"width": 390, "height": 844})
        admin_page = admin_context.new_page()
        _login(admin_page, ADMIN_USERNAME, ADMIN_PASSWORD)
        for path in (
            "/operator",
            "/admin/workdesk/ui",
            "/consultation-slots/ui",
            "/admin/payment-reviews/ui",
            "/admin/refunds/ui",
            "/admin/sla/ui",
            "/admin/consultation-outcomes/ui",
            "/calculator-builder/ui",
        ):
            _assert_html_surface(admin_page, path)
            _assert_no_horizontal_overflow(admin_page)
        admin_context.close()

        lawyer_context = browser.new_context(viewport={"width": 390, "height": 844})
        lawyer_page = lawyer_context.new_page()
        _login(lawyer_page, LAWYER_USERNAME, LAWYER_PASSWORD)
        for path in (
            "/operator",
            "/lawyer/workspace/ui",
            "/lawyer/consultation-desk/ui",
            "/message-center/ui",
            "/document-access/review/ui",
        ):
            _assert_html_surface(lawyer_page, path)
            _assert_no_horizontal_overflow(lawyer_page)
        lawyer_context.close()

        browser.close()


def test_superadmin_landing_has_distinct_leadership_workspace() -> None:
    # Generate the signed session before entering Playwright's synchronous
    # event-loop bridge; asyncio.run() cannot be nested inside sync_playwright().
    token = asyncio.run(_superadmin_session_token())

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 390, "height": 844})
        _add_superadmin_session(context, token)
        page = context.new_page()

        _assert_html_surface(page, "/operator")
        expect(page.locator("#roleName")).to_have_text("Суперадминистратор")
        expect(
            page.get_by_role("heading", name="Руководство и контроль")
        ).to_be_visible()
        for name in (
            "Доступ сотрудников",
            "Безопасность",
            "Аудит",
            "Резервные копии",
            "Хранение данных",
        ):
            expect(page.get_by_role("link", name=name)).to_be_visible()
        expect(page.locator("body")).not_to_contain_text(
            "Администратор · Суперадминистратор"
        )
        _assert_no_horizontal_overflow(page)

        # Leadership links are not decorative: the same MFA-verified personal
        # session must be accepted by the protected supervisory surfaces.
        for path in (
            "/access/ui",
            "/security-events/ui",
            "/audit-center/ui",
            "/backup-center/ui",
            "/retention/ui",
        ):
            _assert_html_surface(page, path)
            _assert_no_horizontal_overflow(page)

        context.close()
        browser.close()


def test_self_filing_staff_card_is_role_safe_and_shows_frozen_bank_contract() -> None:
    case_id, payment_id = asyncio.run(_seed_self_filing_staff_case())
    self_filing_url = f"{BASE_URL}/self-filing/ui?case_id={case_id}"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        admin_context = browser.new_context(viewport={"width": 390, "height": 844})
        admin_page = admin_context.new_page()
        _login(admin_page, ADMIN_USERNAME, ADMIN_PASSWORD)
        response = admin_page.goto(self_filing_url, wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(
            admin_page.get_by_role("heading", name="📄 Пакет для самостоятельной подачи")
        ).to_be_visible()
        expect(
            admin_page.get_by_text("Адыгейская Республиканская Коллегия Адвокатов")
        ).to_be_visible()
        expect(admin_page.get_by_text("для адвоката Гамза Д.Г.")).to_be_visible()
        expect(admin_page.get_by_text(f"Платёж #{payment_id}", exact=False)).to_be_visible()
        expect(admin_page.get_by_role("button", name="Обновить")).to_be_enabled()
        expect(admin_page.get_by_role("button", name="Начать проверку")).to_be_disabled()
        expect(admin_page.get_by_role("button", name="Запросить документы")).to_be_disabled()
        expect(
            admin_page.get_by_role("button", name="Подтвердить комплект и открыть 15 000 ₽")
        ).to_be_disabled()
        _assert_no_horizontal_overflow(admin_page)

        # The generic case card uses the same server authority and exposes a
        # bank-reconciliation action rather than a second payment implementation.
        case_response = admin_page.goto(
            f"{BASE_URL}/admin/cases/{case_id}/ui",
            wait_until="domcontentloaded",
        )
        assert case_response is not None and case_response.status == 200
        expect(admin_page.get_by_role("button", name="Подтвердить поступление")).to_be_visible()
        _assert_no_horizontal_overflow(admin_page)
        admin_context.close()

        lawyer_context = browser.new_context(viewport={"width": 390, "height": 844})
        lawyer_page = lawyer_context.new_page()
        _login(lawyer_page, LAWYER_USERNAME, LAWYER_PASSWORD)
        response = lawyer_page.goto(self_filing_url, wait_until="domcontentloaded")
        assert response is not None and response.status == 200
        expect(lawyer_page.get_by_text("для адвоката Гамза Д.Г.")).to_be_visible()
        # The responsible lawyer can mutate the Case in principle, but stale
        # review/approval controls must still be disabled once payment is pending.
        expect(lawyer_page.get_by_role("button", name="Начать проверку")).to_be_disabled()
        expect(lawyer_page.get_by_role("button", name="Запросить документы")).to_be_disabled()
        expect(
            lawyer_page.get_by_role("button", name="Подтвердить комплект и открыть 15 000 ₽")
        ).to_be_disabled()
        expect(lawyer_page.get_by_role("button", name="Обновить")).to_be_enabled()
        _assert_no_horizontal_overflow(lawyer_page)
        lawyer_context.close()

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

        # The active deep-link already explains why this exact payment entered
        # review, but never renders the free-text AuditLog comment.
        winner_history = winner.locator("#paymentReviewHistory")
        expect(winner_history).to_be_visible()
        expect(winner_history).to_contain_text("Сверка создана")
        expect(winner_history).to_contain_text("Browser stale Payment Review verification")
        expect(winner_history).not_to_contain_text("Payment held for administrator review")

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
        expect(winner_history).to_contain_text("Сверка завершена")
        expect(winner_history).to_contain_text("После: PAID")
        expect(winner_history).to_contain_text(
            "Решение: подтвердить связь с бронью"
        )
        expect(winner_history).not_to_contain_text(
            "Подтверждена текущая оплаченная бронь"
        )

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

        # Conflict recovery reloads server truth, including the terminal history,
        # but must not expose either administrator's reconciliation comment.
        stale_history = stale.locator("#paymentReviewHistory")
        expect(stale_history).to_be_visible()
        expect(stale_history).to_contain_text("Сверка завершена")
        expect(stale_history).to_contain_text("После: PAID")
        expect(stale_history).to_contain_text(
            "Решение: подтвердить связь с бронью"
        )
        expect(stale_history).not_to_contain_text(
            "Подтверждена текущая оплаченная бронь"
        )
        expect(stale_history).not_to_contain_text(
            "Старая вкладка пытается вернуть платёж"
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
