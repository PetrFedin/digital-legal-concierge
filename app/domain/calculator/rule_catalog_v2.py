from __future__ import annotations

from copy import deepcopy
from typing import Any
from urllib.parse import urlparse


class RuleSourceError(ValueError):
    """Legal source registry is incomplete, stale or inconsistent with rule data."""


def approved_v2_rule_template() -> dict[str, Any]:
    """Return the lawyer-approved PM-016 v2 methodology as a DRAFT payload.

    This is a source-controlled import/template only. Runtime calculation authority
    remains the APPROVED immutable revision stored in PostgreSQL. No migration
    silently activates legal data.
    """

    sources = {
        "FZ214_ART6": {
            "kind": "law",
            "title": "Федеральный закон №214-ФЗ, статья 6",
            "authority": "Федеральный закон",
            "url": "https://www.consultant.ru/document/cons_doc_LAW_51038/817620cd3c49f19912fc1ac0f98b10d17f65b75b/",
            "client_visible": True,
        },
        "FZ421_ART2": {
            "kind": "law",
            "title": "Федеральный закон от 04.08.2023 №421-ФЗ, статья 2",
            "authority": "Федеральный закон",
            "url": "https://www.consultant.ru/document/cons_doc_LAW_453876/b004fed0b70d0f223e4a81f8ad6cd92af90a7e3b/",
            "client_visible": True,
        },
        "VS_DDU_REVIEW": {
            "kind": "court",
            "title": "Обзор практики по спорам, связанным с долевым строительством",
            "authority": "Верховный Суд Российской Федерации",
            "url": "https://www.vsrf.ru/files/14489/",
            "client_visible": True,
        },
        "VS_2012_Q3": {
            "kind": "court",
            "title": "Обзор судебной практики Верховного Суда РФ за III квартал 2012 года",
            "authority": "Верховный Суд Российской Федерации",
            "url": "https://www.vsrf.ru/documents/all/15115",
            "client_visible": True,
        },
        "PP423": {
            "kind": "regulation",
            "title": "Постановление Правительства РФ от 02.04.2020 №423",
            "authority": "Правительство Российской Федерации",
            "url": "https://www.consultant.ru/document/cons_doc_LAW_349308/",
            "client_visible": True,
        },
        "VS_COVID_REVIEW_3": {
            "kind": "court",
            "title": "Обзор ВС РФ №3 по COVID-19, вопрос 12",
            "authority": "Верховный Суд Российской Федерации",
            "url": "https://www.vsrf.ru/documents/all/29689/",
            "client_visible": True,
        },
        "PP479": {
            "kind": "regulation",
            "title": "Постановление Правительства РФ от 26.03.2022 №479",
            "authority": "Правительство Российской Федерации",
            "url": "https://www.consultant.ru/document/cons_doc_LAW_413069/",
            "client_visible": True,
        },
        "PP326": {
            "kind": "regulation",
            "title": "Постановление Правительства РФ от 18.03.2024 №326",
            "authority": "Правительство Российской Федерации",
            "url": "https://government.ru/docs/all/152615/",
            "client_visible": True,
        },
        "CBR_REFI_EQUALS_KEY": {
            "kind": "regulator",
            "title": "С 01.01.2016 ставка рефинансирования приравнена к ключевой ставке",
            "authority": "Банк России",
            "url": "https://www.cbr.ru/press/pr/?file=11122015_140001dkp2015-12-11t13_47_51.htm",
            "client_visible": True,
        },
        "CBR_KEY_RATE_HISTORY": {
            "kind": "regulator",
            "title": "История ключевой ставки Банка России",
            "authority": "Банк России",
            "url": "https://www.cbr.ru/hd_base/KeyRate/",
            "client_visible": True,
        },
        "GK333": {
            "kind": "law",
            "title": "ГК РФ, статья 333: уменьшение неустойки судом",
            "authority": "Гражданский кодекс Российской Федерации",
            "url": "https://www.consultant.ru/document/cons_doc_LAW_5142/f9732de88783800811973b3a13ef5112de0b5321/",
            "client_visible": True,
        },
        "INTERNAL_ROUNDING_20260924": {
            "kind": "methodology",
            "title": "Техническое правило округления результата до копеек",
            "authority": "Legal Concierge — согласованная методология",
            "document_ref": "Методология калькулятора ДДУ / редакция 24.09.2026",
            "client_visible": True,
        },
    }

    # Key-rate change points. The runtime resolves the row whose start date is
    # the latest date not later than the contractual due date. Every row points
    # to the same official Bank of Russia history source.
    rate_schedule = [
        ("2016-01-01", "0.11"),
        ("2016-06-14", "0.105"),
        ("2016-09-19", "0.10"),
        ("2017-03-27", "0.0975"),
        ("2017-05-02", "0.0925"),
        ("2017-06-19", "0.09"),
        ("2017-09-18", "0.085"),
        ("2017-10-30", "0.0825"),
        ("2017-12-18", "0.0775"),
        ("2018-02-12", "0.075"),
        ("2018-03-26", "0.0725"),
        ("2018-09-17", "0.075"),
        ("2018-12-17", "0.0775"),
        ("2019-06-17", "0.075"),
        ("2019-07-29", "0.0725"),
        ("2019-09-09", "0.07"),
        ("2019-10-28", "0.065"),
        ("2019-12-16", "0.0625"),
        ("2020-02-10", "0.06"),
        ("2020-04-27", "0.055"),
        ("2020-06-22", "0.045"),
        ("2020-07-27", "0.0425"),
        ("2021-03-22", "0.045"),
        ("2021-04-26", "0.05"),
        ("2021-06-15", "0.055"),
        ("2021-07-26", "0.065"),
        ("2021-09-13", "0.0675"),
        ("2021-10-25", "0.075"),
        ("2021-12-20", "0.085"),
        ("2022-02-14", "0.095"),
        ("2022-02-28", "0.20"),
        ("2022-04-11", "0.17"),
        ("2022-05-04", "0.14"),
        ("2022-05-27", "0.11"),
        ("2022-06-14", "0.095"),
        ("2022-07-25", "0.08"),
        ("2022-09-19", "0.075"),
        ("2023-07-24", "0.085"),
        ("2023-08-15", "0.12"),
        ("2023-09-18", "0.13"),
        ("2023-10-30", "0.15"),
        ("2023-12-18", "0.16"),
        ("2024-07-29", "0.18"),
        ("2024-09-16", "0.19"),
        ("2024-10-28", "0.21"),
        ("2025-06-09", "0.20"),
        ("2025-07-28", "0.18"),
        ("2025-09-15", "0.17"),
        ("2025-10-27", "0.165"),
        ("2025-12-22", "0.16"),
        ("2026-02-16", "0.155"),
        ("2026-03-23", "0.15"),
        ("2026-04-27", "0.145"),
        ("2026-06-22", "0.1425"),
        ("2026-07-27", "0.14"),
    ]

    return {
        "schema_version": 2,
        "revision_family": "ddu_214fz_delay_transfer",
        "formula_code": "ddu_214fz_art6_due_date_rate_v2",
        "sources": sources,
        "period": {
            "start": "day_after_contractual_due_date",
            "end_if_transferred": "transfer_document_date_inclusive",
            "end_if_not_transferred": "calculation_date_inclusive",
            "source_ids": ["FZ214_ART6", "VS_DDU_REVIEW"],
        },
        "rounding": {
            "money_quant": "0.01",
            "mode": "ROUND_HALF_UP",
            "stage": "final_total",
            "source_ids": ["INTERNAL_ROUNDING_20260924"],
        },
        "standard_object": {
            "divisor": "300",
            "source_ids": ["FZ214_ART6", "VS_2012_Q3"],
            "base_rate": {
                "basis": "rate_on_contractual_due_date",
                "before_2016": "CBR_REFINANCING_RATE",
                "from_2016": "CBR_KEY_RATE",
                "source_ids": [
                    "FZ214_ART6",
                    "CBR_REFI_EQUALS_KEY",
                    "CBR_KEY_RATE_HISTORY",
                ],
            },
            "rate_schedule": [
                {
                    "code": f"CBR-{start}",
                    "start": start,
                    "rate": rate,
                    "source_ids": ["CBR_KEY_RATE_HISTORY"],
                }
                for start, rate in rate_schedule
            ],
            "participant_types": {
                "consumer_individual": {
                    "label": "Гражданин — участник долевого строительства",
                    "multiplier": "2",
                    "source_ids": ["FZ214_ART6", "VS_2012_Q3"],
                },
                "other": {
                    "label": "Иной участник",
                    "multiplier": "1",
                    "source_ids": ["FZ214_ART6"],
                },
            },
            "excluded_periods": [
                {
                    "code": "PP423",
                    "start": "2020-04-03",
                    "end": "2021-01-01",
                    "source_ids": ["PP423", "VS_COVID_REVIEW_3"],
                },
                {
                    "code": "PP479",
                    "start": "2022-03-29",
                    "end": "2023-06-30",
                    "source_ids": ["PP479"],
                },
                {
                    "code": "PP326_ART6_PART2",
                    "start": "2024-03-22",
                    "end": "2025-12-31",
                    "source_ids": ["PP326"],
                },
            ],
            "rate_cap_periods": [
                {
                    "code": "PP479_CAP",
                    "start": "2022-02-25",
                    "end": "2023-06-30",
                    "cap_rate": "0.095",
                    "source_ids": ["PP479", "CBR_KEY_RATE_HISTORY"],
                },
                {
                    "code": "PP326_CAP",
                    "start": "2023-07-01",
                    "end": "2025-12-31",
                    "cap_rate": "0.075",
                    "source_ids": ["PP326", "CBR_KEY_RATE_HISTORY"],
                },
            ],
        },
        "unique_object": {
            "label": "Уникальный объект по ч. 2.1 ст. 6 №214-ФЗ",
            "ddu_signed_before": "2023-08-15",
            "maximum_delay_months": 30,
            "divisor": "300",
            "multiplier": "1",
            "maximum_penalty_share_of_contract_price": "0.05",
            "source_ids": ["FZ214_ART6", "FZ421_ART2"],
            "excluded_periods": [
                {
                    "code": "PP326_ART6_PART2_1",
                    "start": "2025-01-01",
                    "end": "2025-12-31",
                    "source_ids": ["PP326"],
                }
            ],
        },
        "manual_review_conditions": [
            {
                "code": "UNIQUE_OBJECT_UNKNOWN",
                "label": "Неизвестно, относится ли объект к уникальным",
                "source_ids": ["FZ214_ART6", "FZ421_ART2"],
            },
            {
                "code": "UNIQUE_DELAY_OVER_30_MONTHS",
                "label": "Просрочка уникального объекта превышает 30 месяцев",
                "source_ids": ["FZ214_ART6"],
            },
            {
                "code": "ACCEPTANCE_EVASION_YES_OR_UNKNOWN",
                "label": "Есть или неясны обстоятельства уклонения от приёмки",
                "source_ids": ["FZ214_ART6"],
            },
            {
                "code": "CONTRACTUAL_DEADLINE_UNCERTAIN",
                "label": "Последний действующий срок передачи по ДДУ/допсоглашениям не подтверждён",
                "source_ids": ["FZ214_ART6", "VS_DDU_REVIEW"],
            },
            {
                "code": "TRANSFER_DOCUMENT_DATE_DISPUTED",
                "label": "Дата передаточного документа оспаривается или неясна",
                "source_ids": ["VS_DDU_REVIEW"],
            },
        ],
        "judicial_adjustments": [
            {
                "code": "GK333_NOT_AUTOMATIC",
                "label": "Возможное снижение по ст. 333 ГК РФ не вычитается автоматически",
                "source_ids": ["GK333", "VS_2012_Q3"],
            }
        ],
        "control_examples": [
            {
                "code": "STANDARD_2026_TRANSFERRED",
                "title": "Стандартный объект, гражданин, передача в 2026 году",
                "input": {
                    "contract_price": "10000000",
                    "planned_transfer_date": "2026-03-31",
                    "actual_transfer_date": "2026-05-25",
                    "calculation_date": "2026-09-24",
                    "client_type": "consumer",
                    "unique_object": False,
                    "deadline_confirmed": True,
                    "acceptance_evasion": "no",
                },
                "expected": {
                    "base_rate": "0.15",
                    "delay_days_total": 55,
                    "delay_days_chargeable": 55,
                    "penalty_amount": "550000.00",
                },
                "source_ids": ["FZ214_ART6", "CBR_KEY_RATE_HISTORY"],
            },
            {
                "code": "STANDARD_2024_CAP_AND_MORATORIUM",
                "title": "Стандартный объект: cap 7,5% и мораторий с 22.03.2024",
                "input": {
                    "contract_price": "5000000",
                    "planned_transfer_date": "2024-03-01",
                    "actual_transfer_date": "2024-04-10",
                    "calculation_date": "2026-09-24",
                    "client_type": "consumer",
                    "unique_object": False,
                    "deadline_confirmed": True,
                    "acceptance_evasion": "no",
                },
                "expected": {
                    "base_rate": "0.16",
                    "delay_days_total": 40,
                    "delay_days_chargeable": 20,
                    "moratorium_days": 20,
                    "penalty_amount": "50000.00",
                },
                "source_ids": ["FZ214_ART6", "PP326", "CBR_KEY_RATE_HISTORY"],
            },
            {
                "code": "UNIQUE_OBJECT_CAP",
                "title": "Уникальный объект: отдельная ветка и лимит 5%",
                "input": {
                    "contract_price": "10000000",
                    "planned_transfer_date": "2024-12-31",
                    "actual_transfer_date": "2026-06-30",
                    "calculation_date": "2026-09-24",
                    "client_type": "consumer",
                    "unique_object": True,
                    "ddu_signing_date": "2023-08-01",
                    "deadline_confirmed": True,
                    "acceptance_evasion": "no",
                },
                "expected": {
                    "base_rate": "0.21",
                    "penalty_cap_applied": True,
                    "penalty_amount": "500000.00",
                },
                "source_ids": ["FZ214_ART6", "FZ421_ART2", "PP326"],
            },
        ],
    }


