from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.domain.calculator.rule_catalog_v2 import prune_unused_sources


class RuleEditorV2Error(ValueError):
    """A structured calculator rule edit is invalid or targets an unknown section."""


def _require_v2(rules: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rules, dict) or rules.get("schema_version") != 2:
        raise RuleEditorV2Error("Структурный редактор доступен только для schema_version=2")
    return deepcopy(rules)


def _clean_source_id(value: str) -> str:
    source_id = str(value or "").strip()
    if not source_id:
        raise RuleEditorV2Error("Для юридического значения обязателен источник")
    if len(source_id) > 100:
        raise RuleEditorV2Error("source_id слишком длинный")
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")
    if any(char not in allowed for char in source_id):
        raise RuleEditorV2Error(
            "source_id может содержать только буквы, цифры, _ и -"
        )
    return source_id


def _upsert_source(
    rules: dict[str, Any],
    *,
    source_id: str,
    title: str,
    authority: str,
    url: str = "",
    document_ref: str = "",
) -> str:
    source_id = _clean_source_id(source_id)
    title = str(title or "").strip()
    authority = str(authority or "").strip()
    url = str(url or "").strip()
    document_ref = str(document_ref or "").strip()
    if not title or not authority:
        raise RuleEditorV2Error("Для источника обязательны название и орган/владелец")
    if not url and not document_ref:
        raise RuleEditorV2Error("Для источника нужна HTTPS-ссылка или внутренний документ")

    registry = rules.setdefault("sources", {})
    if not isinstance(registry, dict):
        raise RuleEditorV2Error("Повреждён реестр источников")
    registry[source_id] = {
        "kind": str((registry.get(source_id) or {}).get("kind") or "legal"),
        "title": title,
        "authority": authority,
        **({"url": url} if url else {}),
        **({"document_ref": document_ref} if document_ref else {}),
        "client_visible": True,
    }
    return source_id


def _source_ids(source_id: str) -> list[str]:
    return [_clean_source_id(source_id)]


