"""POA readiness is a client report; receipt is an assigned-lawyer action."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.lawyer import router as lawyer_router
from app.db.session import get_db
from app.domain.cases.case_service import CaseService
from app.domain.cases.case_transition_policy import CaseTransitionError
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User
from app.security.access_control import create_access_token


@pytest.fixture
async def poa_context(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'poa.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(telegram_id=994202610, full_name="Клиент доверенности")
        lawyer = Lawyer(full_name="Назначенный юрист", email="poa@example.test")
        outsider = Lawyer(full_name="Другой юрист", email="other-poa@example.test")
        account = AdminUser(
            full_name=lawyer.full_name, email=lawyer.email,
            username="poa-lawyer", role="lawyer",
        )
        other_account = AdminUser(
            full_name=outsider.full_name, email=outsider.email,
            username="other-poa-lawyer", role="lawyer",
        )
        admin = AdminUser(
            full_name="Администратор", email="poa-admin@example.test",
            username="poa-admin", role="admin",
        )
        db.add_all([user, lawyer, outsider, account, other_account, admin])
        await db.flush()
        case = Case(
            case_number="POA-2026-1", client_id=user.id, route="M1",
            status=CaseStatus.M1_POWER_OF_ATTORNEY,
            assigned_lawyer_id=lawyer.id,
            next_action="Оформить доверенность",
        )
        db.add(case)
        await db.flush()
        payment = Payment(
            case_id=case.id, payment_code=PaymentCode.M1_INITIAL_PAYMENT,
            title="Первый платёж", amount=Decimal("30000.00"),
            status=PaymentStatus.PAID,
        )
        db.add(payment)
        await db.commit()
        context = SimpleNamespace(
            factory=factory, case_id=case.id, payment_id=payment.id,
            lawyer_id=lawyer.id, user_id=user.id,
            headers={"x-admin-token": create_access_token(
                account.id, account.username, ["lawyer"],
            )},
            other_headers={"x-admin-token": create_access_token(
                other_account.id, other_account.username, ["lawyer"],
            )},
            admin_headers={"x-admin-token": create_access_token(
                admin.id, admin.username, ["admin"],
            )},
        )

    async def override_db():
        async with factory() as db:
            yield db

    app = FastAPI()
    app.include_router(lawyer_router)
    app.dependency_overrides[get_db] = override_db
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test",
        ) as client:
            context.client = client
            yield context
    finally:
        await engine.dispose()


async def snapshot(context):
    response = await context.client.get("/lawyer/cases", headers=context.headers)
    assert response.status_code == 200, response.text
    return next(row for row in response.json() if row["case_id"] == context.case_id)


def receipt_payload(row, **overrides):
    return {
        "action": "poa_received",
        "comment": "Доверенность и заверенные копии получены курьером",
        "expected_status": row["status"],
        "expected_updated_at": row["updated_at"],
        **overrides,
    }


async def confirm(context, payload, headers=None):
    return await context.client.post(
        f"/lawyer/cases/{context.case_id}/m1-process",
        headers=context.headers if headers is None else headers,
        json=payload,
    )


async def assert_not_received(context):
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY
        assert case.poa_received_at is None
        assert await db.scalar(select(func.count(AuditLog.id))) == 0


async def test_assigned_lawyer_receipt_opens_claim_and_is_not_repeatable(poa_context):
    context = poa_context
    initial = await snapshot(context)
    assert initial["can_confirm_poa"] is True
    assert initial["can_send_claim"] is False
    # Receiving documents outside the bot does not require a client readiness report.
    assert initial["poa_client_confirmed_at"] is None
    payload = receipt_payload(initial)
    received = await confirm(context, payload)
    assert received.status_code == 200, received.text
    assert received.json()["status"] == CaseStatus.M1_POA_RECEIVED
    assert received.json()["poa_received_at"]

    duplicate = await confirm(context, payload)
    assert duplicate.status_code == 409
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        received_at = case.poa_received_at
        assert received_at.isoformat() == received.json()["poa_received_at"]
        assert (await db.get(Payment, context.payment_id)).status == PaymentStatus.PAID
        audit = (await db.scalars(select(AuditLog).where(
            AuditLog.action == "POA_RECEIVED",
        ))).one()
        assert audit.entity_id == case.id
        assert audit.actor_type == "lawyer"
        assert audit.actor_id == context.lawyer_id
        assert audit.comment == payload["comment"]
        assert audit.new_value["payment_id"] == context.payment_id

    ready = await snapshot(context)
    assert ready["can_confirm_poa"] is False
    assert ready["can_send_claim"] is True
    sent = await confirm(context, receipt_payload(
        ready, action="claim_sent", comment="Претензия отправлена заказным письмом",
    ))
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == CaseStatus.M1_WAITING_30_DAYS
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.poa_received_at == received_at
        assert case.claim_waiting_until - case.claim_sent_at == timedelta(days=30)


@pytest.mark.parametrize("payment_state", [
    None, PaymentStatus.PENDING, PaymentStatus.PAID_REVIEW, PaymentStatus.REFUNDED,
    "different_type", "different_case",
])
async def test_receipt_requires_this_cases_confirmed_initial_payment(
    poa_context, payment_state,
):
    context = poa_context
    async with context.factory() as db:
        payment = await db.get(Payment, context.payment_id)
        if payment_state is None:
            await db.delete(payment)
        elif payment_state == "different_type":
            payment.payment_code = PaymentCode.M1_COURT_PAYMENT
        elif payment_state == "different_case":
            other = Case(
                case_number="POA-2026-2", client_id=context.user_id,
                route="M1", status=CaseStatus.M1_POWER_OF_ATTORNEY,
            )
            db.add(other)
            await db.flush()
            payment.case_id = other.id
        else:
            payment.status = payment_state
        await db.commit()

    row = await snapshot(context)
    assert row["can_confirm_poa"] is False
    response = await confirm(context, receipt_payload(row))
    assert response.status_code == 409, response.text
    assert "первого платежа" in response.json()["detail"]
    await assert_not_received(context)


@pytest.mark.parametrize("headers_name,code", [
    ("other_headers", 403), ("admin_headers", 403), (None, 401),
])
async def test_poa_endpoint_preserves_personal_lawyer_authority(
    poa_context, headers_name, code,
):
    context = poa_context
    row = await snapshot(context)
    headers = getattr(context, headers_name) if headers_name else {}
    response = await confirm(context, receipt_payload(row), headers=headers)
    assert response.status_code == code, response.text
    await assert_not_received(context)


@pytest.mark.parametrize("change", [
    {"expected_status": CaseStatus.M1_PAYMENT_30000_RECEIVED},
    {"expected_updated_at": "2000-01-01T00:00:00"},
    {"expected_status": None},
    {"expected_updated_at": None},
    {"comment": ""},
])
async def test_poa_requires_current_snapshot_and_receipt_details(poa_context, change):
    context = poa_context
    response = await confirm(context, receipt_payload(await snapshot(context), **change))
    assert response.status_code == 409, response.text
    await assert_not_received(context)


async def test_receipt_is_unavailable_before_poa_stage(poa_context):
    context = poa_context
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        case.status = CaseStatus.M1_WAITING_PAYMENT_30000
        await db.commit()
    row = await snapshot(context)
    assert row["can_confirm_poa"] is False
    response = await confirm(context, receipt_payload(row))
    assert response.status_code == 409, response.text
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        assert case.status == CaseStatus.M1_WAITING_PAYMENT_30000
        assert case.poa_received_at is None


@pytest.mark.parametrize("actor_type,force", [
    ("client", False), ("client", True), ("system", False),
])
async def test_client_cannot_bypass_receipt_through_case_transition(
    poa_context, actor_type, force,
):
    context = poa_context
    async with context.factory() as db:
        case = await db.get(Case, context.case_id)
        with pytest.raises(CaseTransitionError):
            await CaseService(db).change_status(
                case=case, next_status=CaseStatus.M1_POA_RECEIVED,
                actor_type=actor_type, actor_id=context.user_id,
                comment="Сообщение о готовности документов", force=force,
            )
        await db.commit()
    await assert_not_received(context)
