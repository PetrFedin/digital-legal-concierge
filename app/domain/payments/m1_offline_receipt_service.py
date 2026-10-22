"""Record a received bank payment against an existing, provider-free M1 invoice.

This operation does not charge a customer and does not simulate a provider event.
The caller authenticates the administrator and owns the transaction boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import String, cast, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_service import CaseService
from app.domain.notifications.notification_engine import NotificationEngine
from app.domain.payments.mode import payments_disabled
from app.domain.payments.payment_service import PaymentService
from app.domain.payments.payment_types import PaymentCode
from app.domain.retention.content_visibility import (
    private_case_content_available_clause,
    private_case_content_unavailable,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment


class M1OfflineReceiptError(ValueError):
    """The invoice or case no longer permits recording this receipt."""


def offline_receipt_unavailable_reason(payment: Payment, case: Case | None) -> str | None:
    """Shared UI hint and write-side gate; the write also checks row snapshots."""
    if not payments_disabled():
        return "Фиксация поступления доступна только при отключённом платёжном провайдере"
    if (
        payment.provider is not None
        or payment.provider_payment_id is not None
        or payment.payment_url is not None
    ):
        return "Счёт связан с провайдером; поступление должно быть сверено через провайдера"
    if payment.status not in {PaymentStatus.PENDING, PaymentStatus.WAITING_CONFIRMATION}:
        return "Этот счёт уже обработан или не ожидает оплаты. Обновите список платежей"
    if (
        payment.paid_at is not None
        or payment.confirmed_by is not None
        or payment.receipt_reference is not None
        or payment.confirmation_reason is not None
    ):
        return "По этому счёту уже сохранено подтверждение поступления"
    if (
        case is None
        or case.id != payment.case_id
        or case.route != "M1"
        or case.content_deleted_at is not None
    ):
        return "Дело недоступно для фиксации платежа М1"
    if payment.currency != "RUB":
        return "Фиксация доступна только для рублёвого счёта"
    if payment.amount is None or payment.amount <= 0:
        return "У счёта отсутствует положительная сумма"
    if payment.payment_code == PaymentCode.M1_INITIAL_PAYMENT:
        if case.status != CaseStatus.M1_WAITING_PAYMENT_30000 or not case.contract_signed_at:
            return "Первый платёж можно подтвердить после подписания договора на этапе его оплаты"
    elif payment.payment_code == PaymentCode.M1_COURT_PAYMENT:
        if case.status != CaseStatus.M1_WAITING_PAYMENT_70000 or not case.decision_date:
            return "Второй платёж можно подтвердить после решения суда на этапе его оплаты"
    elif payment.payment_code == PaymentCode.M1_SUCCESS_FEE:
        if (
            case.status not in {CaseStatus.M1_MONEY_RECEIVED, CaseStatus.M1_WAITING_SUCCESS_FEE}
            or not case.received_at
            or case.received_amount is None
            or case.received_amount <= 0
        ):
            return "Финальный платёж можно подтвердить только после получения денег клиентом"
        if case.success_fee_amount is None or payment.amount != case.success_fee_amount:
            return "Финальный счёт не совпадает с зафиксированным гонораром по делу"
    else:
        return "Ручная фиксация доступна только для этапных платежей М1"
    return None


class M1OfflineReceiptService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _same_timestamp(self, column, value: datetime | None):
        if value is None:
            return column.is_(None)
        if self.db.get_bind().dialect.name != "sqlite":
            return column == value
        # SQLite stores server NOW() without fractions, while SQLAlchemy binds
        # six fractional digits. Accept only text representations of the exact
        # same timestamp; do not truncate real microseconds with date functions.
        text = value.strftime("%Y-%m-%d %H:%M:%S.%f")
        equivalent = [text]
        while text.endswith("0") and "." in text:
            text = text[:-1]
            if not text.endswith("."):
                equivalent.append(text)
        if text.endswith("."):
            equivalent.append(text[:-1])
        return cast(column, String).in_(equivalent)

    async def _claim_receipt_snapshot(self, payment: Payment, case: Case) -> None:
        """Compare-and-swap both snapshots before writing any financial evidence.

        SQLite ignores SELECT FOR UPDATE. A conditional UPDATE still checks the
        current committed values, including facts whose older updated_at may have
        only second precision. If either claim fails, the caller rolls back both.
        """
        claim_at = datetime.now(timezone.utc)
        for snapshot_at in (payment.updated_at, case.updated_at):
            snapshot_at = (
                snapshot_at.replace(tzinfo=timezone.utc)
                if snapshot_at.tzinfo is None else snapshot_at.astimezone(timezone.utc)
            )
            if claim_at <= snapshot_at:
                claim_at = snapshot_at + timedelta(microseconds=1)
        payment_match = (
            Payment.id == payment.id,
            Payment.case_id == payment.case_id,
            Payment.payment_code == payment.payment_code,
            Payment.status == payment.status,
            self._same_timestamp(Payment.updated_at, payment.updated_at),
            Payment.amount == payment.amount,
            Payment.currency == payment.currency,
            Payment.provider.is_(None),
            Payment.provider_payment_id.is_(None),
            Payment.payment_url.is_(None),
            Payment.paid_at.is_(None),
            Payment.confirmed_by.is_(None),
            Payment.receipt_reference.is_(None),
            Payment.confirmation_reason.is_(None),
        )
        case_match = (
            Case.id == case.id,
            Case.client_id == case.client_id,
            Case.assigned_lawyer_id == case.assigned_lawyer_id,
            Case.status == case.status,
            self._same_timestamp(Case.updated_at, case.updated_at),
            Case.route == case.route,
            self._same_timestamp(Case.closed_at, case.closed_at),
            private_case_content_available_clause(),
            self._same_timestamp(Case.contract_signed_at, case.contract_signed_at),
            self._same_timestamp(Case.decision_date, case.decision_date),
            self._same_timestamp(Case.received_at, case.received_at),
            Case.received_amount == case.received_amount,
            Case.success_fee_amount == case.success_fee_amount,
        )
        try:
            with self.db.no_autoflush:
                payment_claim = await self.db.execute(
                    update(Payment).where(*payment_match).values(updated_at=claim_at)
                    .execution_options(synchronize_session=False)
                )
                if payment_claim.rowcount != 1:
                    raise M1OfflineReceiptError(
                        "Счёт уже изменён или подтверждается другим сотрудником. Обновите экран"
                    )
                case_claim = await self.db.execute(
                    update(Case).where(*case_match).values(updated_at=claim_at)
                    .execution_options(synchronize_session=False)
                )
                if case_claim.rowcount != 1:
                    raise M1OfflineReceiptError(
                        "Дело изменилось при проверке поступления. Обновите экран"
                    )
        except OperationalError as error:
            # With an explicit SQLite read transaction, a concurrent commit can
            # reject the write upgrade instead of returning a zero row count.
            sqlite_code = getattr(error.orig, "sqlite_errorcode", None)
            if (
                self.db.get_bind().dialect.name == "sqlite"
                and sqlite_code is not None
                and (sqlite_code & 0xFF) in {5, 6}  # SQLITE_BUSY / SQLITE_LOCKED
            ):
                raise M1OfflineReceiptError(
                    "Счёт или дело сейчас изменяет другой сотрудник. Обновите экран"
                ) from error
            raise
        set_committed_value(payment, "updated_at", claim_at)
        set_committed_value(case, "updated_at", claim_at)

    async def record_receipt(
        self,
        *,
        payment_id: int,
        actor_id: int,
        amount: Decimal,
        paid_at: datetime,
        reference: str,
        reason: str,
        case_id: int,
        expected_payment_status: str,
        expected_payment_updated_at: str,
        expected_case_status: str,
        expected_case_updated_at: str,
    ) -> tuple[Payment, Case]:
        if not payments_disabled():
            raise M1OfflineReceiptError(
                "Фиксация поступления доступна только при отключённом платёжном провайдере"
            )
        if not actor_id or actor_id <= 0:
            raise M1OfflineReceiptError("Требуется персональная сессия администратора")
        try:
            amount = Decimal(str(amount))
            if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal("0.01")):
                raise ValueError
        except (InvalidOperation, ValueError):
            raise M1OfflineReceiptError("Укажите положительную сумму с точностью до копейки") from None
        if (
            not isinstance(paid_at, datetime)
            or paid_at.tzinfo is None
            or paid_at.utcoffset() is None
            or paid_at > datetime.now(timezone.utc)
        ):
            raise M1OfflineReceiptError("Укажите фактическую дату поступления с часовым поясом, не в будущем")
        paid_at = paid_at.astimezone(timezone.utc)
        reference = str(reference or "").strip()
        reason = str(reason or "").strip()
        if not reference or len(reference) > 255:
            raise M1OfflineReceiptError("Укажите реквизит подтверждения длиной от 1 до 255 символов")
        if not 5 <= len(reason) <= 2000:
            raise M1OfflineReceiptError("Укажите основание длиной от 5 до 2000 символов")
        if not all((
            expected_payment_status, expected_payment_updated_at,
            expected_case_status, expected_case_updated_at,
        )):
            raise M1OfflineReceiptError("Обновите экран: требуется актуальное состояние счёта и дела")

        # Use the same Payment -> Case lock order as the payment webhook path.
        payment = (await self.db.execute(
            select(Payment).where(Payment.id == payment_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if payment is None:
            raise LookupError("Счёт не найден")
        if payment.case_id != case_id:
            raise M1OfflineReceiptError("Счёт относится к другому делу. Обновите экран")
        case = (await self.db.execute(
            select(Case).where(Case.id == payment.case_id)
            .with_for_update().execution_options(populate_existing=True)
        )).scalar_one_or_none()
        if case is None:
            raise LookupError("Дело не найдено")
        if (
            str(payment.status) != str(expected_payment_status)
            or payment.updated_at.isoformat() != expected_payment_updated_at
            or str(case.status) != str(expected_case_status)
            or case.updated_at.isoformat() != expected_case_updated_at
        ):
            raise M1OfflineReceiptError("Счёт или дело изменились после открытия формы. Обновите экран")
        unavailable = offline_receipt_unavailable_reason(payment, case)
        if unavailable:
            raise M1OfflineReceiptError(unavailable)
        if await private_case_content_unavailable(self.db, case=case):
            raise M1OfflineReceiptError("Дело недоступно для фиксации платежа")
        if amount != payment.amount:
            raise M1OfflineReceiptError(
                "Сумма фактического поступления должна совпадать с суммой счёта; "
                "частичная оплата требует отдельной сверки"
            )
        if payment.payment_code == PaymentCode.M1_SUCCESS_FEE:
            calculated = await PaymentService(self.db).estimate_success_fee_for_case(case.id)
            if payment.amount != calculated:
                raise M1OfflineReceiptError(
                    "Сумма финального счёта устарела. Сначала сверьте гонорар с поступлением клиенту"
                )

        old_value = {
            "payment_id": payment.id,
            "payment_status": str(payment.status),
            "payment_updated_at": payment.updated_at.isoformat(),
            "case_status": str(case.status),
            "case_updated_at": case.updated_at.isoformat(),
        }
        await self._claim_receipt_snapshot(payment, case)
        payment.paid_at = paid_at
        payment.confirmed_by = actor_id
        payment.confirmation_reason = reason
        payment.receipt_reference = reference
        # Keep the claimed version through ORM flushes with second-precision NOW().
        claimed_at = payment.updated_at
        await PaymentService(self.db).mark_paid(
            payment=payment, case=case, actor_type="admin", actor_id=actor_id,
        )
        stages = {
            PaymentCode.M1_INITIAL_PAYMENT: (
                CaseStatus.M1_PAYMENT_30000_RECEIVED, CaseStatus.M1_POWER_OF_ATTORNEY,
            ),
            PaymentCode.M1_COURT_PAYMENT: (
                CaseStatus.M1_PAYMENT_70000_RECEIVED, CaseStatus.M1_ENFORCEMENT,
            ),
            PaymentCode.M1_SUCCESS_FEE: (CaseStatus.M1_SUCCESS_FEE_RECEIVED,),
        }
        transitions = stages[payment.payment_code]
        if case.status == CaseStatus.M1_MONEY_RECEIVED:
            transitions = (CaseStatus.M1_WAITING_SUCCESS_FEE, *transitions)
        for status in transitions:
            await CaseService(self.db).change_status(
                case=case, next_status=status, actor_type="admin", actor_id=actor_id,
                comment=f"Поступление оплаты по счёту #{payment.id} проверено администратором",
            )
        payment.updated_at = claimed_at
        case.updated_at = claimed_at
        await add_case_history_event(
            self.db, actor_type="admin", actor_id=actor_id, case_id=case.id,
            action="ADMIN_OFFLINE_PAYMENT_RECEIVED", old_value=old_value,
            new_value={
                "payment_id": payment.id,
                "payment_code": payment.payment_code,
                "status": str(payment.status),
                "case_status": str(case.status),
                "amount": str(amount),
                "currency": payment.currency,
                "paid_at": paid_at.isoformat(),
                "confirmed_by": actor_id,
                "receipt_reference": reference,
                "reason": reason,
                "source": "admin_offline_receipt",
            },
            comment=reason,
        )
        await NotificationEngine(self.db).emit(
            event_code="PAYMENT_PAID", case_id=case.id,
            payload={"case_number": case.case_number, "amount": str(amount)},
            dedupe_key=f"payment:{payment.id}:offline-receipt",
        )
        await self.db.flush()
        return payment, case