def upsert_rate(
    rules: dict[str, Any],
    *,
    code: str,
    start: str,
    rate: str,
    source_id: str,
    source_title: str,
    source_authority: str,
    source_url: str = "",
    source_document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    code = str(code or "").strip()
    if not code or not start or not rate:
        raise RuleEditorV2Error("Для ставки обязательны код, дата начала и значение")
    sid = _upsert_source(
        result,
        source_id=source_id,
        title=source_title,
        authority=source_authority,
        url=source_url,
        document_ref=source_document_ref,
    )
    standard = result.setdefault("standard_object", {})
    rows = list(standard.get("rate_schedule") or [])
    row = {
        "code": code,
        "start": str(start).strip(),
        "rate": str(rate).strip(),
        "source_ids": _source_ids(sid),
    }
    rows = [item for item in rows if str(item.get("code") or "") != code]
    rows.append(row)
    rows.sort(key=lambda item: str(item.get("start") or ""))
    standard["rate_schedule"] = rows
    return prune_unused_sources(result)


def upsert_period(
    rules: dict[str, Any],
    *,
    section: str,
    code: str,
    start: str,
    end: str,
    numeric_value: str = "",
    source_id: str,
    source_title: str,
    source_authority: str,
    source_url: str = "",
    source_document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    code = str(code or "").strip()
    if not code or not start or not end:
        raise RuleEditorV2Error("Для периода обязательны код, начало и окончание")
    if section not in {"excluded_periods", "rate_cap_periods", "unique_excluded_periods"}:
        raise RuleEditorV2Error("Неизвестный раздел периодов")
    sid = _upsert_source(
        result,
        source_id=source_id,
        title=source_title,
        authority=source_authority,
        url=source_url,
        document_ref=source_document_ref,
    )
    if section == "unique_excluded_periods":
        parent = result.setdefault("unique_object", {})
        key = "excluded_periods"
    else:
        parent = result.setdefault("standard_object", {})
        key = section
    rows = list(parent.get(key) or [])
    row: dict[str, Any] = {
        "code": code,
        "start": str(start).strip(),
        "end": str(end).strip(),
        "source_ids": _source_ids(sid),
    }
    if section == "rate_cap_periods":
        if not str(numeric_value or "").strip():
            raise RuleEditorV2Error("Для ограничения ставки нужен cap_rate")
        row["cap_rate"] = str(numeric_value).strip()
    rows = [item for item in rows if str(item.get("code") or "") != code]
    rows.append(row)
    rows.sort(key=lambda item: (str(item.get("start") or ""), str(item.get("code") or "")))
    parent[key] = rows
    return prune_unused_sources(result)


def set_participant_multiplier(
    rules: dict[str, Any],
    *,
    participant_type: str,
    label: str,
    multiplier: str,
    source_id: str,
    source_title: str,
    source_authority: str,
    source_url: str = "",
    source_document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    participant_type = str(participant_type or "").strip()
    if participant_type not in {"consumer_individual", "other"}:
        raise RuleEditorV2Error("Разрешены consumer_individual и other")
    if not str(multiplier or "").strip():
        raise RuleEditorV2Error("Коэффициент не может быть пустым")
    sid = _upsert_source(
        result,
        source_id=source_id,
        title=source_title,
        authority=source_authority,
        url=source_url,
        document_ref=source_document_ref,
    )
    standard = result.setdefault("standard_object", {})
    participant_types = standard.setdefault("participant_types", {})
    participant_types[participant_type] = {
        "label": str(label or participant_type).strip(),
        "multiplier": str(multiplier).strip(),
        "source_ids": _source_ids(sid),
    }
    return prune_unused_sources(result)


def set_unique_object_rule(
    rules: dict[str, Any],
    *,
    ddu_signed_before: str,
    maximum_delay_months: str,
    divisor: str,
    multiplier: str,
    maximum_penalty_share: str,
    source_id: str,
    source_title: str,
    source_authority: str,
    source_url: str = "",
    source_document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    required = [
        ddu_signed_before,
        maximum_delay_months,
        divisor,
        multiplier,
        maximum_penalty_share,
    ]
    if any(not str(value or "").strip() for value in required):
        raise RuleEditorV2Error("Все параметры уникального объекта обязательны")
    sid = _upsert_source(
        result,
        source_id=source_id,
        title=source_title,
        authority=source_authority,
        url=source_url,
        document_ref=source_document_ref,
    )
    previous = result.get("unique_object")
    exclusions = (
        list(previous.get("excluded_periods") or [])
        if isinstance(previous, dict)
        else []
    )
    result["unique_object"] = {
        "label": "Уникальный объект по ч. 2.1 ст. 6 №214-ФЗ",
        "ddu_signed_before": str(ddu_signed_before).strip(),
        "maximum_delay_months": int(str(maximum_delay_months).strip()),
        "divisor": str(divisor).strip(),
        "multiplier": str(multiplier).strip(),
        "maximum_penalty_share_of_contract_price": str(maximum_penalty_share).strip(),
        "source_ids": _source_ids(sid),
        "excluded_periods": exclusions,
    }
    return prune_unused_sources(result)


def upsert_manual_review_condition(
    rules: dict[str, Any],
    *,
    code: str,
    label: str,
    source_id: str,
    source_title: str,
    source_authority: str,
    source_url: str = "",
    source_document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    code = str(code or "").strip()
    label = str(label or "").strip()
    if not code or not label:
        raise RuleEditorV2Error("Для стоп-фактора обязательны код и описание")
    sid = _upsert_source(
        result,
        source_id=source_id,
        title=source_title,
        authority=source_authority,
        url=source_url,
        document_ref=source_document_ref,
    )
    rows = list(result.get("manual_review_conditions") or [])
    rows = [item for item in rows if str(item.get("code") or "") != code]
    rows.append({"code": code, "label": label, "source_ids": [sid]})
    result["manual_review_conditions"] = rows
    return prune_unused_sources(result)


def delete_item(
    rules: dict[str, Any],
    *,
    section: str,
    code: str = "",
    participant_type: str = "",
) -> dict[str, Any]:
    """Clear one legal value and garbage-collect its now-unused source cards."""

    result = _require_v2(rules)
    if section == "rate":
        parent = result.setdefault("standard_object", {})
        parent["rate_schedule"] = [
            item
            for item in list(parent.get("rate_schedule") or [])
            if str(item.get("code") or "") != str(code)
        ]
    elif section in {"excluded_period", "rate_cap"}:
        parent = result.setdefault("standard_object", {})
        key = "excluded_periods" if section == "excluded_period" else "rate_cap_periods"
        parent[key] = [
            item
            for item in list(parent.get(key) or [])
            if str(item.get("code") or "") != str(code)
        ]
    elif section == "unique_excluded_period":
        parent = result.setdefault("unique_object", {})
        parent["excluded_periods"] = [
            item
            for item in list(parent.get("excluded_periods") or [])
            if str(item.get("code") or "") != str(code)
        ]
    elif section == "participant":
        parent = result.setdefault("standard_object", {}).setdefault(
            "participant_types", {}
        )
        parent.pop(str(participant_type), None)
    elif section == "unique_object":
        result.pop("unique_object", None)
    elif section == "manual_review":
        result["manual_review_conditions"] = [
            item
            for item in list(result.get("manual_review_conditions") or [])
            if str(item.get("code") or "") != str(code)
        ]
    elif section == "control_example":
        result["control_examples"] = [
            item
            for item in list(result.get("control_examples") or [])
            if str(item.get("code") or "") != str(code)
        ]
    else:
        raise RuleEditorV2Error("Неизвестный тип удаляемого значения")
    return prune_unused_sources(result)


def update_source_card(
    rules: dict[str, Any],
    *,
    source_id: str,
    title: str,
    authority: str,
    url: str = "",
    document_ref: str = "",
) -> dict[str, Any]:
    result = _require_v2(rules)
    source_id = _clean_source_id(source_id)
    if source_id not in result.get("sources", {}):
        raise RuleEditorV2Error(
            "Нельзя создать несвязанный источник отдельно: сначала добавьте значение правила"
        )
    _upsert_source(
        result,
        source_id=source_id,
        title=title,
        authority=authority,
        url=url,
        document_ref=document_ref,
    )
    return prune_unused_sources(result)


__all__ = [
    "RuleEditorV2Error",
    "delete_item",
    "set_participant_multiplier",
    "set_unique_object_rule",
    "update_source_card",
    "upsert_manual_review_condition",
    "upsert_period",
    "upsert_rate",
]
