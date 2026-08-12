from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.workdesk as workdesk_module
from app.api.workdesk import (
    _append_payment_attention,
    _attention_item,
    _empty_payment_attention,
    workdesk_attention,
)
from app.domain.documents.document_workflow import describe_document_attention
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.user import User


def make_case() -> Case:
    now = datetime.now(timezone.utc)
    return Case(
        id=77,
        case_number="WORKDESK-FIN-77",
        client_id=1,
        route="M2",
        status="M2_CONSULTATION_BOOKED",
        title="Финансовая задача",
        next_action="Подготовиться к консультации",
        assigned_lawyer_id=None,
        sla_status="ACTION_OVERDUE",
        sla_due_at=now - timedelta(hours=4),
        created_at=now - timedelta(days=2),
        updated_at=now - timedelta(minutes=10),
    )


def build_item(attention):
    return _attention_item(
        make_case(),
        unread_client_messages=2,
        latest_client_message_at=datetime.now(timezone.utc) - timedelta(minutes=20),
        document_workflow=describe_document_attention(()),
        consultation_at=None,
        lawyer_name=None,
        payment_attention=attention,
    )


def test_payment_review_has_top_workdesk_priority():
    attention = _empty_payment_attention()
    _append_payment_attention(
        attention,
        payment_id=501,
        status=PaymentStatus.PAID_REVIEW,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    _append_payment_attention(
        attention,
        payment_id=502,
        status=PaymentStatus.REFUND_PENDING,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )

    item = build_item(attention)

    assert item is not None
    assert [reason["code"] for reason in item["reasons"]][:2] == [
        "payment_review",
        "refund",
    ]
    assert item["priority"] == 0
    assert item["financial_attention"] == {
        "payment_review_ids": [501],
        "refund_pending_ids": [502],
    }
    assert item["primary_action"]["label"] == "Сверить полученный платёж"
    assert item["primary_action"]["href"].endswith("payment_id=501")


def test_refund_is_primary_when_review_is_absent():
    attention = _empty_payment_attention()
    _append_payment_attention(
        attention,
        payment_id=601,
        status=PaymentStatus.REFUND_PENDING,
        activity_at=datetime.now(timezone.utc) - timedelta(hours=3),
    )

    item = build_item(attention)

    assert item is not None
    assert item["reasons"][0]["code"] == "refund"
    assert item["priority"] == 1
    assert item["primary_action"]["label"] == "Обработать возврат"
    assert item["primary_action"]["href"].endswith("payment_id=601")


@pytest.mark.asyncio
async def test_workdesk_endpoint_includes_case_with_only_payment_review_attention(
    tmp_path,
    monkeypatch,
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'workdesk-financial.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    async with factory() as session:
        user = User(telegram_id=998001, full_name="Клиент workdesk payment")
        lawyer = Lawyer(
            full_name="Юрист workdesk payment",
            telegram_id=998002,
            is_active=True,
        )
        session.add_all([user, lawyer])
        await session.flush()
        case = Case(
            case_number="WORKDESK-PAYMENT-ONLY",
            client_id=user.id,
            route="M2",
            status="M2_CONSULTATION_BOOKED",
            title="Только финансовая сверка",
            next_action="Подготовиться к консультации",
            assigned_lawyer_id=lawyer.id,
            sla_status="NOT_STARTED",
        )
        session.add(case)
        await session.flush()
        payment = Payment(
            case_id=case.id,
            payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
            title="Оплата консультации",
            amount=Decimal("5000.00"),
            currency="RUB",
            status=PaymentStatus.PAID_REVIEW,
            provider="yookassa",
            provider_payment_id="workdesk-review-only",
        )
        session.add(payment)
        await session.commit()

        monkeypatch.setattr(
            workdesk_module,
            "require_admin",
            lambda token: {"roles": ["admin"]},
        )
        payload = await workdesk_attention(
            limit=12,
            db=session,
            x_admin_token="test-admin-token",
        )

        assert payload["total"] == 1
        assert payload["count"] == 1
        item = payload["items"][0]
        assert item["id"] == case.id
        assert item["priority"] == 0
        assert item["reasons"][0]["code"] == "payment_review"
        assert item["financial_attention"]["payment_review_ids"] == [payment.id]
        assert item["primary_action"]["label"] == "Сверить полученный платёж"
        assert item["primary_action"]["href"].endswith(
            f"payment_id={payment.id}"
        )

    await engine.dispose()
