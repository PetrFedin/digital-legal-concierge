from __future__ import annotations

from decimal import Decimal


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"


def format_calculation_result(result) -> str:
    text = (
        "📊 Предварительный расчет готов\n\n"
        f"Стоимость по ДДУ: {format_money(result.contract_price)}\n"
        f"Расчет на дату: {result.calculation_date.strftime('%d.%m.%Y')}\n"
        f"Дней просрочки: {result.delay_days}\n"
        f"Ключевая ставка в расчете: {format_percent(result.key_rate)}\n"
        f"Коэффициент: {result.consumer_multiplier}\n"
        f"Предварительная сумма неустойки: {format_money(result.penalty_amount)}\n\n"
        "Важно: расчет предварительный. Точная сумма требует проверки применимой "
        "ставки по периодам, мораториев, условий ДДУ, дополнительных соглашений, "
        "дат фактической передачи и позиции суда."
    )
    return text + (f"\n\n⚠️ {result.warning}" if result.warning else "")
