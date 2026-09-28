from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.admin as admin_module
from app.domain.cases.m1_enforcement_service import M1EnforcementService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.notification import Notification
from app.models.payment import Payment
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'offline-m1-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_enforcement_case(session):
    user = User(telegram_id=983203, full_name="Клиент Офлайн Оплата")
    lawyer = Lawyer(full_name="Юрист Офлайн Оплата", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number="M1-OFFLINE-V36",
        client_id=user.id,
        route="M1",
        status=CaseStatus.M1_ENFORCEMENT,
        title="Офлайн success fee",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return case, lawyer


@pytest.mark.asyncio
async def test_admin_offline_success_fee_confirmation_closes_case_atomically(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        admin_module,
        "require_admin",
        lambda _token: {"uid": "77", "roles": ["admin"]},
    )
    monkeypatch.setattr(
        admin_module,
        "offline_payment_confirmation_enabled",
        lambda: True,
    )

    async with database(tmp_path) as factory:
        async with factory() as session:
            case, lawyer = await seed_enforcement_case(session)
            result = await M1EnforcementService(session).record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="250000.00",
                comment="Исполнитель перечислил деньги клиенту",
            )
            await session.commit()

            assert case.status == CaseStatus.M1_WAITING_SUCCESS_FEE
            assert result.payment.status == PaymentStatus.PENDING
            assert Decimal(str(result.payment.amount)) == Decimal("25000.00")
            assert admin_module.payment_can_be_confirmed_offline(result.payment) is True

            response = await admin_module.confirm_offline_payment(
                payment_id=result.payment.id,
                payload={
                    "expected_status": PaymentStatus.PENDING,
                    "reference": "BANK-250000-2026",
                    "comment": "Поступление проверено по банковской выписке",
                },
                db=session,
                x_admin_token="test-admin-token",
            )

            await session.refresh(case)
            await session.refresh(result.payment)
            assert response["ok"] is True
            assert response["reference"] == "BANK-250000-2026"
            assert response["case_status"] == CaseStatus.M1_CLOSED
            assert result.payment.status == PaymentStatus.PAID
            assert case.status == CaseStatus.M1_CLOSED

            audits = list(
                (
                    await session.execute(
                        select(AuditLog)
                        .where(AuditLog.entity_type == "case")
                        .where(AuditLog.entity_id == case.id)
                        .order_by(AuditLog.id.asc())
                    )
                ).scalars().all()
            )
            offline_processed = [
                event for event in audits if event.action == "OFFLINE_PAYMENT_PROCESSED"
            ]
            admin_confirmed = [
                event
                for event in audits
                if event.action == "ADMIN_OFFLINE_PAYMENT_CONFIRMED"
            ]
            assert len(offline_processed) == 1
            assert len(admin_confirmed) == 1
            assert offline_processed[0].actor_type == "admin"
            assert offline_processed[0].actor_id == 77
            assert admin_confirmed[0].actor_type == "admin"
            assert admin_confirmed[0].actor_id == 77
            assert (admin_confirmed[0].new_value or {})["reference"] == "BANK-250000-2026"

            notifications = list(
                (
                    await session.execute(
                        select(Notification).where(
                            Notification.case_id == case.id,
                            Notification.event_code == "M1_CLOSED",
                        )
                    )
                ).scalars().all()
            )
            assert len(notifications) == 1

            with pytest.raises(HTTPException) as stale:
                await admin_module.confirm_offline_payment(
                    payment_id=result.payment.id,
                    payload={
                        "expected_status": PaymentStatus.PENDING,
                        "reference": "BANK-RETRY",
                        "comment": "Повторная попытка подтверждения",
                    },
                    db=session,
                    x_admin_token="test-admin-token",
                )
            assert stale.value.status_code == 409


@pytest.mark.parametrize(
    ("code", "status", "provider", "payment_url", "allowed"),
    (
        (PaymentCode.M1_INITIAL_PAYMENT, PaymentStatus.PENDING, None, None, True),
        (PaymentCode.M1_COURT_PAYMENT, PaymentStatus.PENDING, None, None, True),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.WAITING_CONFIRMATION, None, None, True),
        (PaymentCode.M1_SELF_FILING_PACKAGE, PaymentStatus.PENDING, None, None, True),
        (PaymentCode.M1_SELF_FILING_PACKAGE, PaymentStatus.PENDING, "bank_transfer", None, True),
        (PaymentCode.M2_CONSULTATION_PAYMENT, PaymentStatus.PENDING, None, None, True),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.PAID, None, None, False),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.FAILED, None, None, False),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.PENDING, "offline", None, True),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.PENDING, "yookassa", None, False),
        (PaymentCode.M1_SUCCESS_FEE, PaymentStatus.PENDING, None, "https://pay.example", False),
    ),
)
def test_offline_confirmation_eligibility_is_narrow(
    monkeypatch,
    code,
    status,
    provider,
    payment_url,
    allowed,
):
    monkeypatch.setattr(
        admin_module,
        "offline_payment_confirmation_enabled",
        lambda: True,
    )
    payment = Payment(
        case_id=1,
        payment_code=code,
        title="Платёж",
        amount=Decimal("100.00"),
        currency="RUB",
        status=status,
        provider=provider,
        payment_url=payment_url,
        payment_purpose=(
            "для адвоката Гамза Д.Г."
            if provider == "bank_transfer"
            else None
        ),
        payment_details_snapshot=(
            {"recipient": "Адыгейская Республиканская Коллегия Адвокатов"}
            if provider == "bank_transfer"
            else None
        ),
    )

    assert admin_module.payment_can_be_confirmed_offline(payment) is allowed


def test_offline_confirmation_is_hidden_when_online_provider_mode_is_active(monkeypatch):
    monkeypatch.setattr(
        admin_module,
        "offline_payment_confirmation_enabled",
        lambda: False,
    )
    payment = Payment(
        case_id=1,
        payment_code=PaymentCode.M1_SUCCESS_FEE,
        title="Success fee",
        amount=Decimal("25000.00"),
        currency="RUB",
        status=PaymentStatus.PENDING,
    )

    assert admin_module.payment_can_be_confirmed_offline(payment) is False


def test_case_detail_offline_confirmation_has_snapshot_reference_comment_and_cancel():
    source = Path("app/admin/case_detail_page.py").read_text(encoding="utf-8")

    assert "offline_confirm_allowed" in source
    assert "Подтвердить офлайн-поступление" in source
    assert "Банковский / бухгалтерский референс" in source
    assert "Основание подтверждения" in source
    assert "reference.length<3" in source
    assert "comment.length<5" in source
    assert "expected_status:expected" in source
    assert "/confirm-offline" in source
    assert "Поступление не подтверждено" in source
    assert "Отмена" in source
    assert "статусы и суммы" in source.lower()


def test_case_workspace_is_the_single_payment_control_source_for_detailed_card():
    workspace_source = Path("app/api/web_admin.py").read_text(encoding="utf-8")
    detail_source = Path("app/admin/case_detail_page.py").read_text(encoding="utf-8")

    assert "payment_can_be_confirmed_offline" in workspace_source
    assert '"offline_confirm_allowed": payment_can_be_confirmed_offline(payment)' in workspace_source
    assert "mergePaymentControls" not in detail_source

    load_start = detail_source.index("async function load(showLoaded=true)")
    load_end = detail_source.index("async function boot()", load_start)
    load_source = detail_source[load_start:load_end]
    assert "'/admin/case-workspace/'+caseId" in load_source
    assert "'/admin/payments'" not in load_source
