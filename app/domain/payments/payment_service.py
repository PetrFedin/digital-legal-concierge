from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.providers import get_payment_provider
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.system.settings_service import SettingsService


class PaymentIntegrityError(RuntimeError):
    """A payment operation cannot continue without risking duplication."""


class PaymentService:
    OPEN_STATUSES = frozenset(
        {
            PaymentStatus.PENDING.value,
            PaymentStatus.WAITING_CONFIRMATION.value,
        }
    )

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _value(value) -> str:
        return value.value if hasattr(value, "value") else str(value or "")

    async def amount_for_code(self, code: str | PaymentCode) -> Decimal:
        normalized = self._value(code)
        settings = SettingsService(self.db)
        setting_by_code = {
            PaymentCode.M1_INITIAL_PAYMENT.value: "payments.m1_initial_payment",
            PaymentCode.M1_COURT_PAYMENT.value: "payments.m1_court_payment",
            PaymentCode.M2_CONSULTATION_PAYMENT.value: (
                "payments.m2_consultation_payment"
            ),
        }
        setting_key = setting_by_code.get(normalized)
        if setting_key is None:
            return Decimal("0.00")
        return Decimal(str(await settings.get_value(setting_key))).quantize(
            Decimal("0.01")
        )

    def title_for_code(self, code: str | PaymentCode) -> str:
        normalized = self._value(code)
        return {
            PaymentCode.M1_INITIAL_PAYMENT.value: "Первый платеж М1",
            PaymentCode.M1_COURT_PAYMENT.value: "Второй платеж М1",
            PaymentCode.M1_SUCCESS_FEE.value: "Success fee",
            PaymentCode.M2_CONSULTATION_PAYMENT.value: "Оплата консультации",
        }.get(normalized, "Платеж")

    async def list_case_payments(self, case_id: int) -> list[Payment]:
        result = await self.db.execute(
            select(Payment)
            .where(Payment.case_id == case_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
        )
        return list(result.scalars().all())

    async def get_payment(self, payment_id: int) -> Payment | None:
        result = await self.db.execute(
            select(Payment).where(Payment.id == payment_id)
        )
        return result.scalars().first()

    async def get_or_create_payment(
        self,
        *,
        case: Case,
        payment_code: str | PaymentCode,
        amount: Decimal | None = None,
    ) -> Payment:
        if case is None:
            raise PaymentIntegrityError("Дело для оплаты не найдено.")

        # Serialize all payment creation for one case. This prevents two rapid
        # Telegram callbacks or API requests from both observing an empty set
        # and creating separate payable links.
        locked_case_id = (
            await self.db.execute(
                select(Case.id).where(Case.id == case.id).with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case_id is None:
            raise PaymentIntegrityError("Дело для оплаты не найдено.")

        normalized_code = self._value(payment_code)
        open_payments = list(
            (
                await self.db.execute(
                    select(Payment)
                    .where(
                        Payment.case_id == case.id,
                        Payment.payment_code == normalized_code,
                        Payment.status.in_(self.OPEN_STATUSES),
                    )
                    .order_by(Payment.id.asc())
                    .limit(2)
                    .with_for_update()
                )
            )
            .scalars()
            .all()
        )
        if len(open_payments) > 1:
            raise PaymentIntegrityError(
                "Для этапа найдено несколько открытых платежей. "
                "Повторная оплата остановлена до проверки сотрудником."
            )
        if open_payments:
            return open_payments[0]

        final_amount = amount
        if final_amount is None:
            if normalized_code == PaymentCode.M1_SUCCESS_FEE.value:
                final_amount = await self.estimate_success_fee_for_case(case.id)
            else:
                final_amount = await self.amount_for_code(normalized_code)
        final_amount = Decimal(str(final_amount)).quantize(Decimal("0.01"))
        if final_amount <= 0:
            raise PaymentIntegrityError(
                "Сумма платежа не настроена или рассчитана некорректно."
            )

        payment = Payment(
            case_id=case.id,
            payment_code=normalized_code,
            title=self.title_for_code(normalized_code),
            amount=final_amount,
            currency="RUB",
            status=PaymentStatus.PENDING.value,
        )
        self.db.add(payment)
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="system",
            actor_id=None,
            case_id=case.id,
            action="PAYMENT_CREATED",
            new_value={
                "payment_id": payment.id,
                "code": normalized_code,
                "amount": str(payment.amount),
            },
        )
        return payment

    async def estimate_success_fee_for_case(self, case_id: int) -> Decimal:
        from app.models.calculation import Calculation

        settings = SettingsService(self.db)
        percent = Decimal(
            str(await settings.get_value("payments.m1_success_fee_percent"))
        )
        result = await self.db.execute(
            select(Calculation).where(Calculation.case_id == case_id)
        )
        calculation = result.scalars().first()
        base = (
            calculation.penalty_amount
            if calculation and calculation.penalty_amount
            else Decimal("0")
        )
        amount = (base * percent / Decimal("100")).quantize(Decimal("0.01"))
        return amount if amount > 0 else Decimal("1.00")

    async def create_payment_link(self, payment: Payment) -> Payment:
        if payment is None:
            raise PaymentIntegrityError("Платёж не найден.")
        if payment.payment_url:
            return payment

        status = self._value(payment.status)
        if status != PaymentStatus.PENDING.value:
            raise PaymentIntegrityError(
                "Платёж находится в состоянии, которое не допускает создание ссылки."
            )
        if payment.provider or payment.provider_payment_id:
            raise PaymentIntegrityError(
                "Провайдер уже начал создание платежа, но ссылка отсутствует. "
                "Требуется проверка сотрудником."
            )

        provider = get_payment_provider()
        result = await provider.create_payment(
            payment_id=payment.id,
            amount=payment.amount,
            currency=payment.currency,
            title=payment.title,
            metadata={
                "case_id": payment.case_id,
                "payment_code": payment.payment_code,
            },
        )
        payment.provider = result.provider
        payment.provider_payment_id = result.provider_payment_id
        payment.payment_url = result.payment_url
        payment.status = PaymentStatus.WAITING_CONFIRMATION.value
        await self.db.flush()
        return payment

    async def mark_paid(
        self,
        *,
        payment: Payment,
        case: Case,
        actor_type: str = "system",
        actor_id: int | None = None,
    ) -> Payment:
        if payment is None or case is None or payment.case_id != case.id:
            raise PaymentIntegrityError("Платёж не принадлежит указанному делу.")
        if self._value(payment.status) == PaymentStatus.PAID.value:
            return payment

        old_status = self._value(payment.status)
        payment.status = PaymentStatus.PAID.value
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="PAYMENT_PAID",
            old_value={"status": old_status},
            new_value={
                "payment_id": payment.id,
                "code": payment.payment_code,
                "amount": str(payment.amount),
            },
        )
        await self.db.flush()
        return payment
