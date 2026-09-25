from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any


def format_money(value: Decimal) -> str:
    return f"{value:,.2f}".replace(",", " ") + " ₽"


def format_percent(value: Decimal) -> str:
    return f"{value * Decimal('100'):.3f}".rstrip("0").rstrip(".") + "%"




def split_telegram_text(text: str, *, max_chars: int = 3500) -> list[str]:
    """Split long client-safe details below Telegram's message size ceiling."""

    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    value = str(text or "")
    if len(value) <= max_chars:
        return [value]

    pages: list[str] = []
    current: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        page = "\n".join(current).rstrip()
        if page:
            pages.append(page)
        current = []
        current_len = 0

    for raw_line in value.splitlines():
        line = raw_line
        while len(line) > max_chars:
            if current:
                flush()
            pages.append(line[:max_chars])
            line = line[max_chars:]
        added = len(line) + (1 if current else 0)
        if current and current_len + added > max_chars:
            flush()
        current.append(line)
        current_len += len(line) + (1 if len(current) > 1 else 0)

    if current:
        flush()
    return pages or [""]

def _rate_line(result) -> str:
    key_rate = getattr(result, "key_rate", None)
    if key_rate is not None:
        return f"Ставка на дату исполнения обязательства: {format_percent(Decimal(key_rate))}"
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
        "📊 Предварительный расчёт готов",
        "",
        f"Стоимость по ДДУ: {format_money(Decimal(result.contract_price))}",
        f"Расчёт на дату: {result.calculation_date.strftime('%d.%m.%Y')}",
        *day_lines,
        _rate_line(result),
        f"Коэффициент: {result.consumer_multiplier}",
        f"Предварительная сумма неустойки: {format_money(Decimal(result.penalty_amount))}",
    ]
    if bool(getattr(result, "amount_cap_applied", False)):
        cap = getattr(result, "amount_cap", None)
        if cap is not None:
            lines.append(
                "Применён предельный размер для уникального объекта: "
                f"{format_money(Decimal(cap))}"
            )
    if rule_key:
        lines.append(f"Версия правил расчёта: {rule_key}")
    lines.extend(
        [
            "",
            "Важно: расчёт предварительный. Юрист подтверждает применимость "
            "правил к конкретному ДДУ, дополнительным соглашениям, фактическим "
            "обстоятельствам и выбранному способу защиты.",
        ]
    )
    text = "\n".join(lines)
    return text + (f"\n\n⚠️ {result.warning}" if result.warning else "")


def _collect_relevant_source_refs(result) -> list[str]:
    snapshot = getattr(result, "rule_snapshot", None) or {}
    if not isinstance(snapshot, dict):
        return []

    refs: list[str] = []

    def add(raw: object) -> None:
        if not isinstance(raw, list):
            return
        for item in raw:
            value = str(item or "").strip()
            if value and value not in refs:
                refs.append(value)

    formula = snapshot.get("formula")
    if isinstance(formula, dict):
        add(formula.get("source_refs"))
    rate_policy = snapshot.get("rate_policy")
    if isinstance(rate_policy, dict):
        add(rate_policy.get("source_refs"))

    client_types = snapshot.get("client_types")
    client_type = str(getattr(result, "client_type", "") or "")
    if isinstance(client_types, dict) and isinstance(client_types.get(client_type), dict):
        add(client_types[client_type].get("source_refs"))

    if bool(getattr(result, "unique_object", False)):
        unique_rule = snapshot.get("unique_object")
        if isinstance(unique_rule, dict):
            add(unique_rule.get("source_refs"))

    for segment in list(getattr(result, "applied_segments", None) or []):
        if isinstance(segment, dict):
            add(segment.get("base_rate_source_refs"))
            add(segment.get("cap_source_refs"))
    for segment in list(getattr(result, "excluded_segments", None) or []):
        if isinstance(segment, dict):
            add(segment.get("source_refs"))
    return refs


