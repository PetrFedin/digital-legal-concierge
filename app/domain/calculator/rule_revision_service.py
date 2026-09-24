from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.rule_catalog_v2 import (
    RuleSourceError,
    prune_unused_sources,
    validate_required_source_bindings,
    validate_source_registry,
)
from app.domain.calculator.rule_engine import (
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
    _parse_rule_payload,
)
from app.models.audit_log import AuditLog
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


def _validate_control_examples(rules: dict[str, Any]) -> None:
    if rules.get("schema_version") != 2:
        return
    engine = CalculationRuleEngine()
    examples = rules.get("control_examples")
    if not isinstance(examples, list) or not examples:
        raise CalculationRuleRevisionError(
            "Перед APPROVED требуется хотя бы один контрольный расчёт"
        )
    for index, example in enumerate(examples, start=1):
        if not isinstance(example, dict):
            raise CalculationRuleRevisionError(
                f"Контрольный пример #{index} должен быть объектом"
            )
        raw = example.get("input")
        expected = example.get("expected")
        if not isinstance(raw, dict) or not isinstance(expected, dict):
            raise CalculationRuleRevisionError(
                f"Контрольный пример #{index}: нужны input и expected"
            )
        try:
            result = engine.calculate(
                RuleBasedCalculationInput(
                    contract_price=Decimal(str(raw["contract_price"])),
                    planned_transfer_date=date.fromisoformat(str(raw["planned_transfer_date"])),
                    calculation_date=date.fromisoformat(str(raw["calculation_date"])),
                    object_transferred=bool(raw.get("object_transferred", True)),
                    actual_transfer_date=(
                        date.fromisoformat(str(raw["actual_transfer_date"]))
                        if raw.get("actual_transfer_date")
                        else None
                    ),
                    client_type=str(raw.get("client_type") or "consumer"),
                    unique_object=raw.get("unique_object"),
                    acceptance_evasion=raw.get("acceptance_evasion"),
                    deadline_confirmed=raw.get("deadline_confirmed"),
                    ddu_signing_date=(
                        date.fromisoformat(str(raw["ddu_signing_date"]))
                        if raw.get("ddu_signing_date")
                        else None
                    ),
                ),
                rule_revision_id=0,
                rule_revision_key="APPROVAL-CONTROL",
                rule_snapshot_sha256="control",
                rule_snapshot=rules,
            )
        except (KeyError, ValueError, CalculationRuleError) as error:
            raise CalculationRuleRevisionError(
                f"Контрольный пример #{index} не выполняется: {error}"
            ) from error

        comparable = {
            "base_rate": (
                str(result.base_rate) if result.base_rate is not None else None
            ),
            "delay_days_total": result.delay_days_total,
            "delay_days_chargeable": result.delay_days_chargeable,
            "moratorium_days": result.moratorium_days,
            "penalty_amount": str(result.penalty_amount),
            "penalty_cap_applied": result.penalty_cap_applied,
            "calculation_branch": result.calculation_branch,
        }
        for key, expected_value in expected.items():
            if key not in comparable:
                continue
            actual = comparable[key]
            if str(actual) != str(expected_value):
                raise CalculationRuleRevisionError(
                    f"Контрольный пример #{index}: {key} ожидалось "
                    f"{expected_value!r}, получено {actual!r}"
                )


