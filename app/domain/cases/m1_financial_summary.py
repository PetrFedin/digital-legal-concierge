from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.m1_recovery_amount import load_recovered_amount
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.case import Case
from app.models.payment import Payment
from app.system.settings_service import SettingsService

RUB_CENT = Decimal("0.01")
ACTIVE_PAYMENT_STATUSES = {
    PaymentStatus.PENDING,
    PaymentStatus.WAITING_CONFIRMATION,
}
SUCCESS_FEE_PHASE_STATUSES = {
    CaseStatus.M1_MONEY_RECEIVED,
    CaseStatus.M1_WAITING_SUCCESS_FEE,
    CaseStatus.M1_SUCCESS_FEE_RECEIVED,
    CaseStatus.M1_CLOSED,
}
SEVERITY_ORDER = {"ok": 0, "warning": 1, "critical": 2}


def _money(value: object) -> Decimal:
    return Decimal(str(value)).quantize(RUB_CENT, rounding=ROUND_HALF_UP)


def _payment_payload(payment: Payment | None) -> dict[str, object] | None:
    if payment is None:
        return None
    return {
        "id": payment.id,
        "amount": str(_money(payment.amount)),
        "currency": payment.currency,
        "status": payment.status,
        "provider": payment.provider,
        "provider_payment_id": payment.provider_payment_id,
        "has_payment_url": bool(payment.payment_url),
        "created_at": payment.created_at.isoformat() if payment.created_at else None,
        "updated_at": payment.updated_at.isoformat() if payment.updated_at else None,
    }


def _step(code: str, title: str, state: str, detail: str) -> dict[str, str]:
    return {"code": code, "title": title, "state": state, "detail": detail}