def _show_date(value: object) -> str:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    try:
        return date.fromisoformat(str(value)).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(value or "—")


def format_calculation_details(result) -> str:
    """Client-safe legal/calculation trace for one immutable calculation."""

    snapshot: dict[str, Any] = getattr(result, "rule_snapshot", None) or {}
    sources = snapshot.get("sources") if isinstance(snapshot, dict) else {}
    sources = sources if isinstance(sources, dict) else {}
    segments = list(getattr(result, "applied_segments", None) or [])
    exclusions = list(getattr(result, "excluded_segments", None) or [])

    lines = [
        "🔎 Детализация предварительного расчёта",
        "",
        f"Версия правил: {getattr(result, 'rule_revision_key', '—')}",
        f"SHA-256 правил: {getattr(result, 'rule_snapshot_sha256', '—')}",
        f"Дата исполнения обязательства: {_show_date(getattr(result, 'planned_transfer_date', None))}",
    ]
    key_rate = getattr(result, "key_rate", None)
    if key_rate is not None:
        lines.append(
            "Базовая ставка, зафиксированная на эту дату: "
            f"{format_percent(Decimal(key_rate))}"
        )
    lines.append(
        "Тип участника: "
        + (
            "гражданин для личных/семейных нужд"
            if str(getattr(result, "client_type", "")) == "consumer"
            else "иной участник"
        )
    )
    lines.append(
        "Уникальный объект: "
        + ("да" if bool(getattr(result, "unique_object", False)) else "нет")
    )

    if segments:
        lines.extend(["", "Начисляемые сегменты:"])
        for item in segments:
            if not isinstance(item, dict):
                continue
            rate = item.get("rate")
            rate_text = (
                format_percent(Decimal(str(rate))) if rate is not None else "—"
            )
            cap = item.get("cap")
            cap_text = (
                f"; ограничение ставки {format_percent(Decimal(str(cap)))}"
                if cap is not None
                else ""
            )
            lines.append(
                f"• {_show_date(item.get('start'))}–{_show_date(item.get('end'))}: "
                f"{int(item.get('days') or 0)} дн.; ставка {rate_text}{cap_text}"
            )
    else:
        lines.extend(["", "Начисляемых сегментов нет."])

    if exclusions:
        lines.extend(["", "Исключённые периоды / моратории:"])
        for item in exclusions:
            if not isinstance(item, dict):
                continue
            lines.append(
                f"• {_show_date(item.get('start'))}–{_show_date(item.get('end'))}: "
                f"{int(item.get('days') or 0)} дн.; правило {item.get('code') or '—'}"
            )

    gross = getattr(result, "gross_penalty_amount", None)
    if gross is not None and bool(getattr(result, "amount_cap_applied", False)):
        lines.extend(
            [
                "",
                f"Сумма до предельного ограничения: {format_money(Decimal(gross))}",
                f"Применённый предел: {format_money(Decimal(result.amount_cap))}",
            ]
        )

    refs = _collect_relevant_source_refs(result)
    if refs:
        lines.extend(["", "Правовые и расчётные источники:"])
        for ref in refs:
            source = sources.get(ref)
            if not isinstance(source, dict):
                lines.append(f"• {ref}: источник отсутствует в сохранённом снимке")
                continue
            title = str(source.get("title") or ref)
            locator = str(source.get("locator") or "").strip()
            url = str(source.get("url") or "")
            lines.append(
                f"• {title}"
                + (f"\n  Основание: {locator}" if locator else "")
                + f"\n  {url}"
            )

    lines.extend(
        [
            "",
            "Расчёт воспроизводится по сохранённой версии правил и не меняется "
            "при последующем обновлении справочников. Он остаётся предварительным: "
            "окончательная применимость нормы подтверждается юристом по документам дела.",
        ]
    )
    return "\n".join(lines)


__all__ = [
    "format_calculation_details",
    "format_calculation_result",
    "format_money",
    "format_percent",
    "split_telegram_text",
]