def _walk_source_ids(value: object) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "source_ids" and isinstance(child, list):
                found.update(
                    str(item).strip()
                    for item in child
                    if str(item or "").strip()
                )
            elif key != "sources":
                found.update(_walk_source_ids(child))
    elif isinstance(value, list):
        for child in value:
            found.update(_walk_source_ids(child))
    return found


def referenced_source_ids(rules: dict[str, Any]) -> set[str]:
    return _walk_source_ids(rules)


def source_registry(rules: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = rules.get("sources")
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise RuleSourceError("sources должен быть объектом source_id → metadata")
    result: dict[str, dict[str, Any]] = {}
    for raw_id, metadata in raw.items():
        source_id = str(raw_id or "").strip()
        if not source_id:
            raise RuleSourceError("Источник без source_id недопустим")
        if not isinstance(metadata, dict):
            raise RuleSourceError(f"Источник {source_id}: metadata должна быть объектом")
        result[source_id] = metadata
    return result


def validate_required_source_bindings(rules: dict[str, Any]) -> None:
    """Require every legally meaningful v2 value to name its supporting source."""

    if rules.get("schema_version") != 2:
        return

    def require_ids(value: object, title: str) -> None:
        if not isinstance(value, dict):
            raise RuleSourceError(f"{title}: раздел отсутствует")
        ids = value.get("source_ids")
        if not isinstance(ids, list) or not any(str(item or "").strip() for item in ids):
            raise RuleSourceError(f"{title}: не привязан правовой источник")

    period = rules.get("period")
    require_ids(period, "Период просрочки")
    require_ids(rules.get("rounding"), "Правило округления")

    standard = rules.get("standard_object")
    require_ids(standard, "Базовая формула")
    if not isinstance(standard, dict):
        return
    require_ids(standard.get("base_rate"), "Источник ставки")

    for index, row in enumerate(list(standard.get("rate_schedule") or []), start=1):
        require_ids(row, f"Ставка ЦБ #{index}")
    for index, row in enumerate(list(standard.get("excluded_periods") or []), start=1):
        require_ids(row, f"Исключённый период #{index}")
    for index, row in enumerate(list(standard.get("rate_cap_periods") or []), start=1):
        require_ids(row, f"Ограничение ставки #{index}")

    participant_types = standard.get("participant_types")
    if not isinstance(participant_types, dict) or not participant_types:
        raise RuleSourceError("Типы участников отсутствуют")
    for key, row in participant_types.items():
        require_ids(row, f"Тип участника {key}")

    require_ids(rules.get("unique_object"), "Уникальный объект")
    unique = rules.get("unique_object")
    if isinstance(unique, dict):
        for index, row in enumerate(list(unique.get("excluded_periods") or []), start=1):
            require_ids(row, f"Исключение уникального объекта #{index}")

    manual = rules.get("manual_review_conditions")
    if not isinstance(manual, list) or not manual:
        raise RuleSourceError("Не заданы условия ручной юридической проверки")
    for index, row in enumerate(manual, start=1):
        require_ids(row, f"Стоп-фактор #{index}")

    for index, row in enumerate(list(rules.get("judicial_adjustments") or []), start=1):
        require_ids(row, f"Судебная корректировка #{index}")

    examples = rules.get("control_examples")
    if not isinstance(examples, list) or not examples:
        raise RuleSourceError("Перед APPROVED нужен хотя бы один контрольный пример")
    for index, row in enumerate(examples, start=1):
        require_ids(row, f"Контрольный пример #{index}")


def validate_source_registry(rules: dict[str, Any], *, reject_orphans: bool = True) -> None:
    registry = source_registry(rules)
    referenced = referenced_source_ids(rules)
    missing = sorted(referenced - set(registry))
    if missing:
        raise RuleSourceError(
            "Нет карточек источников для: " + ", ".join(missing)
        )

    for source_id in sorted(referenced):
        metadata = registry[source_id]
        if not str(metadata.get("title") or "").strip():
            raise RuleSourceError(f"Источник {source_id}: не указано название")
        if not str(metadata.get("authority") or "").strip():
            raise RuleSourceError(f"Источник {source_id}: не указан орган/владелец")
        url = str(metadata.get("url") or "").strip()
        document_ref = str(metadata.get("document_ref") or "").strip()
        if not url and not document_ref:
            raise RuleSourceError(
                f"Источник {source_id}: нужна ссылка или внутренний документ"
            )
        if url:
            parsed = urlparse(url)
            if parsed.scheme != "https" or not parsed.netloc:
                raise RuleSourceError(
                    f"Источник {source_id}: разрешены только абсолютные HTTPS-ссылки"
                )

    if reject_orphans:
        orphaned = sorted(set(registry) - referenced)
        if orphaned:
            raise RuleSourceError(
                "Есть источники, не связанные ни с одним действующим параметром: "
                + ", ".join(orphaned)
            )


def prune_unused_sources(rules: dict[str, Any]) -> dict[str, Any]:
    """Delete source cards that no longer support any remaining rule value."""

    result = deepcopy(rules)
    registry = source_registry(result)
    referenced = referenced_source_ids(result)
    result["sources"] = {
        source_id: metadata
        for source_id, metadata in registry.items()
        if source_id in referenced
    }
    return result


def client_sources(
    rules: dict[str, Any],
    source_ids: list[str] | tuple[str, ...] | set[str],
) -> list[dict[str, Any]]:
    registry = source_registry(rules)
    result: list[dict[str, Any]] = []
    for source_id in sorted(set(source_ids)):
        metadata = registry.get(str(source_id))
        if not metadata or metadata.get("client_visible") is False:
            continue
        result.append({"id": source_id, **deepcopy(metadata)})
    return result


__all__ = [
    "RuleSourceError",
    "approved_v2_rule_template",
    "client_sources",
    "prune_unused_sources",
    "referenced_source_ids",
    "source_registry",
    "validate_required_source_bindings",
    "validate_source_registry",
]
