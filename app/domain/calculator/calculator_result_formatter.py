from __future__ import annotations

from decimal import Decimal


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"


def _rate_line(result) -> str:
    base_rate = getattr(result, "base_rate", None)
    base_rate_date = getattr(result, "base_rate_date", None)
    if base_rate is not None and base_rate_date is not None:
        return (
            "Ставка на договорную дату "
            f"{base_rate_date.strftime('%d.%m.%Y')}: {format_percent(base_rate)}"
        )

    key_rate = getattr(result, "key_rate", None)
    if key_rate is not None:
        return f"Ставка в расчёте: {format_percent(key_rate)}"

    segments = list(getattr(result, "applied_segments", None) or [])
    if segments:
        unique = {
            str(item.get("effective_rate") or item.get("rate"))
            for item in segments
            if item.get("effective_rate") is not None or item.get("rate") is not None
        }
        return f"Ставка в расчёте: по периодам ({len(unique)} знач.)"
    return "Ставка в расчёте: начисляемый период отсутствует"


def format_calculation_result(result) -> str:
    total_days = int(getattr(result, "delay_days_total", result.delay_days) or 0)
    chargeable_days = int(
        getattr(result, "delay_days_chargeable", result.delay_days) or 0
    )
    moratorium_days = int(getattr(result, "moratorium_days", 0) or 0)
    rule_key = getattr(result, "rule_revision_key", None)
    branch = str(getattr(result, "calculation_branch", "standard") or "standard")
    cap_applied = bool(getattr(result, "penalty_cap_applied", False))
    segments = list(getattr(result, "applied_segments", None) or [])
    rate_cap_used = any(item.get("cap_code") for item in segments)

    day_lines = [f"Календарных дней просрочки: {total_days}"]
    if chargeable_days != total_days or moratorium_days:
        day_lines.append(f"Начисляемых дней: {chargeable_days}")
        day_lines.append(f"Исключённых дней: {moratorium_days}")

    lines = [
        "📊 Предварительный расчёт готов",
        "",
        f"Стоимость по ДДУ: {format_money(result.contract_price)}",
        f"Расчёт на дату: {result.calculation_date.strftime('%d.%m.%Y')}",
        (
            "Ветка правил: уникальный объект (ч. 2.1 ст. 6 №214-ФЗ)"
            if branch == "unique"
            else "Ветка правил: стандартный объект (ч. 2 ст. 6 №214-ФЗ)"
        ),
        *day_lines,
        _rate_line(result),
    ]
    if rate_cap_used:
        lines.append("Ограничение ставки: применено к соответствующей части периода")
    lines.append(f"Коэффициент участника: {result.consumer_multiplier}")
    if cap_applied:
        lines.append("Лимит суммы для уникального объекта: применён")
    lines.append(
        f"Предварительная сумма неустойки: {format_money(result.penalty_amount)}"
    )
    if rule_key:
        lines.append(f"Версия юридических правил: {rule_key}")
    lines.extend(
        [
            "",
            "Нажмите «Как рассчитано и правовые основания», чтобы увидеть формулу, "
            "периоды, ограничения и ссылки на источники.",
            "",
            "Важно: расчёт предварительный. Юрист подтверждает применимость "
            "правил к конкретному ДДУ, дополнительным соглашениям, фактическим "
            "обстоятельствам и выбранному способу защиты.",
        ]
    )
    text = "\n".join(lines)
    return text + (f"\n\n⚠️ {result.warning}" if result.warning else "")
