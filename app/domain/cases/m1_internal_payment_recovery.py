from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment


class M1InternalPaymentRecoveryError(ValueError):
    pass


@dataclass(frozen=True)
class RecoveryPlan:
    source: CaseStatus
    payment_code: PaymentCode
    target: CaseStatus
    title: str


RECOVERY_PLANS: dict[CaseStatus, RecoveryPlan] = {
    CaseStatus.M1_PAYMENT_30000_RECEIVED: RecoveryPlan(
        source=CaseStatus.M1_PAYMENT_30000_RECEIVED,
        payment_code=PaymentCode.M1_INITIAL_PAYMENT,
        target=CaseStatus.M1_POWER_OF_ATTORNEY,
        title="Открыть этап доверенности после подтверждённого первого платежа",
    ),
    CaseStatus.M1_PAYMENT_70000_RECEIVED: RecoveryPlan(
        source=CaseStatus.M1_PAYMENT_70000_RECEIVED,
        payment_code=PaymentCode.M1_COURT_PAYMENT,
        target=CaseStatus.M1_ENFORCEMENT,
        title="Открыть исполнение после подтверждённого второго платежа",
    ),
    CaseStatus.M1_SUCCESS_FEE_RECEIVED: RecoveryPlan(
        source=CaseStatus.M1_SUCCESS_FEE_RECEIVED,
        payment_code=PaymentCode.M1_SUCCESS_FEE,
        target=CaseStatus.M1_CLOSED,
        title="Закрыть дело после подтверждённого success fee",
    ),
}


class M1InternalPaymentRecoveryService:
    """Repair only transient M1 statuses whose exact payment is already PAID.

    The normal payment service performs source -> transient -> target in one DB
    transaction, so a persisted transient status is a historical/integrity
    anomaly. Recovery never accepts a target from the caller and cannot be used
    to manufacture a payment milestone.
    """

    def __init__(self, db: AsyncSession):
        self.db = db
        self.cases = CaseService(db)

    @staticmethod
    def plan_for_status(value: object) -> RecoveryPlan | None:
        try:
            status = value if isinstance(value, CaseStatus) else CaseStatus(str(value))
        except (TypeError, ValueError):
            return None
        return RECOVERY_PLANS.get(status)

    async def _lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        return case

    async def _lock_paid_payment(self, *, case_id: int, code: PaymentCode) -> Payment:
        payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == int(case_id),
                    Payment.payment_code == code,
                    Payment.status == PaymentStatus.PAID,
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .limit(1)
                .with_for_update()
            )
        ).scalars().first()
        if payment is None:
            raise M1InternalPaymentRecoveryError(
                "Подтверждённый PAID-платёж нужного этапа не найден. "
                "Автоматическое восстановление заблокировано."
            )
        return payment

    async def inspect(self, *, case_id: int) -> tuple[Case, RecoveryPlan, Payment]:
        case = await self.db.get(Case, int(case_id))
        if case is None:
            raise LookupError("Дело не найдено")
        if str(case.route or "") != "M1":
            raise M1InternalPaymentRecoveryError("Восстановление доступно только для M1")
        plan = self.plan_for_status(case.status)
        if plan is None:
            raise M1InternalPaymentRecoveryError(
                "Текущее состояние не является восстанавливаемым внутренним платёжным этапом"
            )
        payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.case_id == case.id,
                    Payment.payment_code == plan.payment_code,
                    Payment.status == PaymentStatus.PAID,
                )
                .order_by(Payment.created_at.desc(), Payment.id.desc())
                .limit(1)
            )
        ).scalars().first()
        if payment is None:
            raise M1InternalPaymentRecoveryError(
                "Подтверждённый PAID-платёж нужного этапа не найден"
            )
        return case, plan, payment

    async def recover(
        self,
        *,
        case_id: int,
        admin_id: int,
        comment: str,
    ) -> tuple[Case, RecoveryPlan, Payment]:
        clean_comment = str(comment or "").strip()
        if len(clean_comment) < 10:
            raise M1InternalPaymentRecoveryError(
                "Опишите основание восстановления минимум в 10 символах"
            )

        case = await self._lock_case(case_id)
        if str(case.route or "") != "M1":
            raise M1InternalPaymentRecoveryError("Восстановление доступно только для M1")
        plan = self.plan_for_status(case.status)
        if plan is None:
            raise M1InternalPaymentRecoveryError(
                "Этап дела уже изменился или не поддерживает это восстановление"
            )
        payment = await self._lock_paid_payment(
            case_id=case.id,
            code=plan.payment_code,
        )

        old_status = str(case.status)
        await self.cases.change_status(
            case=case,
            next_status=plan.target,
            actor_type="admin",
            actor_id=int(admin_id),
            comment=(
                "Восстановлен зависший внутренний платёжный этап на основании "
                f"PAID-платежа #{payment.id}. {clean_comment}"
            ),
        )
        await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=int(admin_id),
            case_id=case.id,
            action="M1_INTERNAL_PAYMENT_STAGE_RECOVERED",
            old_value={
                "case_status": old_status,
                "payment_id": payment.id,
                "payment_code": str(payment.payment_code),
                "payment_status": str(payment.status),
            },
            new_value={
                "case_status": str(case.status),
                "payment_id": payment.id,
                "payment_code": str(payment.payment_code),
                "payment_status": str(payment.status),
            },
            comment=clean_comment,
        )
        await self.db.flush()
        return case, plan, payment


__all__ = [
    "M1InternalPaymentRecoveryError",
    "M1InternalPaymentRecoveryService",
    "RECOVERY_PLANS",
    "RecoveryPlan",
]
