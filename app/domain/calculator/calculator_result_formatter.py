from __future__ import annotations

from decimal import Decimal


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"


def _segment_rate_summary(result) -> str | None:
    segments = tuple(getattr(result, "segments", ()) or ())
    chargeable = [segment for segment in segments if int(segment.days_chargeable) > 0]
    if not chargeable:
        return None

    # Keep order of application and de-duplicate exact repeated rates. A
    # multi-rate result must never be presented as if one scalar rate governed
    # the full period.
    values: list[Decimal] = []
    for segment in chargeable:
        value = Decimal(segment.rate_value)
        if value not in values:
            values.append(value)
    if len(values) == 1:
        return f"Ставка в начисляемом периоде: {format_percent(values[0])}"
    return "Ставки по периодам: " + ", ".join(format_percent(value) for value in values)


def format_calculation_result(result) -> str:
    gross_days = int(
        getattr(result, "delay_days_total", getattr(result, "delay_days", 0)) or 0
    )
    chargeable_days = int(
        getattr(result, "delay_days_chargeable", gross_days) or 0
    )
    excluded_days = int(getattr(result, "moratorium_days", 0) or 0)
    end_date = getattr(result, "calculation_end_date", None)

    lines = [
        "📊 Предварительный расчёт готов",
        "",
        f"Стоимость по ДДУ: {format_money(Decimal(result.contract_price))}",
        f"Расчёт на дату: {result.calculation_date.strftime('%d.%m.%Y')}",
        f"Дней просрочки всего: {gross_days}",
    ]
    if chargeable_days != gross_days or excluded_days:
        lines.append(f"Дней, участвующих в начислении: {chargeable_days}")
        lines.append(f"Исключено из начисления: {excluded_days}")
    if end_date is not None and end_date != result.calculation_date:
        lines.append(f"Конец расчётного периода: {end_date.strftime('%d.%m.%Y')}")

    rate_summary = _segment_rate_summary(result)
    if rate_summary:
        lines.append(rate_summary)

    segments = tuple(getattr(result, "segments", ()) or ())
    multipliers = []
    for segment in segments:
        value = Decimal(segment.consumer_multiplier)
        if value not in multipliers:
            multipliers.append(value)
    if len(multipliers) == 1:
        lines.append(f"Коэффициент: {multipliers[0]}")

    revision = getattr(result, "rule_revision", None)
    if revision is not None:
        lines.append(f"Применена редакция правил расчёта: {revision}")

    lines.extend(
        [
            f"Предварительная сумма неустойки: {format_money(Decimal(result.penalty_amount))}",
            "",
            "Важно: расчёт предварительный. Юрист проверит применимость правил, условия ДДУ, дополнительные соглашения, документы и обстоятельства дела.",
        ]
    )
    warning = getattr(result, "warning", None)
    if warning:
        lines.extend(["", f"⚠️ {warning}"])
    return "\n".join(lines)
