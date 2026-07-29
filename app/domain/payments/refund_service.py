from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.audit_log import AuditLog
from app.models.case import Case
from app.models.payment import Payment


class PaymentRefundError(RuntimeError):
    """An external refund cannot be recorded safely."""


class PaymentRefundService:
    ACTION = "PAYMENT_REFUND_CONFIRMED_EXTERNALLY"

    def __init__(self, db: AsyncSession):
        self.db = db

    async def confirm_external_refund(
        self,
        *,
        payment: Payment,
        case: Case,
        actor_id: int,
        refund_reference: str,
        comment: str,
        amount: Decimal | str | None = None,
        source: str = "admin_api",
    ) -> AuditLog:
        if payment is None or case is None or payment.case_id != case.id:
            raise PaymentRefundError("Платёж не принадлежит указанному делу.")

        reference = " ".join(str(refund_reference or "").split())
        normalized_comment = " ".join(str(comment or "").split())
        if not reference:
            raise PaymentRefundError("Для возврата требуется reference провайдера.")
        if len(reference) > 255:
            raise PaymentRefundError("Reference возврата слишком длинный.")
        if not normalized_comment:
            raise PaymentRefundError("Для возврата требуется комментарий сотрудника.")

        locked_case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == case.id)
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_case is None:
            raise PaymentRefundError("Дело для возврата не найдено.")

        locked_payment = (
            await self.db.execute(
                select(Payment)
                .where(
                    Payment.id == payment.id,
                    Payment.case_id == locked_case.id,
                )
                .execution_options(populate_existing=True)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if locked_payment is None:
            raise PaymentRefundError("Платёж для возврата не найден.")

        existing = (
            await self.db.execute(
                select(AuditLog)
                .where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == locked_case.id,
                    AuditLog.action == self.ACTION,
                )
                .order_by(AuditLog.id.asc())
            )
        ).scalars().all()
        matching = next(
            (
                event
                for event in existing
                if (event.new_value or {}).get("payment_id") == locked_payment.id
            ),
            None,
        )
        if matching is not None:
            recorded_reference = str(
                (matching.new_value or {}).get("refund_reference") or ""
            )
            if recorded_reference != reference:
                raise PaymentRefundError(
                    "Для платежа уже зафиксирован другой reference возврата."
                )
            return matching

        if locked_payment.status != PaymentStatus.PAID.value:
            raise PaymentRefundError(
                "Возврат можно подтвердить только для платежа в статусе PAID."
            )

        try:
            refund_amount = (
                Decimal(str(amount))
                if amount is not None
                else Decimal(str(locked_payment.amount))
            ).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise PaymentRefundError("Некорректная сумма возврата.") from exc
        paid_amount = Decimal(str(locked_payment.amount)).quantize(Decimal("0.01"))
        if refund_amount != paid_amount:
            raise PaymentRefundError(
                "Частичный возврат не поддерживается: сумма должна полностью "
                "совпадать с подтверждённой оплатой."
            )

        old_status = locked_payment.status
        locked_payment.status = PaymentStatus.REFUNDED.value
        locked_payment.payment_url = None
        event = await add_case_history_event(
            self.db,
            actor_type="admin",
            actor_id=actor_id,
            case_id=locked_case.id,
            action=self.ACTION,
            old_value={
                "payment_id": locked_payment.id,
                "payment_status": old_status,
            },
            new_value={
                "payment_id": locked_payment.id,
                "payment_code": locked_payment.payment_code,
                "payment_status": locked_payment.status,
                "amount": str(refund_amount),
                "currency": locked_payment.currency,
                "refund_reference": reference,
                "source": source,
            },
            comment=normalized_comment,
        )
        await self.db.flush()
        return event
