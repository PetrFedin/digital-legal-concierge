from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.payments.payment_processing_outcomes import PaymentProcessingOutcome
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


async def _create_test_database(tmp_path, name: str):
    database_path = tmp_path / name
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, session_factory


async def _seed_m1_payment(session, *, suffix: int, case_status: str, payment_code: str):
    user = User(
        telegram_id=930_000 + suffix,
        telegram_username=f"m1_client_{suffix}",
        full_name=f"Клиент М1 {suffix}",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number=f"TEST-M1-PAYMENT-{suffix}",
        client_id=user.id,
        route=RouteCode.M1.value,
        status=case_status,
        title="Тест оплаты маршрута М1",
        next_action="Ожидается оплата",
    )
    session.add(case)
    await session.flush()

    payment = Payment(
        case_id=case.id,
        payment_code=payment_code,
        title="Платёж М1",
        amount=Decimal("30000.00"),
        currency="RUB",
        status=PaymentStatus.WAITING_CONFIRMATION,
        provider="fake",
        provider_payment_id=f"m1-provider-{suffix}",
        payment_url=f"https://payments.example.test/m1/{suffix}",
    )
    session.add(payment)
    await session.commit()
    return case.id, payment.id


async def _action_count(session, *, case_id: int, action: str) -> int:
    return int(
        (
            await session.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id == case_id,
                    AuditLog.action == action,
                )
            )
        ).scalar_one()
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case_status", "payment_code", "expected_status"),
    [
        (
            CaseStatus.M1_WAITING_PAYMENT_30000.value,
            PaymentCode.M1_INITIAL_PAYMENT,
            CaseStatus.M1_POWER_OF_ATTORNEY.value,
        ),
        (
            CaseStatus.M1_WAITING_PAYMENT_70000.value,
            PaymentCode.M1_COURT_PAYMENT,
            CaseStatus.M1_ENFORCEMENT.value,
        ),
        (
            CaseStatus.M1_WAITING_SUCCESS_FEE.value,
            PaymentCode.M1_SUCCESS_FEE,
            CaseStatus.M1_CLOSED.value,
        ),
    ],
)
async def test_m1_payment_advances_only_expected_stage(
    tmp_path,
    case_status,
    payment_code,
    expected_status,
):
    engine, session_factory = await _create_test_database(
        tmp_path,
        f"m1-{payment_code}.db",
    )

    async with session_factory() as session:
        case_id, payment_id = await _seed_m1_payment(
            session,
            suffix=len(payment_code),
            case_status=case_status,
            payment_code=payment_code,
        )

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        payment = await session.get(Payment, payment_id)

        await PaymentWebhookService(session).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded"},
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert payment.processing_outcome == PaymentProcessingOutcome.PROCESSED
        assert payment.manual_review_required is False
        assert payment.processing_error is None
        assert payment.processed_at is not None
        assert case.status == expected_status
        assert case.route == RouteCode.M1.value
        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        ) == 1
        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        ) == 1

        processed = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "PAYMENT_WEBHOOK_PROCESSED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert processed is not None
        assert processed.new_value["m1_transitions"]
        assert processed.new_value["m1_transitions"][-1] == expected_status

        await PaymentWebhookService(session).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded.retry"},
        )
        await session.commit()

        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_PAID",
        ) == 1
        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        ) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_m1_early_payment_requires_manual_review_without_skipping_stages(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m1-early-payment.db",
    )

    async with session_factory() as session:
        case_id, payment_id = await _seed_m1_payment(
            session,
            suffix=101,
            case_status=CaseStatus.M1_LAWYER_REVIEW.value,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        )

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        payment = await session.get(Payment, payment_id)

        await PaymentWebhookService(session).process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"event": "payment.succeeded"},
        )
        await session.commit()

        assert payment.status == PaymentStatus.PAID
        assert payment.processing_outcome == PaymentProcessingOutcome.CONFLICT
        assert payment.manual_review_required is True
        assert payment.processed_at is None
        assert "раньше допустимого этапа" in payment.processing_error
        assert case.status == CaseStatus.M1_LAWYER_REVIEW.value
        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_REQUIRES_MANUAL_REVIEW",
        ) == 1
        assert await _action_count(
            session,
            case_id=case.id,
            action="PAYMENT_WEBHOOK_PROCESSED",
        ) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_can_reprocess_m1_payment_after_status_correction(tmp_path):
    engine, session_factory = await _create_test_database(
        tmp_path,
        "m1-admin-reprocess.db",
    )

    async with session_factory() as session:
        case_id, payment_id = await _seed_m1_payment(
            session,
            suffix=102,
            case_status=CaseStatus.M1_LAWYER_REVIEW.value,
            payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        )

    async with session_factory() as session:
        case = await session.get(Case, case_id)
        payment = await session.get(Payment, payment_id)
        webhook = PaymentWebhookService(session)

        await webhook.process_successful_payment(payment=payment, case=case)
        await session.commit()
        assert payment.processing_outcome == PaymentProcessingOutcome.CONFLICT

        case.status = CaseStatus.M1_WAITING_PAYMENT_30000.value
        case.next_action = "Оплатить первый платёж"
        await session.flush()

        await webhook.process_successful_payment(
            payment=payment,
            case=case,
            provider_payload={"source": "admin_manual_confirm"},
            allow_reprocess=True,
            actor_type="admin",
            actor_id=88,
            source="admin_manual_confirm",
        )
        await session.commit()

        assert payment.processing_outcome == PaymentProcessingOutcome.PROCESSED
        assert payment.manual_review_required is False
        assert payment.processing_error is None
        assert case.status == CaseStatus.M1_POWER_OF_ATTORNEY.value

        processed = (
            await session.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_id == case.id,
                    AuditLog.action == "PAYMENT_WEBHOOK_PROCESSED",
                )
                .order_by(AuditLog.id.desc())
            )
        ).scalars().first()
        assert processed.actor_type == "admin"
        assert processed.actor_id == 88
        assert processed.new_value["manual_reprocess"] is True
        assert (
            processed.new_value["previous_processing_outcome"]
            == PaymentProcessingOutcome.CONFLICT.value
        )

    await engine.dispose()