def validate_draft_payload(rules: dict[str, Any]) -> None:
    """Allow an intentionally incomplete DRAFT while preserving safe structure.

    Clearing a rule value in the editor is a legitimate draft operation. It must
    not leave a stale source card behind, but it may make the draft temporarily
    non-approvable. Strict legal/calculation validation is performed at APPROVE.
    """

    if not isinstance(rules, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    version = rules.get("schema_version")
    if version not in {1, 2}:
        raise CalculationRuleRevisionError("Поддерживаются schema_version 1 и 2")
    canonical_rule_json(rules)
    if version == 2:
        try:
            validate_source_registry(rules, reject_orphans=True)
        except RuleSourceError as error:
            raise CalculationRuleRevisionError(str(error)) from error


def validate_rule_payload(rules: dict[str, Any]) -> None:
    """Validate the complete immutable payload before APPROVED/runtime use."""

    if not isinstance(rules, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    version = rules.get("schema_version")

    if version == 1:
        client_types = rules.get("client_types")
        if not isinstance(client_types, dict) or not client_types:
            raise CalculationRuleRevisionError(
                "В наборе правил должен быть хотя бы один тип клиента"
            )
        for client_type in client_types:
            _parse_rule_payload(rules, client_type=str(client_type))
    elif version == 2:
        standard = rules.get("standard_object")
        if not isinstance(standard, dict):
            raise CalculationRuleRevisionError("Не задана стандартная ветка расчёта")
        participant_types = standard.get("participant_types")
        if not isinstance(participant_types, dict) or not participant_types:
            raise CalculationRuleRevisionError("Не заданы типы участников")
        try:
            validate_required_source_bindings(rules)
            validate_source_registry(rules, reject_orphans=True)
        except RuleSourceError as error:
            raise CalculationRuleRevisionError(str(error)) from error
        for client_type in participant_types:
            _parse_rule_payload(rules, client_type=str(client_type))
    else:
        raise CalculationRuleRevisionError("Неподдерживаемая версия схемы правил")

    _validate_control_examples(rules)
    # Canonicalization is part of approval: a revision that cannot be hashed
    # reproducibly cannot become legal calculation authority.
    canonical_rule_json(rules)


def _audit_value(revision: CalculationRuleRevision) -> dict[str, Any]:
    return {
        "revision_key": revision.revision_key,
        "status": revision.status,
        "effective_from": revision.effective_from.isoformat(),
        "effective_to": (
            revision.effective_to.isoformat() if revision.effective_to else None
        ),
        "rules_sha256": revision.rules_sha256,
        "approved_by_actor_type": revision.approved_by_actor_type,
        "approved_by_actor_id": revision.approved_by_actor_id,
        "approved_at": (
            revision.approved_at.isoformat() if revision.approved_at else None
        ),
        "note": revision.note,
    }


def _parse_expected_updated_at(value: str | datetime | None) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as error:
        raise CalculationRuleRevisionError(
            "Некорректная версия редактируемого черновика"
        ) from error


def _same_moment(left: datetime | None, right: datetime | None) -> bool:
    if left is None or right is None:
        return left is right
    # SQLite often round-trips timezone-naive timestamps while PostgreSQL keeps
    # timezone information. Compare normalized wall-clock UTC-compatible values
    # rather than making optimistic concurrency backend-specific.
    if left.tzinfo is not None:
        left = left.astimezone(timezone.utc).replace(tzinfo=None)
    if right.tzinfo is not None:
        right = right.astimezone(timezone.utc).replace(tzinfo=None)
    return left == right


class CalculationRuleRevisionService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def _audit(
        self,
        *,
        revision: CalculationRuleRevision,
        action: str,
        actor_type: str,
        actor_id: int | None,
        old_value: dict[str, Any] | None,
        comment: str,
    ) -> None:
        self.db.add(
            AuditLog(
                actor_type=str(actor_type or "staff").strip() or "staff",
                actor_id=actor_id,
                action=action,
                entity_type="calculation_rule_revision",
                entity_id=int(revision.id) if revision.id is not None else None,
                old_value=old_value,
                new_value=_audit_value(revision),
                comment=comment,
            )
        )

    async def create_draft(
        self,
        *,
        revision_key: str,
        effective_from: date,
        effective_to: date | None,
        rules: dict[str, Any],
        note: str | None = None,
        actor_type: str = "staff",
        actor_id: int | None = None,
    ) -> CalculationRuleRevision:
        key = str(revision_key or "").strip()
        if not key:
            raise CalculationRuleRevisionError("revision_key обязателен")
        if effective_to is not None and effective_to < effective_from:
            raise CalculationRuleRevisionError("effective_to не может быть раньше effective_from")
        if rules.get("schema_version") == 2:
            rules = prune_unused_sources(rules)
        validate_draft_payload(rules)
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
        self._audit(
            revision=revision,
            action="CALCULATION_RULE_DRAFT_CREATED",
            actor_type=actor_type,
            actor_id=actor_id,
            old_value=None,
            comment="Создан черновик редакции правил предварительного расчёта",
        )
        await self.db.flush()
        return revision

    async def get_for_update(self, *, revision_id: int) -> CalculationRuleRevision:
        revision = (
            await self.db.execute(
                select(CalculationRuleRevision)
                .where(CalculationRuleRevision.id == int(revision_id))
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalar_one_or_none()
        if revision is None:
            raise CalculationRuleRevisionError("Ревизия правил не найдена")
        return revision

    async def update_draft(
        self,
        *,
        revision_id: int,
        effective_from: date,
        effective_to: date | None,
        rules: dict[str, Any],
        note: str | None,
        expected_updated_at: str | datetime | None,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        """Edit only DRAFT content with optimistic conflict detection.

        ``revision_key`` is intentionally immutable. An approved revision is
        never edited; a legal change is represented by another revision.
        """

        revision = await self.get_for_update(revision_id=revision_id)
        if str(revision.status).upper() != "DRAFT":
            raise CalculationRuleRevisionError(
                "Редактировать можно только DRAFT-ревизию"
            )
        expected = _parse_expected_updated_at(expected_updated_at)
        if expected is not None and not _same_moment(revision.updated_at, expected):
            raise CalculationRuleRevisionError(
                "Черновик уже изменён другим пользователем; обновите страницу"
            )
        if effective_to is not None and effective_to < effective_from:
            raise CalculationRuleRevisionError("effective_to не может быть раньше effective_from")
        if rules.get("schema_version") == 2:
            rules = prune_unused_sources(rules)
        validate_draft_payload(rules)

        before = _audit_value(revision)
        revision.effective_from = effective_from
        revision.effective_to = effective_to
        revision.rules = rules
        revision.rules_sha256 = rule_payload_sha256(rules)
        revision.note = note
        await self.db.flush()
        self._audit(
            revision=revision,
            action="CALCULATION_RULE_DRAFT_UPDATED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Изменён черновик редакции правил предварительного расчёта",
        )
        await self.db.flush()
        return revision

    async def _lock_all_revisions(self) -> list[CalculationRuleRevision]:
        # Approval is a very low-volume staff operation. Serializing the small
        # directory is preferable to allowing two concurrent transactions to
        # both observe no overlapping APPROVED row and create ambiguous legal
        # authority. Stable ordering avoids lock-order deadlocks.
        statement = (
            select(CalculationRuleRevision)
            .order_by(CalculationRuleRevision.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.db.execute(statement)).scalars().all())

    async def approve(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        locked = await self._lock_all_revisions()
        current = next((item for item in locked if item.id == revision.id), None)
        if current is None:
            raise CalculationRuleRevisionError("Ревизия правил не найдена")
        revision = current

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

        for existing in locked:
            if existing.id == revision.id or str(existing.status).upper() != "APPROVED":
                continue
            starts_before_target_ends = (
                revision.effective_to is None
                or existing.effective_from <= revision.effective_to
            )
            target_starts_before_existing_ends = (
                existing.effective_to is None
                or existing.effective_to >= revision.effective_from
            )
            if starts_before_target_ends and target_starts_before_existing_ends:
                raise CalculationRuleRevisionError(
                    "Период действия пересекается с другой APPROVED-ревизией"
                )

        before = _audit_value(revision)
        revision.status = "APPROVED"
        revision.approved_by_actor_type = str(actor_type or "").strip() or "staff"
        revision.approved_by_actor_id = int(actor_id)
        revision.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        self._audit(
            revision=revision,
            action="CALCULATION_RULE_APPROVED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Утверждена редакция юридических правил предварительного расчёта",
        )
        await self.db.flush()
        return revision

    async def retire(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        locked = await self._lock_all_revisions()
        current = next((item for item in locked if item.id == revision.id), None)
        if current is None:
            raise CalculationRuleRevisionError("Ревизия правил не найдена")
        revision = current
        if str(revision.status).upper() != "APPROVED":
            raise CalculationRuleRevisionError(
                "В архив можно перевести только APPROVED-ревизию"
            )
        before = _audit_value(revision)
        revision.status = "RETIRED"
        await self.db.flush()
        self._audit(
            revision=revision,
            action="CALCULATION_RULE_RETIRED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Редакция правил исключена из новых расчётов; исторические расчёты не изменены",
        )
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
    "validate_draft_payload",
    "validate_rule_payload",
]
