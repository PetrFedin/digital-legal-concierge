from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.bot.case_callback_scope import (
    bind_payment_case_action,
    bound_case_callback,
    callback_matches_action,
    parse_bound_case_id,
)
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.payment import Payment
from app.models.payment_event import PaymentEvent, PaymentEventIntegrityError
from app.models.user import User
from app.presentation_time import format_business_datetime


def test_case_bound_callback_contract() -> None:
    token = bound_case_callback("pay_start_30000", 42)

    assert token == "pay_start_30000:v2:42"
    assert callback_matches_action(token, "pay_start_30000") is True
    assert parse_bound_case_id(token, "pay_start_30000") == (False, 42)
    assert parse_bound_case_id("pay_start_30000", "pay_start_30000") == (True, None)
    assert bind_payment_case_action("pay_start_30000", 42) == token
    assert bind_payment_case_action("calc_recover", 42) == "calc_recover:v2:42"
    assert bind_payment_case_action("documents_open", 42) == "documents_open:v2:42"


def test_business_timezone_formatter_uses_configured_moscow_boundary() -> None:
    value = datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc)

    assert format_business_datetime(value) == "19.08.2026 15:30 МСК"


def test_case_terminal_lifecycle_facts_are_stamped_and_recovery_clears_them() -> None:
    case = Case(
        case_number="TEST-CASE-1",
        client_id=1,
        route="M1",
        status="M1_ENFORCEMENT",
    )

    case.status = "M1_CLOSED"
    assert case.closed_at is not None
    assert case.close_reason == "M1_COMPLETED"
    assert case.archived_at is None

    case.status = "ARCHIVED"
    assert case.archived_at is not None

    case.status = "M1_ENFORCEMENT"
    assert case.closed_at is None
    assert case.archived_at is None
    assert case.close_reason is None


def test_case_explicit_close_reason_is_preserved() -> None:
    case = Case(
        case_number="TEST-CASE-2",
        client_id=1,
        route="M2",
        status="M2_CONSULTATION_DONE",
        close_reason="M2_CLIENT_NO_SHOW",
    )

    case.status = "M2_CLOSED"

    assert case.closed_at is not None
    assert case.close_reason == "M2_CLIENT_NO_SHOW"


def _payment(status: str = "PENDING") -> Payment:
    return Payment(
        case_id=1,
        payment_code="TEST",
        title="Test payment",
        amount=Decimal("100.00"),
        currency="RUB",
        status=status,
    )


def test_payment_lifecycle_timestamps_are_first_fact_timestamps() -> None:
    payment = _payment()

    payment.status = "PAID"
    paid_at = payment.paid_at
    assert paid_at is not None

    payment.status = "REFUND_PENDING"
    assert payment.paid_at == paid_at

    payment.status = "REFUNDED"
    assert payment.paid_at == paid_at
    assert payment.refunded_at is not None


def test_payment_lifecycle_service_uses_exact_fact_time_and_is_idempotent() -> None:
    payment = _payment()
    paid_fact_at = datetime(2026, 8, 19, 10, 15, tzinfo=timezone.utc)
    refund_fact_at = paid_fact_at + timedelta(days=2)

    paid = PaymentLifecycleService.transition(
        payment,
        to_status=PaymentStatus.PAID,
        occurred_at=paid_fact_at,
    )
    assert paid.changed is True
    assert paid.old_status == PaymentStatus.PENDING
    assert paid.new_status == PaymentStatus.PAID
    assert payment.paid_at == paid_fact_at

    pending_refund = PaymentLifecycleService.transition(
        payment,
        to_status=PaymentStatus.REFUND_PENDING,
        occurred_at=paid_fact_at + timedelta(hours=1),
    )
    assert pending_refund.changed is True
    assert payment.paid_at == paid_fact_at

    refunded = PaymentLifecycleService.transition(
        payment,
        to_status=PaymentStatus.REFUNDED,
        occurred_at=refund_fact_at,
    )
    assert refunded.changed is True
    assert payment.refunded_at == refund_fact_at
    assert payment.paid_at == paid_fact_at

    repeated = PaymentLifecycleService.transition(
        payment,
        to_status=PaymentStatus.REFUNDED,
        occurred_at=refund_fact_at + timedelta(hours=1),
    )
    assert repeated.changed is False
    assert payment.refunded_at == refund_fact_at
    assert payment.paid_at == paid_fact_at


def test_payment_event_ledger_records_creation_and_status_change_in_same_db() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        user = User(telegram_id=990000001, full_name="Ledger Test")
        session.add(user)
        session.flush()
        case = Case(
            case_number="LEDGER-CASE-1",
            client_id=user.id,
            route="M1",
            status="M1_WAITING_PAYMENT_30000",
        )
        session.add(case)
        session.flush()
        payment = Payment(
            case_id=case.id,
            payment_code="M1_INITIAL_PAYMENT",
            title="Initial payment",
            amount=Decimal("30000.00"),
            currency="RUB",
            status="PENDING",
        )
        session.add(payment)
        session.commit()

        created = list(
            session.scalars(
                select(PaymentEvent)
                .where(PaymentEvent.payment_id == payment.id)
                .order_by(PaymentEvent.id.asc())
            )
        )
        assert len(created) == 1
        assert created[0].event_type == "CREATED"
        assert created[0].status_before is None
        assert created[0].status_after == "PENDING"

        # Direct assignment intentionally exercises the model-level backstop.
        # Application code is separately forbidden from doing this by
        # scripts/architecture_check.py.
        payment.status = "PAID"
        session.commit()

        events = list(
            session.scalars(
                select(PaymentEvent)
                .where(PaymentEvent.payment_id == payment.id)
                .order_by(PaymentEvent.id.asc())
            )
        )
        assert [item.event_type for item in events] == ["CREATED", "STATUS_CHANGED"]
        assert events[-1].status_before == "PENDING"
        assert events[-1].status_after == "PAID"
        assert payment.paid_at is not None

        # Non-status edits must not manufacture another financial lifecycle event.
        payment.title = "Initial payment — reconciled label"
        session.commit()
        assert (
            session.scalar(
                select(PaymentEvent.id)
                .where(PaymentEvent.payment_id == payment.id)
                .order_by(PaymentEvent.id.desc())
                .limit(1)
            )
            == events[-1].id
        )

        # The normalized financial ledger is evidence, not an editable
        # projection. Ordinary ORM repair/admin code must fail closed rather than
        # rewrite the past.
        protected_event_id = int(events[-1].id)
        protected_event = session.get(PaymentEvent, protected_event_id)
        assert protected_event is not None
        protected_event.status_after = "TAMPERED"
        with pytest.raises(PaymentEventIntegrityError):
            session.commit()
        session.rollback()

        unchanged = session.get(PaymentEvent, protected_event_id)
        assert unchanged is not None
        assert unchanged.status_after == "PAID"

        session.delete(unchanged)
        with pytest.raises(PaymentEventIntegrityError):
            session.commit()
        session.rollback()

        still_present = session.get(PaymentEvent, protected_event_id)
        assert still_present is not None
        assert still_present.status_after == "PAID"
