"""Runtime release *admission* boundary for Digital Legal Concierge M1/M2.

A running process can establish file presence and health, but cannot independently
attest to off-platform legal consent, live Telegram and payment UAT, signed
acceptance, or an independent human review. The old v25/v27 pages treated static
configuration checks as a launch authorization.

Until a separately reviewed, tamper-resistant evidence authority is integrated,
this projection MUST remain fail-closed. It does not mutate environment, DB,
provider state, or GitHub evidence and does not claim a live approval.
"""
from __future__ import annotations

from typing import Any

MANDATORY_EXTERNAL_GATES: tuple[tuple[str, str], ...] = (
    ("source_review", "Независимое ревью и объединённый SHA релиз-кандидата"),
    ("legal_b007", "Утверждённые тексты согласия B-007, оператор ПД и опубликованный SHA"),
    ("legal_calculator", "Юридически проверенные ставки, моратории и исключения"),
    ("privacy_retention", "Решение по историческому аудиту, удалению ПД и резервным копиям"),
    ("archive_delivery", "Утверждённый безопасный канал выдачи архивных документов"),
    ("staging", "Изолированный Timeweb staging и восстановление БД/хранилища"),
    ("telegram", "Реальные Telegram M1/M2 и проверка разных учётных записей"),
    ("payments", "Проверенные sandbox webhooks YooKassa и сопоставление с ledger"),
    ("external_uat", "Подписанные сценарии UAT-01…UAT-16"),
    ("operator", "Подписанный регламент запуска, мониторинга и отката"),
)


def runtime_release_admission() -> dict[str, Any]:
    """Truthful local projection: NO autonomous production/customer approval.

    The values do not infer evidence from env vars, a healthy /ready, a green CI
    message, or a manually supplied string. External authority is independently
    reviewed at a later stage; returning READY here would be a false statement.
    """
    return {
        "status": "HOLD",
        "release_authorized": False,
        "customer_accepted": False,
        "automated_source_checks_are_not_live_uat": True,
        "reason": (
            "Наличие настроек и зелёный CI не подтверждают юридическое "
            "согласование, оплату, реальный Telegram, UAT и разрешение владельца."
        ),
        "scope": ["M1", "M2"],
        "required_external_evidence": [
            {"code": code, "title": title, "status": "NOT_VERIFIED_HERE"}
            for code, title in MANDATORY_EXTERNAL_GATES
        ],
        "reference": "docs/MVP_OPERATIONAL_ACCEPTANCE_EVIDENCE_2026-10-12.md",
    }
