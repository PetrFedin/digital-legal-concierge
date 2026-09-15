from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.rule_engine import CalculationRuleError, _parse_rule_payload
from app.models.calculation_rule_revision import CalculationRuleRevision


class CalculationRuleRevisionError(CalculationRuleError):
    """Rule revision is absent, ambiguous, unapproved or tampered."""


def canonical_rule_json(rules: dict[str, Any]) -> str:
    try:
        return json.dumps(
            rules,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise CalculationRuleRevisionError(
            "Набор правил должен быть канонизируемым JSON без NaN/Infinity"
        ) from error


def rule_payload_sha256(rules: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_rule_json(rules).encode("utf-8")).hexdigest()


def validate_rule_payload(rules: dict[str, Any]) -> None:
    if not isinstance(rules, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    client_types = rules.get("client_types")
    if not isinstance(client_types, dict) or not client_types:
        raise CalculationRuleRevisionError(
            "В наборе правил должен быть хотя бы один тип клиента"
        )
    for client_type in client_types:
        _parse_rule_payload(rules, client_type=str(client_type))
    # Canonicalization is part of approval: a revision that cannot be hashed
    # reproducibly cannot become legal calculation authority.
    canonical_rule_json(rules)


class CalculationRuleRevisionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_draft(
        self,
        *,
        revision_key: str,
        effective_from: date,
        effective_to: date | None,
        rules: dict[str, Any],
        note: str | None = None,
    ) -> CalculationRuleRevision:
        key = str(revision_key or "").strip()
        if not key:
            raise CalculationRuleRevisionError("revision_key обязателен")
        if effective_to is not None and effective_to < effective_from:
            raise CalculationRuleRevisionError("effective_to не может быть раньше effective_from")
        validate_rule_payload(rules)
        revision = CalculationRuleRevision(
            revision_key=key,
            status="DRAFT",
            effective_from=effective_from,
            effective_to=effective_to,
            rules=rules,
            rules_sha256=rule_payload_sha256(rules),
            note=note,
        )
        self.db.add(revision)
        await self.db.flush()
        return revision

    async def approve(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        if str(revision.status).upper() != "DRAFT":
            raise CalculationRuleRevisionError(
                "Утверждать можно только DRAFT-ревизию; утверждённая ревизия не редактируется"
            )
        validate_rule_payload(revision.rules)
        current_hash = rule_payload_sha256(revision.rules)
        if current_hash != str(revision.rules_sha256 or ""):
            raise CalculationRuleRevisionError(
                "Хеш набора правил не совпадает: ревизия изменена после фиксации"
            )
        if revision.effective_to is not None and revision.effective_to < revision.effective_from:
            raise CalculationRuleRevisionError("Некорректный период действия ревизии")

        overlap_conditions = [
            CalculationRuleRevision.status == "APPROVED",
            CalculationRuleRevision.id != revision.id,
            or_(
                CalculationRuleRevision.effective_to.is_(None),
                CalculationRuleRevision.effective_to >= revision.effective_from,
            ),
        ]
        if revision.effective_to is not None:
            overlap_conditions.append(
                CalculationRuleRevision.effective_from <= revision.effective_to
            )
        existing = (
            await self.db.execute(
                select(CalculationRuleRevision.id)
                .where(*overlap_conditions)
                .limit(1)
            )
        ).scalar_one_or_none()
        if existing is not None:
            raise CalculationRuleRevisionError(
                "Период действия пересекается с другой APPROVED-ревизией"
            )

        revision.status = "APPROVED"
        revision.approved_by_actor_type = str(actor_type or "").strip() or "staff"
        revision.approved_by_actor_id = int(actor_id)
        revision.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        return revision

    async def resolve(self, *, calculation_date: date) -> CalculationRuleRevision:
        statement = (
            select(CalculationRuleRevision)
            .where(
                CalculationRuleRevision.status == "APPROVED",
                CalculationRuleRevision.approved_at.is_not(None),
                CalculationRuleRevision.effective_from <= calculation_date,
                or_(
                    CalculationRuleRevision.effective_to.is_(None),
                    CalculationRuleRevision.effective_to >= calculation_date,
                ),
            )
            .order_by(
                CalculationRuleRevision.effective_from.desc(),
                CalculationRuleRevision.id.desc(),
            )
            .limit(2)
        )
        revisions = list((await self.db.execute(statement)).scalars().all())
        if not revisions:
            raise CalculationRuleRevisionError(
                "Для даты расчёта нет утверждённой ревизии юридических правил"
            )
        if len(revisions) > 1:
            raise CalculationRuleRevisionError(
                "Для даты расчёта найдено несколько утверждённых ревизий; расчёт остановлен"
            )
        revision = revisions[0]
        validate_rule_payload(revision.rules)
        current_hash = rule_payload_sha256(revision.rules)
        if current_hash != str(revision.rules_sha256 or ""):
            raise CalculationRuleRevisionError(
                "Утверждённая ревизия правил не прошла проверку целостности"
            )
        return revision


__all__ = [
    "CalculationRuleRevisionError",
    "CalculationRuleRevisionService",
    "canonical_rule_json",
    "rule_payload_sha256",
    "validate_rule_payload",
]
