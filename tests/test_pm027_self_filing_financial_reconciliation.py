from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.client_case_view import (
    ClientAction,
    ClientStageProjection,
    _payment_aware_projection,
)
from app.config import settings
from app.domain.cases.self_filing_service import SelfFilingService
from app.domain.cases.service_modes import M1ServiceMode
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.payment import Payment
from app.models.payment_event import PaymentEvent
from app.models.self_filing_package import SelfFilingPackage
from app.models.user import User
from app.system.settings_defaults import DEFAULT_SETTINGS


@asynccontextmanager
async def database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


def configure_smtp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "self_filing_email_provider", "smtp")
    monkeypatch.setattr(settings, "self_filing_smtp_host", "smtp.example.test")
    monkeypatch.setattr(settings, "self_filing_smtp_port", 587)
    monkeypatch.setattr(settings, "self_filing_smtp_username", "test-user")
    monkeypatch.setattr(settings, "self_filing_smtp_password", "test-password")
    monkeypatch.setattr(
        settings,
        "self_filing_smtp_from_email",
        "legal@example.test",
    )
    monkeypatch.setattr(settings, "self_filing_smtp_starttls", True)
    monkeypatch.setattr(settings, "self_filing_email_max_attempts", 8)
    monkeypatch.setattr(settings, "self_filing_email_timeout_seconds", 30)


def set_delivery_days(monkeypatch, value: int) -> None:
    monkeypatch.setitem(
        DEFAULT_SETTINGS["self_filing.delivery_calendar_days"],
        "value",
        value,
    )


async def bypass_claim_snapshot(
    self,
    *,
    case,
    package,
    payment_at,
    actor_type,
    actor_id,
):
    # Financial-reconciliation tests are intentionally scoped to received-money
    # durability; the claim-calculation authority has dedicated PM-027 tests.
    return None


