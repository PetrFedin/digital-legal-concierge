from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.consultations.consultation_intake import (
    ConsultationDescriptionRequired,
    consultation_description_ready,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.providers import get_payment_provider
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.system.settings_service import SettingsService


class PaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def amount_for_code(self, code: str):
        settings = SettingsService(self.db)
        if code == PaymentCode.M1_INITIAL_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m1_initial_payment")))
        if code == PaymentCode.M1_COURT_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m1_court_payment")))
        if code == PaymentCode.M2_CONSULTATION_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m2_consultation_payment")))
        return Decimal("0")

    def title_for_code(self, code: str):
        return {
            PaymentCode.M1_INITIAL_PAYMENT: "Первый платеж М1",
            PaymentCode.M1_COURT_PAYMENT: "Второй платеж М1",
            PaymentCode.M1_SUCCESS_FEE: "Success fee",
            PaymentCode.M2_CONSULTATION_PAYMENT: "Оплата консультации",
        }.get(code, "Платеж")

    async def list_case_payments(self, case_id: int):
        result = await self.db.execute(
            select(Payment)
            .where(Payment.case_id == case_id)
            .order_by(Payment.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_payment(self, payment_id: int):
        result = await self.db.execute(
            select(Payment).where(Payment.id == payment_id)
        )
        return result.scalars().first()

    @staticmethod
    def consultation_reservation_key(consultation_id: int, slot_id: int) -> str:
        return f"consultation:{consultation_id}:slot:{slot_id}"

    async def _prepare_consultation_payment_context(self, case: Case) -> str:
        consultation_service = ConsultationService(self.db)
        consultation = await consultation_service.get_current_for_case(case.id)
        if not consultation:
            raise ValueError("Активная консультация не найдена")
        if not consultation_description_ready(consultation):
            raise ConsultationDescriptionRequired(
                "Сначала опишите ситуацию и конкретный вопрос для юриста."
            )
        slot = await consultation_service.require_payable_slot(consultation)
        return self.consultation_reservation_key(consultation.id, slot.id)

    async def _expire_stale_consultation_payments(
        self,
        *,
        case: Case,
        reservation_key: str,
    ) -> None:
        result = await self.db.execute(
            select(Payment).where(
                Payment.case_id == case.id,
                Payment.payment_code == PaymentCode.M2_CONSULTATION_PAYMENT,
                Payment.status.in_(
                    [
                        PaymentStatus.PENDING,
                        PaymentStatus.WAITING_CONFIRMATION,
                    ]
                ),
                or_(
                    Payment.reservation_key.is_(None),
                    Payment.reservation_key != reservation_key,
                ),
            )
        )
        for payment in result.scalars().all():
            old_status = payment.status
            payment.status = PaymentStatus.EXPIRED
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case.id,
                action="CONSULTATION_PAYMENT_LINK_EXPIRED",
                old_value={
                    "payment_id": payment.id,
                    "status": old_status,
                    "reservation_key": payment.reservation_key,
                },
                new_value={
                    "status": payment.status,
                    "reservation_key": reservation_key,
                },
                comment="Ссылка устарела после изменения или повторного выбора слота",
            )

    async def get_or_create_payment(
        self,
        *,
        case: Case,
        payment_code: str,
        amount: Decimal | None = None,
    ):
        reservation_key = None
        if payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            reservation_key = await self._prepare_consultation_payment_context(case)
            await self._expire_stale_consultation_payments(
                case=case,
                reservation_key=reservation_key,
            )

        query = select(Payment).where(
            Payment.case_id == case.id,
            Payment.payment_code == payment_code,
            Payment.status.in_(
                [
                    PaymentStatus.PENDING,
                    PaymentStatus.WAITING_CONFIRMATION,
                ]
            ),
        )
        if reservation_key is not None:
            query = query.where(Payment.reservation_key == reservation_key)
        payment = (await self.db.execute(query)).scalars().first()
        if payment:
            if payment_code == PaymentCode.M1_SUCCESS_FEE:
                expected_amount = (
                    amount
                    if amount is not None
                    else await self.estimate_success_fee_for_case(case.id)
                )
                if payment.amount != expected_amount:
                    raise ValueError(
                        "Существующий финальный платёж не соответствует "
                        "фактической сумме поступления клиенту"
                    )
                case.success_fee_amount = expected_amount
                await self.db.flush()
            return payment

        final_amount = amount
        if final_amount is None:
            if payment_code == PaymentCode.M1_SUCCESS_FEE:
                final_amount = await self.estimate_success_fee_for_case(case.id)
            else:
                final_amount = await self.amount_for_code(payment_code)

        if payment_code == PaymentCode.M1_SUCCESS_FEE:
            case.success_fee_amount = final_amount

        payment = Payment(
            case_id=case.id,
            payment_code=payment_code,
            title=self.title_for_code(payment_code),
            amount=final_amount,
            currency="RUB",
            status=PaymentStatus.PENDING,
            reservation_key=reservation_key,
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
                "code": payment_code,
                "amount": str(payment.amount),
                "reservation_key": reservation_key,
            },
        )
        return payment

    async def estimate_success_fee_for_case(self, case_id: int):
        settings = SettingsService(self.db)
        percent = Decimal(
            str(await settings.get_value("payments.m1_success_fee_percent"))
        )
        case = await self.db.get(Case, case_id)
        if not case:
            raise ValueError("Дело не найдено")
        base = Decimal(str(case.received_amount or "0"))
        if base <= 0:
            raise ValueError(
                "Финальный платёж нельзя рассчитать без фактической суммы поступления клиенту"
            )
        amount = (base * percent / Decimal("100")).quantize(Decimal("0.01"))
        if amount <= 0:
            raise ValueError("Рассчитанный финальный платёж должен быть больше нуля")
        return amount

    async def create_payment_link(self, payment: Payment):
        if not payment.payment_url:
            provider = get_payment_provider()
            result = await provider.create_payment(
                payment_id=payment.id,
                amount=payment.amount,
                currency=payment.currency,
                title=payment.title,
                metadata={
                    "case_id": payment.case_id,
                    "payment_code": payment.payment_code,
                    "reservation_key": payment.reservation_key or "",
                },
            )
            payment.provider = result.provider
            payment.provider_payment_id = result.provider_payment_id
            payment.payment_url = result.payment_url
            payment.status = PaymentStatus.WAITING_CONFIRMATION
            await self.db.flush()
        return payment

    async def mark_paid(
        self,
        *,
        payment: Payment,
        case: Case,
        actor_type="system",
        actor_id: int | None = None,
    ):
        if payment.status == PaymentStatus.PAID:
            return payment
        old = payment.status
        payment.status = PaymentStatus.PAID
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="PAYMENT_PAID",
            old_value={"status": old},
            new_value={
                "payment_id": payment.id,
                "code": payment.payment_code,
                "amount": str(payment.amount),
                "reservation_key": payment.reservation_key,
            },
        )
        await self.db.flush()
        return payment
