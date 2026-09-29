from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal


@dataclass(frozen=True)
class BankRequisites:
    recipient: str
    advocate: str
    inn: str
    kpp: str
    ogrn: str
    settlement_account: str
    correspondent_account: str
    bank: str
    bik: str
    mandatory_purpose: str


# Customer-provided payment contract, 28 Sep 2026.
# Money is paid only to the bar association bank account. The payment purpose
# must contain the exact advocate marker below; the client must not be sent to a
# card, personal account, YooKassa, Robokassa or an invented SBP flow.
GAMZA_COLLEGIUM_REQUISITES = BankRequisites(
    recipient="Адыгейская Республиканская Коллегия Адвокатов",
    advocate="Гамза Денис Георгиевич",
    inn="0105040071",
    kpp="010501001",
    ogrn="1030100534331",
    settlement_account="40703810201000102939",
    correspondent_account="30101810600000000602",
    bank='Юго-Западный банк ПАО «СБЕРБАНК РОССИИ», г. Ростов-на-Дону',
    bik="046015602",
    mandatory_purpose="для адвоката Гамза Д.Г.",
)


def bank_requisites_snapshot() -> dict[str, str]:
    return {key: str(value) for key, value in asdict(GAMZA_COLLEGIUM_REQUISITES).items()}


def bank_requisites_ready() -> bool:
    r = GAMZA_COLLEGIUM_REQUISITES
    return bool(
        r.recipient.strip()
        and r.advocate.strip()
        and r.inn.isdigit()
        and len(r.inn) == 10
        and r.kpp.isdigit()
        and len(r.kpp) == 9
        and r.ogrn.isdigit()
        and len(r.ogrn) == 13
        and r.settlement_account.isdigit()
        and len(r.settlement_account) == 20
        and r.correspondent_account.isdigit()
        and len(r.correspondent_account) == 20
        and r.bik.isdigit()
        and len(r.bik) == 9
        and r.mandatory_purpose.strip()
    )


def bank_payment_instructions(
    *,
    amount: Decimal | str | int | None = None,
) -> str:
    r = GAMZA_COLLEGIUM_REQUISITES
    amount_line = ""
    if amount is not None:
        amount_line = f"Сумма: {Decimal(str(amount)):.2f} ₽\n"

    return (
        "Оплата только банковским переводом по реквизитам коллегии адвокатов.\n\n"
        f"{amount_line}"
        f"Получатель: {r.recipient}\n"
        f"ИНН: {r.inn}\n"
        f"КПП: {r.kpp}\n"
        f"ОГРН: {r.ogrn}\n"
        f"Расчётный счёт: {r.settlement_account}\n"
        f"Корреспондентский счёт: {r.correspondent_account}\n"
        f"Банк: {r.bank}\n"
        f"БИК: {r.bik}\n\n"
        f"Назначение платежа — обязательно: {r.mandatory_purpose}\n\n"
        "После перевода не создавайте второй платёж. Поступление подтверждает "
        "команда после сверки банковской выписки."
    )


__all__ = [
    "BankRequisites",
    "GAMZA_COLLEGIUM_REQUISITES",
    "bank_payment_instructions",
    "bank_requisites_ready",
    "bank_requisites_snapshot",
]
