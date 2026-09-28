from __future__ import annotations

from dataclasses import dataclass

from app.domain.payments.mode import payments_offline


@dataclass(frozen=True)
class OfflinePaymentPresentation:
    next_action: str
    button_label: str
    callback: str = "payments_open"


OFFLINE_M1_PAYMENT_PRESENTATIONS: dict[str, OfflinePaymentPresentation] = {
    "M1_WAITING_PAYMENT_30000": OfflinePaymentPresentation(
        next_action=(
            "Первый платёж ожидает подтверждения командой. После подтверждения "
            "автоматически откроется этап оформления доверенности."
        ),
        button_label="💳 Проверить первый платёж",
    ),
    "M1_WAITING_PAYMENT_70000": OfflinePaymentPresentation(
        next_action=(
            "Второй платёж ожидает подтверждения командой. После подтверждения "
            "автоматически откроется исполнительный этап."
        ),
        button_label="💳 Проверить второй платёж",
    ),
    "M1_SELF_FILING_PAYMENT_PENDING": OfflinePaymentPresentation(
        next_action=(
            "Оплата 15 000 ₽ за подготовку пакета ожидает подтверждения командой. "
            "После подтверждённого поступления денег результат должен быть отправлен в течение 3 календарных дней, "
            "так как полный комплект документов уже принят юристом."
        ),
        button_label="💳 Проверить оплату пакета",
    ),
    "M1_WAITING_SUCCESS_FEE": OfflinePaymentPresentation(
        next_action=(
            "Финальный платёж ожидает подтверждения командой. После подтверждения "
            "финансовый этап завершится и дело закроется автоматически."
        ),
        button_label="💳 Проверить финальный платёж",
    ),
}


def offline_m1_payment_presentation(view) -> OfflinePaymentPresentation | None:
    """Return safe client copy for an M1 obligation in explicit offline mode.

    Provider mode changes how a payment can be completed, not whether the
    payment exists. Client screens therefore point to persisted payment status
    instead of rendering a fresh/pay-again CTA while accounting confirmation is
    pending.
    """

    if not payments_offline():
        return None
    if str(getattr(view, "route", "") or "") != "M1":
        return None
    return OFFLINE_M1_PAYMENT_PRESENTATIONS.get(
        str(getattr(view, "case_status", "") or "")
    )