class M1FinancialSummaryService:
    """Read-only source of truth for the final M1 money → fee → close chain."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def build(self, case: Case) -> dict[str, object]:
        if str(case.route or "") != "M1":
            return {
                "applicable": False,
                "health": "ok",
                "diagnostics": [],
                "recommended_action": "Для этого маршрута финансовый финал M1 не применяется.",
                "steps": [],
            }

        diagnostics: list[dict[str, str]] = []

        def add_diagnostic(code: str, severity: str, message: str) -> None:
            diagnostics.append(
                {"code": code, "severity": severity, "message": message}
            )

        percent: Decimal | None = None
        try:
            percent = Decimal(
                str(
                    await SettingsService(self.db).get_value(
                        "payments.m1_success_fee_percent"
                    )
                )
            )
            if percent <= 0:
                raise ValueError
        except (InvalidOperation, TypeError, ValueError):
            add_diagnostic(
                "SUCCESS_FEE_PERCENT_INVALID",
                "critical",
                "Ставка success fee не задана корректно. Финальный платёж нельзя считать автоматически.",
            )

        recovered: Decimal | None = None
        recovery_history_valid = True
        try:
            recovered = await load_recovered_amount(self.db, case_id=case.id)
        except ValueError as error:
            recovery_history_valid = False
            add_diagnostic(
                "RECOVERY_AUDIT_INVALID",
                "critical",
                f"Запись о фактическом взыскании повреждена: {error}",
            )

        expected_fee: Decimal | None = None
        if recovered is not None and percent is not None:
            expected_fee = _money(recovered * percent / Decimal("100"))
            if expected_fee <= 0:
                add_diagnostic(
                    "SUCCESS_FEE_NOT_POSITIVE",
                    "critical",
                    "Рассчитанный success fee должен быть больше нуля.",
                )
                expected_fee = None

        payments = list(
            (
                await self.db.execute(
                    select(Payment)
                    .where(Payment.case_id == case.id)
                    .where(Payment.payment_code == PaymentCode.M1_SUCCESS_FEE)
                    .order_by(Payment.created_at.desc(), Payment.id.desc())
                )
            ).scalars().all()
        )
        latest_payment = payments[0] if payments else None
        active_payments = [
            payment
            for payment in payments
            if payment.status in ACTIVE_PAYMENT_STATUSES
        ]
        paid_payments = [
            payment for payment in payments if payment.status == PaymentStatus.PAID
        ]

        status = str(case.status or "")
        in_fee_phase = status in {str(item) for item in SUCCESS_FEE_PHASE_STATUSES}

        if payments and recovered is None and recovery_history_valid:
            add_diagnostic(
                "PAYMENT_WITHOUT_RECOVERY",
                "critical",
                "Есть платёж success fee, но в неизменяемой истории нет фактически взысканной суммы.",
            )
        if recovered is not None and status == CaseStatus.M1_ENFORCEMENT:
            add_diagnostic(
                "RECOVERY_WITHOUT_STATE_ADVANCE",
                "critical",
                "Фактическое взыскание записано, но дело осталось на исполнительном производстве.",
            )
        if in_fee_phase and recovered is None and recovery_history_valid:
            add_diagnostic(
                "RECOVERY_REQUIRED_FOR_CURRENT_STATE",
                "critical",
                "Текущий этап требует зафиксированной фактически взысканной суммы, но она не найдена.",
            )
        if status == CaseStatus.M1_WAITING_SUCCESS_FEE and not payments:
            add_diagnostic(
                "WAITING_WITHOUT_PAYMENT",
                "critical",
                "Дело ожидает success fee, но платёжная запись отсутствует.",
            )
        if len(active_payments) > 1:
            add_diagnostic(
                "MULTIPLE_ACTIVE_SUCCESS_FEE_PAYMENTS",
                "critical",
                "Обнаружено несколько активных платежей success fee. Не создавайте новый платёж до проверки финансового контура.",
            )
        if expected_fee is not None:
            mismatched = [
                payment
                for payment in payments
                if payment.status
                in {
                    PaymentStatus.PENDING,
                    PaymentStatus.WAITING_CONFIRMATION,
                    PaymentStatus.PAID,
                    PaymentStatus.PAID_REVIEW,
                }
                and _money(payment.amount) != expected_fee
            ]
            if mismatched:
                add_diagnostic(
                    "SUCCESS_FEE_AMOUNT_MISMATCH",
                    "critical",
                    "Сумма существующего success fee не совпадает с расчётом от фактически взысканной суммы.",
                )
        if status == CaseStatus.M1_CLOSED and not paid_payments:
            add_diagnostic(
                "CLOSED_WITHOUT_PAID_SUCCESS_FEE",
                "critical",
                "Дело закрыто, но подтверждённый оплаченный success fee не найден.",
            )
        if paid_payments and status not in {
            CaseStatus.M1_SUCCESS_FEE_RECEIVED,
            CaseStatus.M1_CLOSED,
        }:
            add_diagnostic(
                "PAID_WITHOUT_CASE_CLOSE",
                "critical",
                "Success fee оплачен, но статус дела не завершил webhook-переход. Требуется проверка обработки платежа.",
            )
        if (
            status == CaseStatus.M1_WAITING_SUCCESS_FEE
            and latest_payment is not None
            and not active_payments
            and not paid_payments
        ):
            add_diagnostic(
                "SUCCESS_FEE_REOPEN_REQUIRED",
                "warning",
                "Последний success fee неактивен. Клиенту нужно повторно открыть оплату через штатную кнопку; статус дела вручную не меняйте.",
            )

        health = "ok"
        for diagnostic in diagnostics:
            if SEVERITY_ORDER[diagnostic["severity"]] > SEVERITY_ORDER[health]:
                health = diagnostic["severity"]

        if health == "critical":
            recommended_action = (
                "Не меняйте статус или сумму вручную. Проверьте аудит взыскания и платёжный контур; "
                "после устранения причины повторите штатное действие."
            )
        elif status == CaseStatus.M1_ENFORCEMENT and recovered is None:
            recommended_action = (
                "Назначенный юрист должен зафиксировать только фактически поступившую клиенту сумму."
            )
        elif status == CaseStatus.M1_WAITING_SUCCESS_FEE:
            if active_payments:
                recommended_action = (
                    "Ожидаем финальную оплату клиента. После подтверждённого webhook дело закроется автоматически; "
                    "статус вручную не меняйте."
                )
            else:
                recommended_action = (
                    "Клиенту нужно повторно открыть success fee через штатную Telegram-кнопку."
                )
        elif status == CaseStatus.M1_CLOSED and health == "ok":
            recommended_action = "Финансовый контур завершён. Дело доступно только для чтения и аудита."
        elif recovered is not None:
            recommended_action = "Проверьте следующий штатный этап M1; финансовые данные согласованы."
        else:
            recommended_action = "Финальный финансовый этап ещё не наступил."

        recovery_complete = recovered is not None
        fee_complete = expected_fee is not None
        payment_complete = bool(paid_payments)
        closed_complete = status == CaseStatus.M1_CLOSED

        steps = [
            _step(
                "recovery",
                "Фактически взыскано",
                "complete" if recovery_complete else ("blocked" if health == "critical" and in_fee_phase else "current"),
                f"{recovered} ₽" if recovered is not None else "Сумма ещё не зафиксирована",
            ),
            _step(
                "fee",
                "Success fee рассчитан",
                "complete" if fee_complete else ("blocked" if recovery_complete else "pending"),
                (
                    f"{expected_fee} ₽ · ставка {percent}%"
                    if expected_fee is not None and percent is not None
                    else "Ожидает корректной суммы взыскания и ставки"
                ),
            ),
            _step(
                "payment",
                "Финальный платёж",
                "complete" if payment_complete else ("current" if payments else "pending"),
                (
                    f"{_money(latest_payment.amount)} ₽ · {latest_payment.status}"
                    if latest_payment is not None
                    else "Платёж ещё не создан"
                ),
            ),
            _step(
                "closed",
                "Дело закрыто",
                "complete" if closed_complete else ("current" if payment_complete else "pending"),
                "M1 завершён" if closed_complete else "Закроется автоматически после подтверждённой оплаты",
            ),
        ]

        return {
            "applicable": True,
            "case_id": case.id,
            "case_status": case.status,
            "health": health,
            "recovered_amount": str(recovered) if recovered is not None else None,
            "success_fee_percent": str(percent) if percent is not None else None,
            "expected_success_fee": str(expected_fee) if expected_fee is not None else None,
            "latest_payment": _payment_payload(latest_payment),
            "active_payment_count": len(active_payments),
            "paid_payment_count": len(paid_payments),
            "diagnostics": diagnostics,
            "recommended_action": recommended_action,
            "steps": steps,
        }
