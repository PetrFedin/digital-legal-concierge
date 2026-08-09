from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.cases.m1_enforcement_service import M1EnforcementService
from app.domain.cases.m1_financial_summary import M1FinancialSummaryService
from app.domain.payments.payment_webhook_service import PaymentWebhookService
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Base
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.user import User


@asynccontextmanager
async def database(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'm1-financial-summary-v36.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        yield factory
    finally:
        await engine.dispose()


async def seed_case(session, *, status=CaseStatus.M1_ENFORCEMENT):
    user = User(telegram_id=983202, full_name="Клиент Финальный Контур")
    lawyer = Lawyer(full_name="Юрист Финальный Контур", is_active=True)
    session.add_all([user, lawyer])
    await session.flush()
    case = Case(
        case_number="M1-FINANCIAL-V36",
        client_id=user.id,
        route="M1",
        status=status,
        title="Финальный финансовый контур",
        assigned_lawyer_id=lawyer.id,
    )
    session.add(case)
    await session.flush()
    return case, lawyer


@pytest.mark.asyncio
async def test_closed_paid_case_has_five_green_financial_steps(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            case, lawyer = await seed_case(session)
            result = await M1EnforcementService(session).record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="250000.00",
                comment="Деньги фактически поступили клиенту",
            )
            await PaymentWebhookService(session).process_successful_payment(
                payment=result.payment,
                case=case,
                provider_payload={"event": "payment.succeeded"},
            )
            await session.commit()

            summary = await M1FinancialSummaryService(session).build(case)

            assert summary["applicable"] is True
            assert summary["health"] == "ok"
            assert Decimal(summary["recovered_amount"]) == Decimal("250000.00")
            assert Decimal(summary["success_fee_percent"]) == Decimal("10")
            assert Decimal(summary["expected_success_fee"]) == Decimal("25000.00")
            assert Decimal(summary["latest_payment"]["amount"]) == Decimal("25000.00")
            assert summary["latest_payment"]["status"] == "PAID"
            assert summary["diagnostics"] == []
            assert [step["code"] for step in summary["steps"]] == [
                "recovery",
                "fee",
                "payment_created",
                "payment_confirmed",
                "closed",
            ]
            assert all(step["state"] == "complete" for step in summary["steps"])
            assert "только для чтения" in summary["recommended_action"]


@pytest.mark.asyncio
async def test_wrong_success_fee_amount_is_fail_closed_in_financial_summary(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            case, lawyer = await seed_case(session)
            result = await M1EnforcementService(session).record_money_received(
                case=case,
                lawyer_id=lawyer.id,
                amount="250000.00",
                comment="Зафиксировано взыскание",
            )
            result.payment.amount = Decimal("20000.00")
            await session.commit()

            summary = await M1FinancialSummaryService(session).build(case)
            codes = {item["code"] for item in summary["diagnostics"]}

            assert summary["health"] == "critical"
            assert Decimal(summary["expected_success_fee"]) == Decimal("25000.00")
            assert Decimal(summary["latest_payment"]["amount"]) == Decimal("20000.00")
            assert "SUCCESS_FEE_AMOUNT_MISMATCH" in codes
            assert "Не меняйте статус или сумму вручную" in summary["recommended_action"]


@pytest.mark.asyncio
async def test_waiting_success_fee_without_recovery_or_payment_is_explicitly_broken(tmp_path):
    async with database(tmp_path) as factory:
        async with factory() as session:
            case, _lawyer = await seed_case(
                session,
                status=CaseStatus.M1_WAITING_SUCCESS_FEE,
            )
            await session.commit()

            summary = await M1FinancialSummaryService(session).build(case)
            codes = {item["code"] for item in summary["diagnostics"]}

            assert summary["health"] == "critical"
            assert summary["recovered_amount"] is None
            assert summary["latest_payment"] is None
            assert "RECOVERY_REQUIRED_FOR_CURRENT_STATE" in codes
            assert "WAITING_WITHOUT_PAYMENT" in codes
            assert summary["steps"][0]["state"] == "blocked"
            assert summary["steps"][3]["state"] == "pending"


def test_canonical_admin_workspace_and_case_page_render_financial_final():
    workspace_source = Path("app/api/web_admin.py").read_text(encoding="utf-8")
    page_source = Path("app/admin/case_detail_page.py").read_text(encoding="utf-8")

    assert "M1FinancialSummaryService" in workspace_source
    assert '"financial_final": financial_final' in workspace_source
    assert '"payment_code": payment.payment_code' in workspace_source
    assert '"currency": payment.currency' in workspace_source

    for label in (
        "Фактически взыскано",
        "Success fee",
        "Платёж создан",
        "Оплата подтверждена",
        "Дело закрыто",
    ):
        assert label in page_source
    assert "Финансовый финал M1" in page_source
    assert "Этот блок только для контроля" in page_source
    assert "не исправляются отсюда вручную" in page_source
    assert "financialFinal(d.financial_final)" in page_source
