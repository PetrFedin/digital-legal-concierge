from __future__ import annotations

from decimal import Decimal


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"


def _rate_line(result) -> str:
    key_rate = getattr(result, "key_rate", None)
    if key_rate is not None:
        return f"Ставка в расчёте: {format_percent(key_rate)}"
    segments = list(getattr(result, "applied_segments", None) or [])
    if segments:
        unique = {str(item.get("rate")) for item in segments if item.get("rate") is not None}
        return f"Ставка в расчёте: по периодам ({len(unique)} знач.)"
    return "Ставка в расчёте: начисляемый период отсутствует"


def format_calculation_result(result) -> str:
    total_days = int(getattr(result, "delay_days_total", result.delay_days) or 0)
    chargeable_days = int(
        getattr(result, "delay_days_chargeable", result.delay_days) or 0
    )
    moratorium_days = int(getattr(result, "moratorium_days", 0) or 0)
    rule_key = getattr(result, "rule_revision_key", None)

    day_lines = [f"Календарных дней просрочки: {total_days}"]
    if chargeable_days != total_days or moratorium_days:
        day_lines.append(f"Начисляемых дней: {chargeable_days}")
        day_lines.append(f"Исключённых дней: {moratorium_days}")

    lines = [
        "📊 Предварительный расчет готов",
        "",
        f"Стоимость по ДДУ: {format_money(result.contract_price)}",
        f"Расчет на дату: {result.calculation_date.strftime('%d.%m.%Y')}",
        *day_lines,
        _rate_line(result),
        f"Коэффициент: {result.consumer_multiplier}",
        f"Предварительная сумма неустойки: {format_money(result.penalty_amount)}",
    ]
    if rule_key:
        lines.append(f"Версия правил расчёта: {rule_key}")
    lines.extend(
        [
            "",
            "Важно: расчет предварительный. Юрист подтверждает применимость "
            "правил к конкретному ДДУ, дополнительным соглашениям, фактическим "
            "обстоятельствам и выбранному способу защиты.",
        ]
    )
    text = "\n".join(lines)
    return text + (f"\n\n⚠️ {result.warning}" if result.warning else "")
