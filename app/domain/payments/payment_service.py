from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.cases.m1_recovery_amount import load_recovered_amount
from app.domain.consultations.consultation_intake import (
    ConsultationDescriptionRequired,
    consultation_description_ready,
)
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.consultations.slot_service import SlotUnavailableError
from app.domain.payments.payment_lifecycle import PaymentLifecycleService
from app.domain.payments.payment_types import PaymentCode
from app.domain.payments.providers import get_payment_provider
from app.domain.statuses.case_statuses import CaseStatus, RouteCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.system.settings_service import SettingsService


@dataclass(frozen=True)
class M1SuccessFeeQuote:
    recovered_amount: Decimal
    percent: Decimal
    amount: Decimal


class PaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def amount_for_code(self, code: str):
        settings = SettingsService(self.db)
        if code == PaymentCode.M1_INITIAL_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m1_initial_payment")))
        if code == PaymentCode.M1_COURT_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m1_court_payment")))
        if code == PaymentCode.M1_SELF_FILING_PACKAGE:
            return Decimal(
                str(await settings.get_value("payments.m1_self_filing_package"))
            )
        if code == PaymentCode.M2_CONSULTATION_PAYMENT:
            return Decimal(str(await settings.get_value("payments.m2_consultation_payment")))
        return Decimal("0")

    def title_for_code(self, code: str):
        return {
            PaymentCode.M1_INITIAL_PAYMENT: "Первый платеж М1",
            PaymentCode.M1_COURT_PAYMENT: "Второй платеж М1",
            PaymentCode.M1_SUCCESS_FEE: "Success fee",
            PaymentCode.M1_SELF_FILING_PACKAGE: (
                "Подготовка пакета документов для самостоятельной подачи"
            ),
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

    @staticmethod
    def _money(value: object) -> Decimal:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    async def _received_payment_conflict(
        self,
        *,
        case: Case,
        payment_code: str,
        reservation_key: str | None,
    ) -> Payment | None:
        """Find money states that must be resolved before asking for more money.

        M1 stage payments are one-off, so any already received/protected money for
        the same code blocks a new automatic attempt. M2 may have later paid
        follow-up consultations in the same Case; an old normal PAID reservation
        therefore blocks only the same/legacy-ambiguous reservation. Received
        money still under review/refund resolution blocks every new M2 charge
        until the administrator resolves it.
        """

        protected_statuses = {
            PaymentStatus.PAID,
            PaymentStatus.PAID_REVIEW,
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.REFUND_DECLINED,
        }
        rows = list(
            (
                await self.db.execute(
                    select(Payment)
                    .where(
                        Payment.case_id == case.id,
                        Payment.payment_code == payment_code,
                        Payment.status.in_(tuple(protected_statuses)),
                    )
                    .order_by(Payment.created_at.desc(), Payment.id.desc())
                )
            ).scalars().all()
        )
        for existing in rows:
            if payment_code != PaymentCode.M2_CONSULTATION_PAYMENT:
                return existing
            status = PaymentStatus(str(existing.status))
            if status in {
                PaymentStatus.PAID_REVIEW,
                PaymentStatus.REFUND_PENDING,
                PaymentStatus.REFUND_DECLINED,
            }:
                return existing
            if status == PaymentStatus.PAID and (
                not reservation_key
                or not existing.reservation_key
                or existing.reservation_key == reservation_key
            ):
                return existing
        return None

    async def _restore_m2_slot_selection_after_hold_loss(
        self,
        *,
        case: Case,
        error: Exception,
    ) -> None:
        if (
            str(case.route or "") == RouteCode.M2.value
            and str(case.status) == CaseStatus.M2_PAYMENT_PENDING.value
        ):
            await CaseService(self.db).change_status(
                case=case,
                next_status=CaseStatus.M2_SLOT_PENDING,
                actor_type="system",
                actor_id=None,
                comment=(
                    "Резерв консультации больше недоступен. "
                    f"Возвращён выбор времени: {error}"
                ),
            )

    async def _prepare_consultation_payment_context(self, case: Case) -> str:
        consultation_service = ConsultationService(self.db)
        consultation = await consultation_service.get_current_for_case(case.id)
        if not consultation:
            raise ValueError("Активная консультация не найдена")
        if not consultation_description_ready(consultation):
            raise ConsultationDescriptionRequired(
                "Сначала опишите ситуацию и конкретный вопрос для юриста."
            )
        try:
            slot = await consultation_service.require_payable_slot(consultation)
        except SlotUnavailableError as error:
            await self._restore_m2_slot_selection_after_hold_loss(
                case=case,
                error=error,
            )
            raise
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
            transition = PaymentLifecycleService.transition(
                payment,
                to_status=PaymentStatus.EXPIRED,
            )
            await add_case_history_event(
                self.db,
                actor_type="system",
                actor_id=None,
                case_id=case.id,
                action="CONSULTATION_PAYMENT_LINK_EXPIRED",
                old_value={
                    "payment_id": payment.id,
                    "status": transition.old_status.value,
                    "reservation_key": payment.reservation_key,
                },
                new_value={
                    "status": transition.new_status.value,
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
        """Return one active payment attempt for a case stage under concurrency."""

        case_id = int(case.id)
        locked_case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == case_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise LookupError("Дело не найдено")
        case = locked_case

        reservation_key = None
        if payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:
            reservation_key = await self._prepare_consultation_payment_context(case)
            await self._expire_stale_consultation_payments(
                case=case,
                reservation_key=reservation_key,
            )
            await self.db.flush()

        received_conflict = await self._received_payment_conflict(
            case=case,
            payment_code=payment_code,
            reservation_key=reservation_key,
        )
        if received_conflict is not None:
            raise ValueError(
                "Повторная оплата заблокирована: по этому этапу уже есть полученные деньги "
                f"или незавершённая финансовая сверка (платёж #{received_conflict.id}, "
                f"статус {received_conflict.status}). Сначала завершите сверку/возврат; "
                "клиент не должен платить повторно, пока предыдущие деньги не разобраны."
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
            if amount is not None:
                requested_amount = self._money(amount)
                existing_amount = self._money(payment.amount)
                if existing_amount != requested_amount:
                    raise ValueError(
                        "Существующий активный платёж имеет другую сумму: "
                        f"{existing_amount} ₽ вместо актуальных {requested_amount} ₽. "
                        "Автоматическая оплата заблокирована; требуется проверка платежа."
                    )
            return payment

        final_amount = amount
        if final_amount is None:
            if payment_code == PaymentCode.M1_SUCCESS_FEE:
                final_amount = await self.estimate_success_fee_for_case(case.id)
            else:
                final_amount = await self.amount_for_code(payment_code)

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

    async def success_fee_quote_for_case(self, case_id: int) -> M1SuccessFeeQuote:
        settings = SettingsService(self.db)
        try:
            percent = Decimal(
                str(await settings.get_value("payments.m1_success_fee_percent"))
            )
        except (InvalidOperation, TypeError, ValueError) as error:
            raise ValueError(
                "Ставка success fee настроена некорректно. Оплата заблокирована до проверки настройки."
            ) from error
        if percent <= 0:
            raise ValueError(
                "Ставка success fee должна быть больше нуля. Оплата заблокирована до проверки настройки."
            )

        recovered = await load_recovered_amount(self.db, case_id=case_id)
        if recovered is None:
            raise ValueError(
                "Фактически взысканная сумма не зафиксирована. "
                "Сначала юридическая команда должна подтвердить поступление денег."
            )
        amount = (
            recovered * percent / Decimal("100")
        ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if amount <= 0:
            raise ValueError("Success fee должен быть больше нуля")
        return M1SuccessFeeQuote(
            recovered_amount=recovered,
            percent=percent,
            amount=amount,
        )

    async def estimate_success_fee_for_case(self, case_id: int):
        return (await self.success_fee_quote_for_case(case_id)).amount

    async def create_payment_link(self, payment: Payment):
        if payment.provider == "offline" and payment.provider_payment_id:
            return payment
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
            PaymentLifecycleService.transition(
                payment,
                to_status=PaymentStatus.WAITING_CONFIRMATION,
            )
            await self.db.flush()
        return payment

    async def mark_paid(
        self,
        *,
        payment: Payment,
        case: Case,
        actor_type="system",
        actor_id: int | None = None,
        occurred_at: datetime | None = None,
    ):
        transition = PaymentLifecycleService.transition(
            payment,
            to_status=PaymentStatus.PAID,
            occurred_at=occurred_at,
        )
        if not transition.changed:
            return payment
        await add_case_history_event(
            self.db,
            actor_type=actor_type,
            actor_id=actor_id,
            case_id=case.id,
            action="PAYMENT_PAID",
            old_value={"status": transition.old_status.value},
            new_value={
                "payment_id": payment.id,
                "code": payment.payment_code,
                "amount": str(payment.amount),
                "reservation_key": payment.reservation_key,
                "occurred_at": transition.occurred_at.isoformat(),
            },
        )
        await self.db.flush()
        return payment
