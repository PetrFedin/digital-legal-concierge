"""Real DB/API checks for evidenced offline M1 payments; no fake provider events."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.admin import router as admin_router
from app.api.web_admin import router as web_admin_router
from app.config import settings
from app.db.session import get_db
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments import payment_service
from app.domain.payments.m1_offline_receipt_service import M1OfflineReceiptService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_chain_head import AuditChainHead
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import create_access_token
from app.security.token_revocation import revoke_token


@pytest.fixture
async def receipt_context(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "payment_provider", "disabled")
    monkeypatch.setattr(settings, "app_env", "test")
    monkeypatch.setattr(settings, "demo_mode", False)

    def external_provider_forbidden():
        raise AssertionError("An offline receipt must never call an external provider")

    monkeypatch.setattr(payment_service, "get_payment_provider", external_provider_forbidden)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'offline-receipts.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(telegram_id=995002200, full_name="Клиент оплаты")
        admin = AdminUser(
            full_name="Администратор оплаты", email="receipt-admin@example.test",
            username="receipt-admin", role="admin", telegram_id=995002201,
        )
        lawyer = AdminUser(
            full_name="Юрист", email="receipt-lawyer@example.test",
            username="receipt-lawyer", role="lawyer",
        )
        db.add_all([user, admin, lawyer])
        await db.flush()
        case = Case(
            case_number="RECEIPT-2026-1", client_id=user.id, route="M1",
            status=CaseStatus.M1_WAITING_PAYMENT_30000,
            contract_signed_at=datetime.now(timezone.utc) - timedelta(days=1),
        )
        db.add(case)
        await db.flush()
        payment = Payment(
            case_id=case.id, payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            title="Первый платёж М1", amount=Decimal("30000.00"), currency="RUB",
            status=PaymentStatus.PENDING,
        )
        db.add(payment)
        await db.commit()
        context = SimpleNamespace(
            factory=factory, case_id=case.id, payment_id=payment.id, admin_id=admin.id,
            headers={"x-admin-token": create_access_token(admin.id, admin.username, ["admin"])},
            lawyer_headers={"x-admin-token": create_access_token(lawyer.id, lawyer.username, ["lawyer"])},
        )

    async def override_db():
        async with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(admin_router)
    app.include_router(web_admin_router)
    app.dependency_overrides[get_db] = override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as client:
            context.client = client
            yield context
    finally:
        await engine.dispose()


async def snapshot(context):
    response = await context.client.get("/admin/payments", headers=context.headers)
    assert response.status_code == 200, response.text
    return next(row for row in response.json() if row["id"] == context.payment_id)


def receipt_payload(row, **changes):
    return {
        "amount": row["amount_exact"],
        "paid_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "reference": "Банк: документ № 150/2026",
        "reason": "Поступление сверено с банковской выпиской",
        "case_id": row["case_id"],
        "expected_payment_status": row["status"],
        "expected_payment_updated_at": row["updated_at"],
        "expected_case_status": row["case_status"],
        "expected_case_updated_at": row["case_updated_at"],
        **changes,
    }


async def record(context, payload, *, headers=None):
    return await context.client.post(
        f"/admin/payments/{context.payment_id}/record-receipt",
        headers=context.headers if headers is None else headers, json=payload,
    )


async def assert_unconfirmed(context, *, status=CaseStatus.M1_WAITING_PAYMENT_30000):
    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        case = await db.get(Case, context.case_id)
        assert payment.status == PaymentStatus.PENDING
        assert payment.paid_at is None
        assert payment.confirmed_by is None
        assert payment.receipt_reference is None
        assert payment.confirmation_reason is None
        assert case.status == status
        assert await db.scalar(select(func.count(AuditLog.id))) == 0
        assert await db.scalar(select(func.count(Notification.id))) == 0


@pytest.mark.parametrize(
    ("code", "start", "amount", "finish"),
    [
        (PaymentCode.M1_INITIAL_PAYMENT, CaseStatus.M1_WAITING_PAYMENT_30000,
         "30000.00", CaseStatus.M1_POWER_OF_ATTORNEY),
        (PaymentCode.M1_COURT_PAYMENT, CaseStatus.M1_WAITING_PAYMENT_70000,
         "70000.00", CaseStatus.M1_ENFORCEMENT),
        (PaymentCode.M1_SUCCESS_FEE, CaseStatus.M1_WAITING_SUCCESS_FEE,
         "12500.00", CaseStatus.M1_SUCCESS_FEE_RECEIVED),
        (PaymentCode.M1_SUCCESS_FEE, CaseStatus.M1_MONEY_RECEIVED,
         "12500.00", CaseStatus.M1_SUCCESS_FEE_RECEIVED),
    ],
)
async def test_real_receipt_advances_only_its_m1_stage_and_queues_notice(
    receipt_context, code, start, amount, finish,
):
    context = receipt_context
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        payment = await db.get(Payment, context.payment_id)
        case.status = start
        case.decision_date = datetime.now(timezone.utc) - timedelta(days=2)
        case.received_at = datetime.now(timezone.utc) - timedelta(days=1)
        case.received_amount = Decimal("125000.00")
        case.success_fee_amount = Decimal("12500.00")
        payment.payment_code = code
        payment.amount = Decimal(amount)
        await db.commit()
    row = await snapshot(context)
    assert row["offline_receipt_allowed"] is True
    assert row["manual_confirm_allowed"] is False
    payload = receipt_payload(row)
    response = await record(context, payload)
    assert response.status_code == 200, response.text
    assert response.json()["case_status"] == finish
    assert response.json()["confirmed_by"] == context.admin_id

    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        case = await db.get(Case, context.case_id)
        assert payment.status == PaymentStatus.PAID
        assert payment.provider is None and payment.provider_payment_id is None
        assert payment.payment_url is None
        assert payment.amount == Decimal(amount)
        assert payment.paid_at.replace(tzinfo=timezone.utc) == datetime.fromisoformat(payload["paid_at"])
        assert payment.confirmed_by == context.admin_id
        assert payment.receipt_reference == payload["reference"]
        assert payment.confirmation_reason == payload["reason"]
        assert case.status == finish
        assert case.closed_at is None
        if code == PaymentCode.M1_COURT_PAYMENT:
            assert case.enforcement_started_at is not None
        audit = (await db.scalars(select(AuditLog).where(
            AuditLog.action == "ADMIN_OFFLINE_PAYMENT_RECEIVED",
        ))).one()
        assert audit.actor_type == "admin"
        assert audit.actor_id == context.admin_id
        assert audit.event_hash
        assert audit.new_value["amount"] == amount
        assert audit.new_value["paid_at"] == payload["paid_at"]
        assert audit.new_value["receipt_reference"] == payload["reference"]
        assert audit.old_value["payment_updated_at"] == row["updated_at"]
        assert audit.old_value["case_updated_at"] == row["case_updated_at"]
        assert await db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.actor_type == "payment_provider",
        )) == 0
        notifications = (await db.scalars(select(Notification))).all()
        assert {n.recipient_type for n in notifications} == {"client", "admin"}
        assert all(n.event_code == "PAYMENT_PAID" and n.status == "PENDING" for n in notifications)
        assert all(not n.is_sent for n in notifications)
        assert all(payload["reason"] not in n.text and payload["reference"] not in n.text for n in notifications)

    duplicate = await record(context, payload)
    assert duplicate.status_code == 409
    fresh = await snapshot(context)
    assert fresh["offline_receipt_allowed"] is False
    assert fresh["receipt_reference"] == payload["reference"]
    assert fresh["confirmed_by"] == context.admin_id
    async with context.factory() as db:
        assert await db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "ADMIN_OFFLINE_PAYMENT_RECEIVED",
        )) == 1
        assert await db.scalar(select(func.count(Notification.id))) == 2


@pytest.mark.parametrize("field", [
    "amount", "paid_at", "reference", "reason", "case_id", "expected_payment_status",
    "expected_payment_updated_at", "expected_case_status", "expected_case_updated_at",
])
async def test_missing_evidence_or_snapshot_never_records_payment(receipt_context, field):
    payload = receipt_payload(await snapshot(receipt_context))
    del payload[field]
    response = await record(receipt_context, payload)
    assert response.status_code == 422, response.text
    await assert_unconfirmed(receipt_context)


@pytest.mark.parametrize(("field", "value", "status"), [
    ("amount", "0", 422),
    ("amount", "-1", 422),
    ("amount", "30000.001", 422),
    ("amount", "NaN", 422),
    ("amount", "29999.99", 409),
    ("amount", "30000.01", 409),
    ("reference", "  ", 422),
    ("reason", "да", 422),
    ("paid_at", "2026-01-01T12:00:00", 422),
    ("paid_at", "2999-01-01T12:00:00+00:00", 409),
    ("case_id", 999999, 409),
    ("expected_payment_status", "PAID", 409),
    ("expected_payment_updated_at", "2000-01-01T00:00:00", 409),
    ("expected_case_status", "M1_POWER_OF_ATTORNEY", 409),
    ("expected_case_updated_at", "2000-01-01T00:00:00", 409),
])
async def test_invalid_evidence_and_stale_snapshots_fail_closed(receipt_context, field, value, status):
    payload = receipt_payload(await snapshot(receipt_context), **{field: value})
    response = await record(receipt_context, payload)
    assert response.status_code == status, response.text
    await assert_unconfirmed(receipt_context)


@pytest.mark.parametrize(("target", "field", "value"), [
    ("case", "contract_signed_at", None),
    ("case", "status", CaseStatus.M1_CONTRACT_READY),
    ("case", "route", "M2"),
    ("case", "content_deleted_at", datetime(2026, 1, 1, tzinfo=timezone.utc)),
    ("payment", "provider", "fake"),
    ("payment", "provider", "yookassa"),
    ("payment", "provider_payment_id", "bank-provider-id"),
    ("payment", "payment_url", "https://provider.example/pay/1"),
    ("payment", "currency", "USD"),
    ("payment", "payment_code", PaymentCode.M2_CONSULTATION_PAYMENT),
])
async def test_only_local_m1_invoice_at_verified_stage_is_eligible(receipt_context, target, field, value):
    context = receipt_context
    async with context.factory() as db:
        obj = await db.get(Case if target == "case" else Payment,
                           context.case_id if target == "case" else context.payment_id)
        setattr(obj, field, value)
        await db.commit()
        case_status = (await db.get(Case, context.case_id)).status
    row = await snapshot(context)
    assert row["offline_receipt_allowed"] is False
    response = await record(context, receipt_payload(row))
    assert response.status_code == 409, response.text
    await assert_unconfirmed(context, status=case_status)


@pytest.mark.parametrize("mode", ["fake", "yookassa"])
async def test_external_payment_modes_cannot_use_offline_endpoint(receipt_context, monkeypatch, mode):
    monkeypatch.setattr(settings, "payment_provider", mode)
    row = await snapshot(receipt_context)
    assert row["offline_receipt_allowed"] is False
    response = await record(receipt_context, receipt_payload(row))
    assert response.status_code == 409
    await assert_unconfirmed(receipt_context)


@pytest.mark.parametrize("change", ["lawyer", "inactive", "session_version", "revoked", "legacy"])
async def test_only_live_personal_admin_session_may_record(receipt_context, change):
    context = receipt_context
    payload = receipt_payload(await snapshot(context))
    headers = context.headers
    if change == "lawyer":
        headers = context.lawyer_headers
    elif change == "legacy":
        headers = {"x-admin-token": settings.admin_api_token}
    else:
        async with context.factory() as db:
            admin = await db.get(AdminUser, context.admin_id)
            if change == "inactive":
                admin.is_active = False
            elif change == "session_version":
                admin.session_version += 1
            else:
                await revoke_token(db, context.headers["x-admin-token"])
            await db.commit()
    response = await record(context, payload, headers=headers)
    assert response.status_code in {401, 403}, response.text
    await assert_unconfirmed(context)


async def test_cookie_personal_session_is_supported(receipt_context):
    context = receipt_context
    payload = receipt_payload(await snapshot(context))
    context.client.cookies.set(settings.admin_session_cookie, context.headers["x-admin-token"])
    response = await record(context, payload, headers={})
    assert response.status_code == 200, response.text


async def test_changed_received_amount_makes_success_invoice_stale(receipt_context):
    context = receipt_context
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        payment = await db.get(Payment, context.payment_id)
        case.status = CaseStatus.M1_WAITING_SUCCESS_FEE
        case.received_at = datetime.now(timezone.utc) - timedelta(days=1)
        case.received_amount = Decimal("150000.00")
        case.success_fee_amount = Decimal("12500.00")
        payment.payment_code = PaymentCode.M1_SUCCESS_FEE
        payment.amount = Decimal("12500.00")
        await db.commit()
    response = await record(context, receipt_payload(await snapshot(context)))
    assert response.status_code == 409, response.text
    assert "устарела" in response.json()["detail"]
    await assert_unconfirmed(context, status=CaseStatus.M1_WAITING_SUCCESS_FEE)


@pytest.mark.parametrize("missing", ["decision_date", "received_at", "received_amount", "success_fee_amount"])
async def test_court_and_success_receipts_require_their_actual_milestones(receipt_context, missing):
    context = receipt_context
    is_court = missing == "decision_date"
    stage = CaseStatus.M1_WAITING_PAYMENT_70000 if is_court else CaseStatus.M1_WAITING_SUCCESS_FEE
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        payment = await db.get(Payment, context.payment_id)
        case.status = stage
        case.decision_date = datetime.now(timezone.utc) - timedelta(days=2)
        case.received_at = datetime.now(timezone.utc) - timedelta(days=1)
        case.received_amount = Decimal("125000.00")
        case.success_fee_amount = Decimal("12500.00")
        setattr(case, missing, None)
        payment.payment_code = PaymentCode.M1_COURT_PAYMENT if is_court else PaymentCode.M1_SUCCESS_FEE
        payment.amount = Decimal("70000.00" if is_court else "12500.00")
        await db.commit()
    row = await snapshot(context)
    assert row["offline_receipt_allowed"] is False
    response = await record(context, receipt_payload(row))
    assert response.status_code == 409, response.text
    await assert_unconfirmed(context, status=stage)


@pytest.mark.parametrize("status", [PaymentStatus.PAID, PaymentStatus.FAILED, PaymentStatus.REFUNDED,
                                    PaymentStatus.EXPIRED, PaymentStatus.CANCELLED])
async def test_processed_or_cancelled_invoice_cannot_be_reconfirmed(receipt_context, status):
    context = receipt_context
    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        payment.status = status
        await db.commit()
    row = await snapshot(context)
    assert row["offline_receipt_allowed"] is False
    response = await record(context, receipt_payload(row))
    assert response.status_code == 409, response.text
    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        assert payment.status == status
        assert payment.paid_at is None and payment.confirmed_by is None
        assert await db.scalar(select(func.count(AuditLog.id))) == 0
        assert await db.scalar(select(func.count(Notification.id))) == 0


async def test_case_edited_after_form_open_requires_a_fresh_snapshot(receipt_context):
    context = receipt_context
    payload = receipt_payload(await snapshot(context))
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        case.title = "Карточку изменил другой сотрудник"
        case.updated_at += timedelta(seconds=1)
        await db.commit()
    response = await record(context, payload)
    assert response.status_code == 409, response.text
    await assert_unconfirmed(context)


async def test_receipt_is_blocked_after_content_erasure_approval(receipt_context):
    context = receipt_context
    payload = receipt_payload(await snapshot(context))
    async with context.factory() as db:
        db.add(CaseRetentionRecord(
            case_id=context.case_id, status="APPROVED",
            retention_due_at=datetime.now(timezone.utc),
        ))
        await db.commit()
    response = await record(context, payload)
    assert response.status_code == 409, response.text
    await assert_unconfirmed(context)


@pytest.mark.parametrize("separate_invoices", [False, True])
async def test_concurrent_receipts_with_same_case_snapshot_have_one_winner(
    receipt_context, monkeypatch, separate_invoices,
):
    """Both requests finish their reads before either can claim the snapshots."""
    context = receipt_context
    second_id = context.payment_id
    async with context.factory() as db:
        # A pre-existing audit head ensures duplicate audit initialization cannot
        # accidentally mask the financial race that this test is checking.
        db.add(AuditChainHead(id=1, event_count=0))
        if separate_invoices:
            second = Payment(
                case_id=context.case_id, payment_code=PaymentCode.M1_INITIAL_PAYMENT,
                title="Второй локальный счёт того же этапа", amount=Decimal("30000.00"),
                currency="RUB", status=PaymentStatus.PENDING,
            )
            db.add(second)
            await db.flush()
            second_id = second.id
        await db.commit()
    rows_response = await context.client.get("/admin/payments", headers=context.headers)
    rows = {row["id"]: row for row in rows_response.json()}
    barrier = asyncio.Barrier(2)
    original_claim = M1OfflineReceiptService._claim_receipt_snapshot

    async def synchronized_claim(self, payment, case):
        await asyncio.wait_for(barrier.wait(), timeout=10)
        await original_claim(self, payment, case)

    monkeypatch.setattr(M1OfflineReceiptService, "_claim_receipt_snapshot", synchronized_claim)

    async def attempt(payment_id):
        return await context.client.post(
            f"/admin/payments/{payment_id}/record-receipt", headers=context.headers,
            json=receipt_payload(rows[payment_id]),
        )

    results = await asyncio.wait_for(
        asyncio.gather(attempt(context.payment_id), attempt(second_id)), timeout=20,
    )
    assert sorted(response.status_code for response in results) == [200, 409], [
        (response.status_code, response.text) for response in results
    ]
    winner_id = next(response.json()["payment_id"] for response in results if response.status_code == 200)
    async with context.factory() as db:
        assert (await db.get(Case, context.case_id)).status == CaseStatus.M1_POWER_OF_ATTORNEY
        paid = (await db.scalars(select(Payment).where(Payment.status == PaymentStatus.PAID))).all()
        assert [payment.id for payment in paid] == [winner_id]
        audits = (await db.scalars(select(AuditLog).where(
            AuditLog.action == "ADMIN_OFFLINE_PAYMENT_RECEIVED",
        ))).all()
        assert len(audits) == 1
        assert audits[0].actor_type == "admin" and audits[0].actor_id == context.admin_id
        assert audits[0].old_value["payment_updated_at"] == rows[winner_id]["updated_at"]
        assert audits[0].old_value["case_updated_at"] == rows[winner_id]["case_updated_at"]
        assert await db.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "PAYMENT_PAID")) == 1
        assert await db.scalar(select(func.count(AuditLog.id)).where(
            AuditLog.action == "CASE_STATUS_CHANGED",
        )) == 2
        assert await db.scalar(select(func.count(Notification.id))) == 2
        if separate_invoices:
            loser_id = next(payment_id for payment_id in rows if payment_id != winner_id)
            loser = await db.get(Payment, loser_id)
            assert loser.status == PaymentStatus.PENDING
            assert loser.confirmed_by is None and loser.paid_at is None
            assert loser.updated_at.isoformat() == rows[loser_id]["updated_at"]


@pytest.mark.parametrize("change", [
    "payment_amount", "contract_signed_at", "received_amount", "retention_approval",
    "payment_microsecond", "case_microsecond",
])
async def test_change_between_validation_and_claim_cannot_be_overwritten(
    receipt_context, monkeypatch, change,
):
    context = receipt_context
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        payment = await db.get(Payment, context.payment_id)
        # Two consecutive microseconds within the same millisecond must remain
        # distinguishable, unlike a SQLite strftime/julianday comparison.
        version = datetime.now(timezone.utc).replace(microsecond=123456)
        case.updated_at = version
        payment.updated_at = version
        if change == "received_amount":
            case.status = CaseStatus.M1_WAITING_SUCCESS_FEE
            case.received_at = datetime.now(timezone.utc) - timedelta(days=1)
            case.received_amount = Decimal("125000.00")
            case.success_fee_amount = Decimal("12500.00")
            payment.payment_code = PaymentCode.M1_SUCCESS_FEE
            payment.amount = Decimal("12500.00")
        await db.commit()
    row = await snapshot(context)
    at_claim = asyncio.Event()
    continue_claim = asyncio.Event()
    original_claim = M1OfflineReceiptService._claim_receipt_snapshot

    async def paused_claim(self, payment, case):
        at_claim.set()
        await asyncio.wait_for(continue_claim.wait(), timeout=10)
        await original_claim(self, payment, case)

    monkeypatch.setattr(M1OfflineReceiptService, "_claim_receipt_snapshot", paused_claim)
    pending = asyncio.create_task(record(context, receipt_payload(row)))
    await asyncio.wait_for(at_claim.wait(), timeout=10)
    try:
        async with context.factory() as db:
            if change == "retention_approval":
                db.add(CaseRetentionRecord(
                    case_id=context.case_id, status="APPROVED",
                    retention_due_at=datetime.now(timezone.utc),
                ))
            elif change == "payment_amount":
                await db.execute(update(Payment).where(Payment.id == context.payment_id).values(
                    amount=Decimal("30001.00"), updated_at=version,
                ))
            elif change == "payment_microsecond":
                await db.execute(update(Payment).where(Payment.id == context.payment_id).values(
                    updated_at=version + timedelta(microseconds=1),
                ))
            elif change == "case_microsecond":
                await db.execute(update(Case).where(Case.id == context.case_id).values(
                    updated_at=version + timedelta(microseconds=1),
                ))
            else:
                await db.execute(update(Case).where(Case.id == context.case_id).values(
                    **{change: Decimal("150000.00") if change == "received_amount" else None},
                    updated_at=version,
                ))
            await db.commit()
    finally:
        continue_claim.set()
    response = await asyncio.wait_for(pending, timeout=15)
    assert response.status_code == 409, response.text
    await assert_unconfirmed(context, status=row["case_status"])
    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        case = await db.get(Case, context.case_id)
        if change == "payment_amount":
            assert payment.amount == Decimal("30001.00")
        if change == "received_amount":
            assert case.received_amount == Decimal("150000.00")
        if change == "contract_signed_at":
            assert case.contract_signed_at is None
        if change != "payment_microsecond":
            assert payment.updated_at.isoformat() == row["updated_at"]
        if change != "case_microsecond":
            assert case.updated_at.isoformat() == row["case_updated_at"]


async def test_outbox_failure_rolls_back_money_stage_and_evidence(receipt_context, monkeypatch):
    async def outbox_unavailable(*_args, **_kwargs):
        raise RuntimeError("notification database failure")

    monkeypatch.setattr(NotificationEngine, "emit", outbox_unavailable)
    response = await record(receipt_context, receipt_payload(await snapshot(receipt_context)))
    assert response.status_code == 500
    await assert_unconfirmed(receipt_context)


async def test_both_admin_payment_views_expose_same_receipt_snapshot(receipt_context):
    context = receipt_context
    row = await snapshot(context)
    for path in [f"/admin/cases/{context.case_id}", f"/admin/case-workspace/{context.case_id}"]:
        response = await context.client.get(path, headers=context.headers)
        assert response.status_code == 200, response.text
        item = response.json()["payments"][0]
        for field in ["offline_receipt_allowed", "amount_exact", "case_id", "updated_at",
                      "case_status", "case_updated_at", "paid_at", "confirmed_by"]:
            assert item[field] == row[field]
