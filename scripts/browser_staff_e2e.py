"""Real Chromium smoke for admin and lawyer surfaces on disposable CI data.

Run against a separately started test-mode FastAPI process. No provider API,
Telegram token, production database or real-client data is required.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.payments.payment_types import PaymentCode
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import hash_password


BASE_URL = os.getenv("BROWSER_E2E_BASE_URL", "http://127.0.0.1:18101")
PASSWORD = "Browser-Staff-CI-Only-2026!"


async def seed_staff() -> tuple[str, str, int, str, int]:
    async with AsyncSessionLocal() as db:
        existing = (
            await db.execute(
                select(AdminUser).where(AdminUser.username == "browser-admin")
            )
        ).scalar_one_or_none()
        if existing:
            raise RuntimeError("Browser test database was not isolated")

        db.add_all([
            AdminUser(
                full_name="Synthetic Browser Admin",
                username="browser-admin",
                email="browser-admin@example.test",
                password_hash=hash_password(PASSWORD),
                role="admin",
                is_active=True,
            ),
            AdminUser(
                full_name="Synthetic Browser Lawyer",
                username="browser-lawyer",
                email="browser-lawyer@example.test",
                password_hash=hash_password(PASSWORD),
                role="lawyer",
                is_active=True,
            ),
        ])
        lawyer = Lawyer(
            full_name="Synthetic Browser Lawyer",
            email="browser-lawyer@example.test",
            is_active=True,
            workload_limit=10,
        )
        other_lawyer = Lawyer(
            full_name="Other Lawyer",
            email="browser-other-lawyer@example.test",
            is_active=True,
            workload_limit=10,
        )
        client = User(
            telegram_id=988800011, full_name="Synthetic browser case owner"
        )
        db.add_all([lawyer, other_lawyer, client])
        await db.flush()

        own_case = Case(
            case_number="BROWSER-OWN-2026",
            client_id=client.id,
            assigned_lawyer_id=lawyer.id,
            route="M1",
            status=CaseStatus.M1_LAWYER_REVIEW,
            title="Browser staff own case",
            next_action="Review synthetic documents",
        )
        foreign_case = Case(
            case_number="BROWSER-FOREIGN-2026",
            client_id=client.id,
            assigned_lawyer_id=other_lawyer.id,
            route="M1",
            status=CaseStatus.M1_LAWYER_REVIEW,
            title="Browser staff foreign case",
            next_action="Other lawyer must handle this case",
        )
        receipt_case = Case(
            case_number="BROWSER-M1-RECEIPT-2026",
            client_id=client.id,
            assigned_lawyer_id=lawyer.id,
            route="M1",
            status=CaseStatus.M1_WAITING_PAYMENT_30000,
            title="Synthetic M1 payment and POA handoff",
            contract_signed_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add_all([own_case, foreign_case, receipt_case])
        await db.flush()
        invoice = Payment(
            case_id=receipt_case.id,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            title="Первый платёж по тестовому делу",
            amount=Decimal("30000.00"),
            status="PENDING",
        )
        db.add(invoice)
        await db.flush()
        foreign_id = foreign_case.id
        await db.commit()
        return (
            own_case.case_number, foreign_case.case_number, foreign_id,
            receipt_case.case_number, invoice.id,
        )


def browser_login(page, username: str) -> None:
    page.goto(BASE_URL + "/login", wait_until="domcontentloaded")
    expect(page.locator('input[name="username"]')).to_be_visible()
    page.locator('input[name="username"]').fill(username)
    page.locator('input[name="password"]').fill(PASSWORD)
    page.get_by_role("button", name="Войти в кабинет").click()
    page.wait_for_url("**/admin-ui", timeout=15000)


def session_api_status(page, path: str, *, method="GET", body=None) -> int:
    return page.evaluate(
        """async ([url, method, data]) => {
            const session = await fetch('/auth/session', {credentials:'same-origin'});
            if (!session.ok) return session.status;
            const payload = await session.json();
            const response = await fetch(url, {
                method,
                credentials:'same-origin',
                headers: {'x-admin-token': payload.api_token, 'Content-Type':'application/json'},
                body: data === null ? undefined : JSON.stringify(data),
            });
            return response.status;
        }""",
        [path, method, body],
    )


def check_staff_ui(
    own_case: str, foreign_case: str, foreign_id: int,
    receipt_case: str, payment_id: int,
) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 840}, timezone_id="UTC")
        js_errors: list[str] = []
        page.on("pageerror", lambda exc: js_errors.append(str(exc)))

        def accept_dialog(dialog):
            if dialog.type == "prompt":
                if "доверенности" in dialog.message.lower():
                    dialog.accept("Оригинал доверенности и заверенные копии проверены в тесте.")
                else:
                    dialog.accept("Пожалуйста, предоставьте уточняющие документы.")
            else:
                dialog.accept()

        page.on("dialog", accept_dialog)
        try:
            browser_login(page, "browser-admin")
            expect(page.locator("#view")).to_contain_text(
                "Что требует внимания", timeout=15000
            )
            page.locator('button[data-tab="cases"]').click()
            expect(page.locator("#view")).to_contain_text(
                own_case, timeout=10000
            )
            expect(page.locator("#view")).to_contain_text(
                foreign_case, timeout=10000
            )
            assert session_api_status(page, "/lawyer/cases") == 403

            # Real admin form, isolated synthetic receipt: no provider or bank call.
            page.locator('button[data-tab="payments"]').click()
            receipt_button = page.locator(
                f'#view [data-action="record-offline-receipt"][data-payment-id="{payment_id}"]'
            )
            expect(receipt_button).to_be_visible(timeout=10000)
            receipt_button.click()
            form = page.locator(f"#offlineReceiptForm_{payment_id}")
            expect(form).to_be_visible()
            form.locator("#receiptAmount").fill("30000.00")
            form.locator("#receiptPaidAt").fill(
                (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
            )
            form.locator("#receiptReference").fill("SYNTHETIC-BROWSER-RECEIPT-001")
            form.locator("#receiptReason").fill(
                "Синтетическое подтверждение поступления для изолированной браузерной проверки."
            )
            with page.expect_response(
                lambda response: response.url.endswith(
                    f"/admin/payments/{payment_id}/record-receipt"
                ) and response.request.method == "POST",
            ) as recorded:
                form.locator("#receiptSubmit").click()
            assert recorded.value.status == 200, recorded.value.text()
            expect(receipt_button).to_have_count(0, timeout=12000)
            expect(page.locator("#view")).to_contain_text("PAID", timeout=12000)
            expect(page.locator("#side")).to_contain_text(receipt_case, timeout=12000)

            # A separate personal lawyer account, never the admin token.
            page.context.clear_cookies()
            browser_login(page, "browser-lawyer")
            page.goto(BASE_URL + "/lawyer/ui", wait_until="domcontentloaded")
            expect(page.locator("#casesContent")).to_contain_text(
                own_case, timeout=15000
            )
            expect(page.locator("#casesContent")).not_to_contain_text(
                foreign_case
            )
            assert session_api_status(page, "/admin/cases") == 403

            forbidden = session_api_status(
                page,
                f"/lawyer/cases/{foreign_id}/request-documents",
                method="POST",
                body={"comment": "Synthetic forbidden cross-case request"},
            )
            assert forbidden == 403, f"Foreign-case write unexpectedly returned {forbidden}"

            poa_row = page.locator("#casesContent tr").filter(has_text=receipt_case)
            expect(poa_row).to_contain_text("M1_POWER_OF_ATTORNEY", timeout=12000)
            expect(poa_row.get_by_role("button", name="Претензия направлена")).to_have_count(0)
            poa_row.get_by_role("button", name="Доверенность получена").click()
            expect(poa_row).to_contain_text("M1_POA_RECEIVED", timeout=12000)
            expect(poa_row.get_by_role("button", name="Доверенность получена")).to_have_count(0)
            expect(poa_row.get_by_role("button", name="Претензия направлена")).to_be_visible()

            row = page.locator("#casesContent tr").filter(has_text=own_case)
            row.get_by_role("button", name="Запросить документы").click()
            expect(page.locator("#casesContent")).to_contain_text(
                "M1_DOCS_REQUESTED", timeout=12000
            )
            assert not js_errors, "Client-side staff UI JavaScript errors: " + "; ".join(js_errors)
            print(
                "BROWSER STAFF E2E PASS: admin queues, lawyer-scoped cases, "
                "forbidden cross-case write, permitted document request, "
                "offline M1 receipt form and assigned-lawyer POA verification"
            )
        finally:
            browser.close()


def main() -> None:
    if os.getenv("APP_ENV") != "test":
        raise SystemExit("Browser staff E2E requires APP_ENV=test")
    database_url = os.getenv("DATABASE_URL", "")
    if not database_url.startswith(
        ("sqlite+aiosqlite:///", "postgresql+asyncpg://")
    ):
        raise SystemExit(
            "Browser staff E2E requires an isolated test SQLite or PostgreSQL database"
        )
    if os.getenv("PAYMENT_PROVIDER") != "disabled":
        raise SystemExit("Browser M1 receipt E2E requires PAYMENT_PROVIDER=disabled")
    own, foreign, foreign_id, receipt_case, payment_id = asyncio.run(seed_staff())
    check_staff_ui(own, foreign, foreign_id, receipt_case, payment_id)


if __name__ == "__main__":
    main()
