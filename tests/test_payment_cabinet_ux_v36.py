from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.client_case_view import load_client_case_view
from app.bot.screens.my_case import _payment_summary
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models import Base
from app.models.case import Case
from app.models.payment import Payment
from app.models.user import User


async def create_database(tmp_path, name: str):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    return engine, factory


async def create_case(session, *, suffix: int) -> Case:
    user = User(
        telegram_id=997000 + suffix,
        full_name=f"Клиент payment cabinet {suffix}",
    )
    session.add(user)
    await session.flush()
    case = Case(
        case_number=f"PAYMENT-CABINET-{suffix}",
        client_id=user.id,
        route="M2",
        status=CaseStatus.M2_CONSULTATION_BOOKED,
        title="Проверка кабинета оплат",
        next_action="Подготовиться к консультации",
    )
    session.add(case)
    await session.flush()
    return case


def payment_for(case: Case, *, status: PaymentStatus, suffix: str) -> Payment:
    return Payment(
        case_id=case.id,
        payment_code=PaymentCode.M2_CONSULTATION_PAYMENT,
        title="Оплата консультации",
        amount=Decimal("5000.00"),
        currency="RUB",
        status=status,
        provider="yookassa",
        provider_payment_id=f"cabinet-{suffix}",
    )


@pytest.mark.asyncio
async def test_my_case_payment_summary_describes_real_review_and_refund_state(tmp_path):
    engine, factory = await create_database(tmp_path, "payment-cabinet-summary.db")
    async with factory() as session:
        case = await create_case(session, suffix=1)
        assert await _payment_summary(session, case.id) == "Платежей по обращению нет"

        payment = payment_for(
            case,
            status=PaymentStatus.PAID_REVIEW,
            suffix="review",
        )
        session.add(payment)
        await session.commit()
        assert await _payment_summary(session, case.id) == (
            "Есть платёж, который проверяет команда"
        )

        payment.status = PaymentStatus.REFUND_PENDING
        await session.commit()
        assert await _payment_summary(session, case.id) == (
            "Возврат денежных средств обрабатывается"
        )

        payment.status = PaymentStatus.REFUNDED
        await session.commit()
        assert await _payment_summary(session, case.id) == (
            "Платежей в истории: 1 · активных действий по оплате нет"
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_shared_client_case_view_always_reads_persisted_payment_state(tmp_path):
    engine, factory = await create_database(tmp_path, "shared-payment-view.db")
    async with factory() as session:
        case = await create_case(session, suffix=2)
        payment = payment_for(
            case,
            status=PaymentStatus.PAID_REVIEW,
            suffix="shared-review",
        )
        session.add(payment)
        await session.commit()

        view = await load_client_case_view(session, case)
        assert view.payments_summary == "Есть платёж, который проверяет команда"

        payment.status = PaymentStatus.REFUND_PENDING
        await session.commit()
        view = await load_client_case_view(session, case)
        assert view.payments_summary == "Возврат денежных средств обрабатывается"

    await engine.dispose()


def test_shared_client_case_view_does_not_hide_history_by_provider_mode():
    source = Path("app/bot/client_case_view.py").read_text(encoding="utf-8")
    assert "payments_disabled" not in source
    assert "select(Payment)" in source
    assert "Есть платёж, который проверяет команда" in source
    assert "Возврат денежных средств обрабатывается" in source


def test_admin_case_detail_has_contextual_review_and_refund_navigation():
    source = Path("app/admin/case_detail_page.py").read_text(encoding="utf-8")
    assert "Получено — требуется сверка" in source
    assert "Возврат обрабатывается" in source
    assert "Открыть сверку этого платежа" in source
    assert 'href="/admin/payment-reviews/ui"' in source
    assert "Открыть очередь возвратов" in source
    assert 'href="/admin/refunds/ui"' in source


def test_payment_notifications_do_not_claim_booking_was_cancelled_by_refund():
    source = Path(
        "app/domain/notifications/notification_templates.py"
    ).read_text(encoding="utf-8")
    assert "автоматическое применение остановлено для безопасной сверки" in source
    assert "Возврат обрабатывается отдельно от статуса консультации" in source
    assert "актуальную запись всегда проверяйте в «Моё дело»" in source
