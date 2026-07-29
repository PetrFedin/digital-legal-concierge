from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP


FORMULA_VERSION = "ddu-214fz-v2"
MONEY_QUANT = Decimal("0.01")
RATE_QUANT = Decimal("0.000001")


@dataclass(frozen=True)
class PenaltyCalculationInput:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None = None
    key_rate: Decimal = Decimal("0.16")
    consumer_multiplier: Decimal = Decimal("2")


@dataclass(frozen=True)
class PenaltyCalculationResult:
    contract_price: Decimal
    planned_transfer_date: date
    calculation_date: date
    object_transferred: bool
    actual_transfer_date: date | None
    delay_days: int
    key_rate: Decimal
    consumer_multiplier: Decimal
    penalty_amount: Decimal
    formula_version: str
    formula: str
    recommended_route: str
    is_preliminary: bool = True
    warning: str | None = None


class PenaltyCalculationError(ValueError):
    """Invalid or internally inconsistent calculator input."""


class PenaltyCalculator:
    def calculate(self, data: PenaltyCalculationInput) -> PenaltyCalculationResult:
        price = _as_decimal(data.contract_price, "Стоимость по ДДУ")
        rate = _as_decimal(data.key_rate, "Ключевая ставка").quantize(RATE_QUANT)
        multiplier = _as_decimal(
            data.consumer_multiplier,
            "Коэффициент участника долевого строительства",
        )

        if price <= 0 or price > Decimal("1000000000000"):
            raise PenaltyCalculationError(
                "Стоимость по ДДУ должна быть больше нуля и не превышать 1 трлн рублей"
            )
        if rate < 0 or rate > Decimal("1"):
            raise PenaltyCalculationError(
                "Ключевая ставка задаётся долей от 0 до 1, например 0.16 для 16%"
            )
        if multiplier <= 0 or multiplier > Decimal("10"):
            raise PenaltyCalculationError("Коэффициент должен быть больше 0 и не выше 10")
        if data.calculation_date < data.planned_transfer_date:
            raise PenaltyCalculationError(
                "Дата расчёта не может быть раньше договорной даты передачи"
            )

        if data.object_transferred:
            if data.actual_transfer_date is None:
                raise PenaltyCalculationError(
                    "Для переданного объекта нужна фактическая дата передачи"
                )
            if data.actual_transfer_date < data.planned_transfer_date:
                raise PenaltyCalculationError(
                    "Фактическая дата передачи не может быть раньше договорной"
                )
            if data.actual_transfer_date > data.calculation_date:
                raise PenaltyCalculationError(
                    "Фактическая дата передачи не может быть позже даты расчёта"
                )
            end_date = data.actual_transfer_date
        else:
            if data.actual_transfer_date is not None:
                raise PenaltyCalculationError(
                    "Фактическая дата не передаётся, пока объект не передан"
                )
            end_date = data.calculation_date

        # Просрочка начинается на следующий день после договорной даты. Разность
        # дат уже даёт точное число календарных дней такого периода включительно
        # по конечную дату.
        delay_days = max((end_date - data.planned_transfer_date).days, 0)
        amount = (
            price
            * rate
            / Decimal("300")
            * Decimal(delay_days)
            * multiplier
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

        formula = (
            f"{price.quantize(MONEY_QUANT)} × {rate} / 300 × "
            f"{delay_days} × {multiplier}"
        )
        warning = None
        if delay_days == 0:
            warning = "Просрочка на выбранную дату не обнаружена."
        elif rate == 0:
            warning = "Ключевая ставка равна нулю; проверьте исходные данные."

        return PenaltyCalculationResult(
            contract_price=price.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            planned_transfer_date=data.planned_transfer_date,
            calculation_date=data.calculation_date,
            object_transferred=data.object_transferred,
            actual_transfer_date=data.actual_transfer_date,
            delay_days=delay_days,
            key_rate=rate,
            consumer_multiplier=multiplier,
            penalty_amount=amount,
            formula_version=FORMULA_VERSION,
            formula=formula,
            recommended_route="M1" if delay_days > 0 and amount > 0 else "M2",
            warning=warning,
        )


def _as_decimal(value: Decimal | int | float | str, title: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PenaltyCalculationError(f"{title}: некорректное число") from error
    if not result.is_finite():
        raise PenaltyCalculationError(f"{title}: число должно быть конечным")
    return result


def parse_money(value: str) -> Decimal:
    if value is None:
        raise PenaltyCalculationError("Стоимость не указана")
    cleaned = (
        str(value)
        .replace(" ", "")
        .replace("\u00a0", "")
        .replace("₽", "")
        .replace(",", ".")
        .strip()
    )
    amount = _as_decimal(cleaned, "Стоимость")
    if amount <= 0 or amount > Decimal("1000000000000"):
        raise PenaltyCalculationError(
            "Стоимость должна быть больше нуля и не превышать 1 трлн рублей"
        )
    return amount.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
