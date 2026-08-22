from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
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


ROOT = Path(__file__).resolve().parents[1]


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


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


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
            user_id = int(user.id)
            case = Case(
                case_number="BOUNDARY-M1-PAY",
                client_id=user_id,
                route=RouteCode.M1.value,
                status=CaseStatus.M1_WAITING_PAYMENT_30000.value,
            )
            db.add(case)
            await db.flush()
            case_id = int(case.id)
            await db.commit()

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


def test_bound_stage_payment_snapshots_provider_result_before_commit():
    source = _read("app/bot/screens/payment_stage_binding_guard.py")
    provider_branch = source.split(
        "payment = await service.create_payment_link(payment)", 1
    )[1].split("except (LookupError, ValueError, RuntimeError)", 1)[0]

    assert "text, markup = _exact_payment_presentation(" in provider_branch
    assert "await db.commit()" in provider_branch
    assert provider_branch.index("text, markup = _exact_payment_presentation(") < provider_branch.index(
        "await db.commit()"
    )
    assert "case_number=case_number" in provider_branch


def test_payment_archive_reconciliation_reloads_after_commit_and_success_fee_snapshots():
    source = _read("app/bot/screens/payment_archive_guard.py")

    reconcile = source.split("async def _reconcile_m2_payment_view", 1)[1].split(
        "async def _render_hold_lost", 1
    )[0]
    assert "await db.commit()" in reconcile
    assert "fresh_payment, fresh_case = await payment_screen.get_owned_payment(" in reconcile
    assert reconcile.index("await db.commit()") < reconcile.index(
        "fresh_payment, fresh_case = await payment_screen.get_owned_payment("
    )
    assert "return fresh_payment, fresh_case, changed" in reconcile

    success_fee = source.split("async def guard_success_fee_stage", 1)[1]
    link_branch = success_fee.split(
        "payment = await service.create_payment_link(payment)", 1
    )[1].split("except (RuntimeError, ValueError)", 1)[0]
    assert "payment_amount = payment.amount" in link_branch
    assert "payment_markup = payment_screen.payment_keyboard(payment)" in link_branch
    assert link_branch.index("payment_amount = payment.amount") < link_branch.index(
        "await db.commit()"
    )
    assert link_branch.index(
        "payment_markup = payment_screen.payment_keyboard(payment)"
    ) < link_branch.index("await db.commit()")


def test_payment_review_notifications_use_business_timezone_formatter():
    source = _read("app/domain/payments/payment_review_service.py")

    assert "from app.presentation_time import format_business_datetime" in source
    assert source.count('"date": format_business_datetime(slot.starts_at)') == 2
    assert 'slot.starts_at.strftime("%d.%m.%Y %H:%M")' not in source