async def seed_payment_pending(session, *, suffix: int):
    now = datetime.now(timezone.utc)
    user = User(
        telegram_id=995000 + suffix,
        full_name=f"Клиент самостоятельной подачи {suffix}",
    )
    lawyer = Lawyer(
        full_name=f"Юрист самостоятельной подачи {suffix}",
        telegram_id=996000 + suffix,
        is_active=True,
    )
    session.add_all([user, lawyer])
    await session.flush()

    case = Case(
        case_number=f"SELF-FILING-PAY-{suffix}",
        client_id=user.id,
        route="M1",
        service_mode=M1ServiceMode.SELF_FILING_PACKAGE.value,
        status=CaseStatus.M1_SELF_FILING_PAYMENT_PENDING,
        title="Пакет для самостоятельной подачи",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()

    package = SelfFilingPackage(
        case_id=case.id,
        status="PAYMENT_PENDING",
        version=7,
        client_region="Тверская область",
        client_address="г. Тверь, тестовый адрес",
        delivery_email="client@example.test",
        email_confirmed_at=now - timedelta(hours=3),
        documents_complete_at=now - timedelta(hours=2),
        documents_complete_by_lawyer_id=lawyer.id,
        court_name="Тестовый районный суд",
        court_address="г. Тверь, тестовый адрес суда",
        jurisdiction_basis="CLIENT_RESIDENCE_OR_STAY",
        jurisdiction_note="Подсудность проверена юристом по материалам тестового дела",
        jurisdiction_confirmed_at=now - timedelta(hours=2),
        jurisdiction_confirmed_by_lawyer_id=lawyer.id,
    )
    payment = Payment(
        case_id=case.id,
        payment_code=PaymentCode.M1_SELF_FILING_PACKAGE,
        title="Подготовка пакета документов для самостоятельной подачи",
        amount=Decimal("15000.00"),
        currency="RUB",
        status=PaymentStatus.PENDING,
        provider="yookassa",
        provider_payment_id=f"self-filing-provider-{suffix}",
    )
    session.add_all([package, payment])
    await session.flush()
    return user, lawyer, case, package, payment


@pytest.mark.asyncio
async def test_received_money_survives_failed_sla_activation(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)
    # Simulate configuration drift after the lawyer opened the payment. Runtime
    # truth must preserve provider-confirmed money when the commercial contract
    # cannot safely start delivery.
    set_delivery_days(monkeypatch, 4)
    monkeypatch.setattr(
        SelfFilingService,
        "_freeze_claim_calculation",
        bypass_claim_snapshot,
    )

    async with database(tmp_path, "self-filing-payment-review.db") as factory:
        async with factory() as session:
            _user, _lawyer, case, package, payment = await seed_payment_pending(
                session,
                suffix=1,
            )
            await session.commit()
            payment_id = int(payment.id)
            case_id = int(case.id)
            package_id = int(package.id)

            occurred_at = datetime.now(timezone.utc)
            result = await PaymentWebhookService(session).process_successful_payment(
                payment=payment,
                case=case,
                provider_payload={
                    "provider_payment_id": payment.provider_payment_id,
                    "status": "succeeded",
                },
                occurred_at=occurred_at,
            )
            await session.commit()

            persisted_payment = await session.get(Payment, payment_id)
            persisted_case = await session.get(Case, case_id)
            persisted_package = await session.get(SelfFilingPackage, package_id)

            assert result.id == payment_id
            assert persisted_payment.status == PaymentStatus.PAID_REVIEW
            assert persisted_payment.paid_at is not None
            assert persisted_case.status == CaseStatus.M1_SELF_FILING_PAYMENT_PENDING
            assert persisted_package.status == "PAYMENT_PENDING"
            assert persisted_package.payment_confirmed_at is None
            assert persisted_package.sla_started_at is None
            assert persisted_package.sla_due_at is None

            review_event = (
                await session.execute(
                    select(AuditLog)
                    .where(
                        AuditLog.entity_type == "case",
                        AuditLog.entity_id == case_id,
                        AuditLog.action == "SELF_FILING_PAYMENT_REVIEW_REQUIRED",
                    )
                    .order_by(AuditLog.id.desc())
                )
            ).scalars().first()
            assert review_event is not None
            assert review_event.new_value["payment_status"] == PaymentStatus.PAID_REVIEW
            assert review_event.new_value["money_received_at"]

            ledger = list(
                (
                    await session.execute(
                        select(PaymentEvent)
                        .where(PaymentEvent.payment_id == payment_id)
                        .order_by(PaymentEvent.id.asc())
                    )
                ).scalars().all()
            )
            statuses = [str(item.status_after) for item in ledger]
            assert PaymentStatus.PAID in statuses
            assert PaymentStatus.PAID_REVIEW in statuses


@pytest.mark.asyncio
async def test_admin_resume_uses_original_money_time_and_starts_sla_once(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)
    set_delivery_days(monkeypatch, 4)
    monkeypatch.setattr(
        SelfFilingService,
        "_freeze_claim_calculation",
        bypass_claim_snapshot,
    )

    async with database(tmp_path, "self-filing-payment-resume.db") as factory:
        async with factory() as session:
            _user, _lawyer, case, _package, payment = await seed_payment_pending(
                session,
                suffix=2,
            )
            await session.commit()

            await PaymentWebhookService(session).process_successful_payment(
                payment=payment,
                case=case,
                provider_payload={"status": "succeeded"},
                occurred_at=datetime.now(timezone.utc),
            )
            await session.commit()
            await session.refresh(payment)
            original_paid_at = payment.paid_at
            assert payment.status == PaymentStatus.PAID_REVIEW
            assert original_paid_at is not None

            set_delivery_days(monkeypatch, 3)
            package, resolved_payment = await SelfFilingService(
                session
            ).resolve_received_payment_review(
                case_id=int(case.id),
                payment_id=int(payment.id),
                actor_id=7002,
                decision="resume",
                comment="Календарь и канал доставки проверены администратором",
            )
            await session.commit()
            await session.refresh(case)
            await session.refresh(resolved_payment)
            await session.refresh(package)

            assert resolved_payment.status == PaymentStatus.PAID
            assert resolved_payment.paid_at == original_paid_at
            assert case.status == CaseStatus.M1_SELF_FILING_PREPARATION
            assert package.status == "PREPARATION"
            assert package.payment_confirmed_at is not None
            assert package.sla_started_at is not None
            assert package.sla_due_at is not None

            # SQLite may hydrate timezone-naive values; compare normalized UTC.
            started = package.sla_started_at
            paid = original_paid_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if paid.tzinfo is None:
                paid = paid.replace(tzinfo=timezone.utc)
            assert started == paid
            due = package.sla_due_at
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            assert due == paid + timedelta(days=3)

            # Exact retry is idempotent after the package has already started.
            retry_package, retry_payment = await SelfFilingService(
                session
            ).resolve_received_payment_review(
                case_id=int(case.id),
                payment_id=int(payment.id),
                actor_id=7002,
                decision="resume",
                comment="Повтор команды после подтверждённого запуска пакета",
            )
            assert retry_payment.status == PaymentStatus.PAID
            assert retry_package.sla_started_at == package.sla_started_at


@pytest.mark.asyncio
async def test_admin_refund_keeps_case_out_of_preparation_and_blocks_client_repay(
    tmp_path,
    monkeypatch,
):
    configure_smtp(monkeypatch)
    set_delivery_days(monkeypatch, 4)
    monkeypatch.setattr(
        SelfFilingService,
        "_freeze_claim_calculation",
        bypass_claim_snapshot,
    )

    async with database(tmp_path, "self-filing-payment-refund.db") as factory:
        async with factory() as session:
            _user, _lawyer, case, package, payment = await seed_payment_pending(
                session,
                suffix=3,
            )
            await session.commit()

            await PaymentWebhookService(session).process_successful_payment(
                payment=payment,
                case=case,
                provider_payload={"status": "succeeded"},
                occurred_at=datetime.now(timezone.utc),
            )
            await session.commit()

            package, payment = await SelfFilingService(
                session
            ).resolve_received_payment_review(
                case_id=int(case.id),
                payment_id=int(payment.id),
                actor_id=7003,
                decision="refund_pending",
                comment="Услугу по полученному платежу не запускаем, оформляем возврат",
            )
            await session.commit()
            await session.refresh(case)
            await session.refresh(payment)
            await session.refresh(package)

            assert payment.status == PaymentStatus.REFUND_PENDING
            assert case.status == CaseStatus.M1_SELF_FILING_PAYMENT_PENDING
            assert package.sla_started_at is None
            assert package.sla_due_at is None

            base = ClientStageProjection(
                status_label="Ожидается оплата",
                now_text="Базовый текст оплаты",
                client_requirement="Оплатите",
                blocker=None,
                action=ClientAction(
                    "Открыть оплату 15 000 ₽",
                    "pay_self_filing",
                    "Оплатите пакет",
                ),
            )
            client_projection = _payment_aware_projection(
                case,
                base,
                [payment],
            )
            assert client_projection.action is not None
            assert client_projection.action.callback == "payments_open"
            assert "Повторно не оплачивайте" in client_projection.client_requirement
            assert "возврат" in client_projection.now_text.lower()


def test_self_filing_review_has_dedicated_admin_route_and_m2_review_is_scoped():
    product = __import__(
        "app.api.self_filing_product",
        fromlist=["SELF_FILING_HTML"],
    )
    source = open(product.__file__, encoding="utf-8").read()
    payment_review_source = open(
        __import__(
            "app.api.payment_review_center",
            fromlist=["router"],
        ).__file__,
        encoding="utf-8",
    ).read()

    assert "/payment-review/{payment_id}/resolve" in source
    assert "can_financial_reconcile" in source
    assert "resolve_received_payment_review" in source
    assert "Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT" in payment_review_source
    assert "Этот платёж не относится к консультационной сверке" in payment_review_source
