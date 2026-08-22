from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.screens.payments import start_payment
from app.config import settings
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.models import Base
from app.models.case import Case
from app.models.user import User


class _FakeMessage:
    def __init__(self) -> None:
        self.text: str | None = None
        self.reply_markup = None

    async def edit_text(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self

    async def answer(self, text: str, *, reply_markup=None):
        self.text = text
        self.reply_markup = reply_markup
        return self


class _FakeCallback:
    def __init__(self) -> None:
        self.message = _FakeMessage()

    async def answer(self, *args, **kwargs):
        return None


def test_disabled_m1_payment_presentation_never_reads_orm_after_commit(monkeypatch):
    async def scenario() -> None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        # This session deliberately expires ORM state on commit. If the Telegram
        # handler touches Case/Payment attributes after the financial transaction,
        # async SQLAlchemy will try implicit I/O during presentation and the test
        # will fail instead of silently relying on the production session policy.
        session_factory = async_sessionmaker(engine, expire_on_commit=True)

        monkeypatch.setattr(settings, "app_env", "test")
        monkeypatch.setattr(settings, "payment_provider", "disabled")
        monkeypatch.setattr(settings, "demo_mode", False)

        async def fixed_amount(self, code):
            assert code == PaymentCode.M1_INITIAL_PAYMENT
            return Decimal("30000.00")

        monkeypatch.setattr(PaymentService, "amount_for_code", fixed_amount)

        async with session_factory() as db:
            user = User(telegram_id=990000401, full_name="Commit Boundary")
            db.add(user)
            await db.flush()
            case = Case(
                case_number="BOUNDARY-M1-PAY",
                client_id=user.id,
                route=RouteCode.M1.value,
                status=CaseStatus.M1_WAITING_PAYMENT_30000.value,
            )
            db.add(case)
            await db.commit()
            user_id = int(user.id)
            case_id = int(case.id)

        callback = _FakeCallback()
        async with session_factory() as db:
            user = await db.get(User, user_id)
            case = await db.get(Case, case_id)
            assert user is not None
            assert case is not None
            await start_payment(
                callback,
                db,
                PaymentCode.M1_INITIAL_PAYMENT,
                scope=SimpleNamespace(ctx=None, user=user, case=case),
            )

        assert callback.message.text is not None
        assert "BOUNDARY-M1-PAY" in callback.message.text
        assert "30 000,00 ₽" in callback.message.text
        assert "Онлайн-оплата сейчас отключена" in callback.message.text
        await engine.dispose()

    asyncio.run(scenario())
