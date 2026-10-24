from __future__ import annotations

from decimal import Decimal


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"


def format_calculation_result(result) -> str:
    verified_period = getattr(result, "legal_rules_version", None) is not None
    chargeable_days = getattr(result, "chargeable_days", None)
    excluded_days = getattr(result, "excluded_days", 0)
    if verified_period:
        rate_text = (
            format_percent(result.key_rate)
            if chargeable_days else "не применяется (нет начисляемых дней)"
        )
        period_details = (
            f"Календарная просрочка: {result.delay_days} дн.\n"
            f"Исключено по постановлению № 326: {excluded_days} дн.\n"
            f"Начисляемый период: {chargeable_days} дн.\n"
            f"Ставка ЦБ на дату исполнения: {rate_text}\n"
        )
    else:
        period_details = (
            f"Дней просрочки: {result.delay_days}\n"
            f"Ключевая ставка в расчете: {format_percent(result.key_rate)}\n"
        )
    text = (
        "Предварительный расчёт\n\n"
        f"Стоимость по ДДУ: {format_money(result.contract_price)}\n"
        f"Расчёт на дату: {result.calculation_date.strftime('%d.%m.%Y')}\n"
        + period_details
        + f"Коэффициент: {result.consumer_multiplier}\n"
        + f"Предварительная сумма неустойки: {format_money(result.penalty_amount)}\n\n"
        + "Сумма не является гарантией взыскания. Юрист проверит ДДУ, "
        "особые случаи статьи 6, возможные соглашения, даты и применимость "
        "отсрочки исполнения."
    )
    return text + (f"\n\nВажно: {result.warning}" if result.warning else "")
