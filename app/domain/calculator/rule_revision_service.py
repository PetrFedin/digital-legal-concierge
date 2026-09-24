from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.rule_engine import (
    CalculationManualReviewRequired,
    CalculationRuleEngine,
    CalculationRuleError,
    RuleBasedCalculationInput,
    _parse_rule_payload,
)
from app.models.audit_log import AuditLog
from app.models.calculation_rule_revision import CalculationRuleRevision


class CalculationRuleRevisionError(CalculationRuleError):
    """Rule revision is absent, ambiguous, unapproved or tampered."""


_EDITABLE_SECTIONS = frozenset(
    {
        "formula",
        "rate_policy",
        "rate_directory",
        "rate_caps",
        "moratoria",
        "client_types",
        "unique_object",
        "stop_factors",
        "control_examples",
    }
)


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


def _validate_draft_payload(rules: dict[str, Any]) -> None:
    if not isinstance(rules, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    if rules.get("schema_version") not in {1, 2}:
        raise CalculationRuleRevisionError(
            "DRAFT должен явно указывать поддерживаемый schema_version"
        )
    canonical_rule_json(rules)


def validate_rule_payload(rules: dict[str, Any]) -> None:
    """Validate a complete rule payload.

    Legacy schema v1 remains readable for historical/tests compatibility. New
    legal review/publication is restricted separately to schema v2.
    """

    if not isinstance(rules, dict):
        raise CalculationRuleRevisionError("Набор правил должен быть JSON-объектом")
    client_types = rules.get("client_types")
    if not isinstance(client_types, dict) or not client_types:
        raise CalculationRuleRevisionError(
            "В наборе правил должен быть хотя бы один тип клиента"
        )
    for client_type in client_types:
        _parse_rule_payload(rules, client_type=str(client_type))
    canonical_rule_json(rules)


def _control_example_input(raw: dict[str, Any]) -> RuleBasedCalculationInput:
    try:
        contract_price = Decimal(str(raw["contract_price"]))
        planned_transfer_date = date.fromisoformat(str(raw["planned_transfer_date"]))
        calculation_date = date.fromisoformat(str(raw["calculation_date"]))
        object_transferred = bool(raw["object_transferred"])
        actual_raw = raw.get("actual_transfer_date")
        actual_transfer_date = (
            date.fromisoformat(str(actual_raw)) if actual_raw not in (None, "") else None
        )
    except (KeyError, ValueError, TypeError) as error:
        raise CalculationRuleRevisionError(
            "Контрольный пример содержит некорректные входные данные"
        ) from error
    flags_raw = raw.get("manual_review_flags", [])
    if not isinstance(flags_raw, list):
        raise CalculationRuleRevisionError(
            "manual_review_flags контрольного примера должен быть списком"
        )
    return RuleBasedCalculationInput(
        contract_price=contract_price,
        planned_transfer_date=planned_transfer_date,
        calculation_date=calculation_date,
        object_transferred=object_transferred,
        actual_transfer_date=actual_transfer_date,
        client_type=str(raw.get("client_type") or "consumer"),
        unique_object=bool(raw.get("unique_object", False)),
        manual_review_flags=tuple(str(item) for item in flags_raw),
    )


def run_control_examples(rules: dict[str, Any]) -> list[dict[str, Any]]:
    """Execute deterministic legal examples before legal review/publication."""

    if rules.get("schema_version") != 2:
        raise CalculationRuleRevisionError(
            "Юридическое подтверждение доступно только для schema_version=2"
        )
    validate_rule_payload(rules)
    sources = rules.get("sources")
    raw_examples = rules.get("control_examples")
    if not isinstance(raw_examples, list) or not raw_examples:
        raise CalculationRuleRevisionError(
            "Для юридического подтверждения нужен хотя бы один контрольный пример"
        )

    engine = CalculationRuleEngine()
    outcomes: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_examples):
        if not isinstance(raw, dict):
            raise CalculationRuleRevisionError(
                f"Контрольный пример #{index + 1}: ожидается объект"
            )
        code = str(raw.get("code") or f"example-{index + 1}")
        source_refs = raw.get("source_refs")
        if not isinstance(source_refs, list) or not source_refs:
            raise CalculationRuleRevisionError(
                f"Контрольный пример {code}: нужны source_refs"
            )
        for ref in source_refs:
            if not isinstance(sources, dict) or str(ref) not in sources:
                raise CalculationRuleRevisionError(
                    f"Контрольный пример {code}: неизвестный источник {ref!r}"
                )
        expected = raw.get("expected")
        if not isinstance(expected, dict):
            raise CalculationRuleRevisionError(
                f"Контрольный пример {code}: не задан expected"
            )

        data = _control_example_input(raw.get("input") or {})
        expects_manual = bool(expected.get("manual_review_required", False))
        try:
            result = engine.calculate(
                data,
                rule_revision_id=0,
                rule_revision_key="CONTROL",
                rule_snapshot_sha256=rule_payload_sha256(rules),
                rule_snapshot=rules,
            )
        except CalculationManualReviewRequired as error:
            if not expects_manual:
                raise CalculationRuleRevisionError(
                    f"Контрольный пример {code}: неожиданно требует ручной проверки"
                ) from error
            outcomes.append(
                {
                    "code": code,
                    "passed": True,
                    "manual_review_required": True,
                    "manual_review_reasons": list(error.reasons),
                }
            )
            continue

        if expects_manual:
            raise CalculationRuleRevisionError(
                f"Контрольный пример {code}: ожидалась ручная проверка"
            )

        checks: dict[str, Any] = {
            "penalty_amount": str(result.penalty_amount),
            "delay_days_total": int(result.delay_days_total),
            "delay_days_chargeable": int(result.delay_days_chargeable),
            "moratorium_days": int(result.moratorium_days),
            "base_rate": str(result.key_rate) if result.key_rate is not None else None,
            "amount_cap_applied": bool(result.amount_cap_applied),
        }
        for key, actual in checks.items():
            if key not in expected:
                continue
            wanted = expected[key]
            if key in {"penalty_amount", "base_rate"}:
                if wanted is None and actual is None:
                    continue
                if Decimal(str(wanted)) != Decimal(str(actual)):
                    raise CalculationRuleRevisionError(
                        f"Контрольный пример {code}: {key}={actual}, ожидалось {wanted}"
                    )
            elif key in {"delay_days_total", "delay_days_chargeable", "moratorium_days"}:
                if int(wanted) != int(actual):
                    raise CalculationRuleRevisionError(
                        f"Контрольный пример {code}: {key}={actual}, ожидалось {wanted}"
                    )
            elif bool(wanted) != bool(actual):
                raise CalculationRuleRevisionError(
                    f"Контрольный пример {code}: {key}={actual}, ожидалось {wanted}"
                )
        outcomes.append({"code": code, "passed": True, **checks})
    return outcomes


def _collect_source_refs(value: object) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "sources":
                continue
            if key == "source_refs" and isinstance(item, list):
                refs.update(str(ref) for ref in item if str(ref).strip())
            else:
                refs.update(_collect_source_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_source_refs(item))
    return refs


def _prune_orphan_sources(rules: dict[str, Any]) -> dict[str, Any]:
    next_rules = deepcopy(rules)
    sources = next_rules.get("sources")
    if not isinstance(sources, dict):
        return next_rules
    used = _collect_source_refs(next_rules)
    next_rules["sources"] = {
        key: value
        for key, value in sources.items()
        if str(key) in used
    }
    return next_rules


def _audit_value(revision: CalculationRuleRevision) -> dict[str, Any]:
    return {
        "revision_key": revision.revision_key,
        "status": revision.status,
        "effective_from": revision.effective_from.isoformat(),
        "effective_to": (
            revision.effective_to.isoformat() if revision.effective_to else None
        ),
        "rules_sha256": revision.rules_sha256,
        "legal_reviewed_by_actor_type": revision.legal_reviewed_by_actor_type,
        "legal_reviewed_by_actor_id": revision.legal_reviewed_by_actor_id,
        "legal_reviewed_at": (
            revision.legal_reviewed_at.isoformat()
            if revision.legal_reviewed_at
            else None
        ),
        "legal_review_sha256": revision.legal_review_sha256,
        "approved_by_actor_type": revision.approved_by_actor_type,
        "approved_by_actor_id": revision.approved_by_actor_id,
        "approved_at": (
            revision.approved_at.isoformat() if revision.approved_at else None
        ),
        "published_by_actor_type": revision.published_by_actor_type,
        "published_by_actor_id": revision.published_by_actor_id,
        "published_at": (
            revision.published_at.isoformat() if revision.published_at else None
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
            raise CalculationRuleRevisionError(
                "effective_to не может быть раньше effective_from"
            )
        _validate_draft_payload(rules)
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
            raise CalculationRuleRevisionError(
                "effective_to не может быть раньше effective_from"
            )
        _validate_draft_payload(rules)

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

    async def clear_draft_section(
        self,
        *,
        revision_id: int,
        section: str,
        expected_updated_at: str | datetime | None,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        key = str(section or "").strip()
        if key not in _EDITABLE_SECTIONS:
            raise CalculationRuleRevisionError("Этот раздел нельзя очищать отдельно")
        revision = await self.get_for_update(revision_id=revision_id)
        if str(revision.status).upper() != "DRAFT":
            raise CalculationRuleRevisionError("Очищать можно только DRAFT-ревизию")
        expected = _parse_expected_updated_at(expected_updated_at)
        if expected is not None and not _same_moment(revision.updated_at, expected):
            raise CalculationRuleRevisionError(
                "Черновик уже изменён другим пользователем; обновите страницу"
            )

        before = _audit_value(revision)
        next_rules = deepcopy(revision.rules)
        next_rules.pop(key, None)
        next_rules = _prune_orphan_sources(next_rules)
        _validate_draft_payload(next_rules)
        revision.rules = next_rules
        revision.rules_sha256 = rule_payload_sha256(next_rules)
        await self.db.flush()
        self._audit(
            revision=revision,
            action="CALCULATION_RULE_DRAFT_SECTION_CLEARED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment=(
                f"Очищен раздел {key}; неиспользуемые связанные источники удалены из DRAFT"
            ),
        )
        await self.db.flush()
        return revision

    async def _lock_all_revisions(self) -> list[CalculationRuleRevision]:
        statement = (
            select(CalculationRuleRevision)
            .order_by(CalculationRuleRevision.id.asc())
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return list((await self.db.execute(statement)).scalars().all())

    async def confirm_legal_review(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
        comment: str,
    ) -> CalculationRuleRevision:
        current = await self.get_for_update(revision_id=int(revision.id))
        if str(current.status).upper() != "DRAFT":
            raise CalculationRuleRevisionError(
                "Юридически подтверждать можно только DRAFT-ревизию"
            )
        if current.rules.get("schema_version") != 2:
            raise CalculationRuleRevisionError(
                "Юридическое подтверждение доступно только для schema_version=2"
            )
        validate_rule_payload(current.rules)
        run_control_examples(current.rules)
        current_hash = rule_payload_sha256(current.rules)
        if current_hash != str(current.rules_sha256 or ""):
            raise CalculationRuleRevisionError(
                "Хеш набора правил не совпадает: DRAFT изменён после фиксации"
            )

        before = _audit_value(current)
        current.status = "LEGAL_REVIEWED"
        current.legal_reviewed_by_actor_type = str(actor_type or "").strip() or "lawyer"
        current.legal_reviewed_by_actor_id = int(actor_id)
        current.legal_reviewed_at = datetime.now(timezone.utc)
        current.legal_review_sha256 = current_hash
        current.legal_review_comment = str(comment or "").strip() or None
        await self.db.flush()
        self._audit(
            revision=current,
            action="CALCULATION_RULE_LEGAL_REVIEW_CONFIRMED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Юрист подтвердил точную SHA-256 редакцию правил и контрольные примеры",
        )
        await self.db.flush()
        return current

    async def return_to_draft(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
        comment: str,
    ) -> CalculationRuleRevision:
        current = await self.get_for_update(revision_id=int(revision.id))
        status = str(current.status).upper()
        if status not in {"LEGAL_REVIEWED", "APPROVED"}:
            raise CalculationRuleRevisionError(
                "Вернуть в DRAFT можно только LEGAL_REVIEWED или APPROVED до публикации"
            )
        if current.published_at is not None:
            raise CalculationRuleRevisionError(
                "Опубликованную редакцию нельзя менять; создайте новую ревизию"
            )

        before = _audit_value(current)
        current.status = "DRAFT"
        current.legal_reviewed_by_actor_type = None
        current.legal_reviewed_by_actor_id = None
        current.legal_reviewed_at = None
        current.legal_review_sha256 = None
        current.legal_review_comment = None
        current.approved_by_actor_type = None
        current.approved_by_actor_id = None
        current.approved_at = None
        await self.db.flush()
        self._audit(
            revision=current,
            action="CALCULATION_RULE_RETURNED_TO_DRAFT",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment=str(comment or "").strip() or "Редакция возвращена в DRAFT",
        )
        await self.db.flush()
        return current

    async def approve(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        current = await self.get_for_update(revision_id=int(revision.id))
        if str(current.status).upper() != "LEGAL_REVIEWED":
            raise CalculationRuleRevisionError(
                "APPROVED возможен только после юридического подтверждения"
            )
        validate_rule_payload(current.rules)
        run_control_examples(current.rules)
        current_hash = rule_payload_sha256(current.rules)
        if current_hash != str(current.rules_sha256 or ""):
            raise CalculationRuleRevisionError(
                "Хеш набора правил не совпадает: редакция изменена после фиксации"
            )
        if current_hash != str(current.legal_review_sha256 or ""):
            raise CalculationRuleRevisionError(
                "Юридическое подтверждение относится к другой SHA-256 редакции"
            )
        if current.effective_to is not None and current.effective_to < current.effective_from:
            raise CalculationRuleRevisionError("Некорректный период действия ревизии")

        before = _audit_value(current)
        current.status = "APPROVED"
        current.approved_by_actor_type = str(actor_type or "").strip() or "staff"
        current.approved_by_actor_id = int(actor_id)
        current.approved_at = datetime.now(timezone.utc)
        await self.db.flush()
        self._audit(
            revision=current,
            action="CALCULATION_RULE_APPROVED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Редакция административно утверждена, но ещё не опубликована в production",
        )
        await self.db.flush()
        return current

    async def publish(
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
        if str(current.status).upper() != "APPROVED":
            raise CalculationRuleRevisionError(
                "В production можно публиковать только APPROVED-ревизию"
            )
        validate_rule_payload(current.rules)
        run_control_examples(current.rules)
        current_hash = rule_payload_sha256(current.rules)
        if (
            current_hash != str(current.rules_sha256 or "")
            or current_hash != str(current.legal_review_sha256 or "")
        ):
            raise CalculationRuleRevisionError(
                "Публикация остановлена: SHA-256 не совпадает с юридически подтверждённой редакцией"
            )

        for existing in locked:
            if existing.id == current.id or str(existing.status).upper() != "PRODUCTION":
                continue
            starts_before_target_ends = (
                current.effective_to is None
                or existing.effective_from <= current.effective_to
            )
            target_starts_before_existing_ends = (
                existing.effective_to is None
                or existing.effective_to >= current.effective_from
            )
            if starts_before_target_ends and target_starts_before_existing_ends:
                raise CalculationRuleRevisionError(
                    "Период действия пересекается с другой PRODUCTION-ревизией"
                )

        before = _audit_value(current)
        current.status = "PRODUCTION"
        current.published_by_actor_type = str(actor_type or "").strip() or "staff"
        current.published_by_actor_id = int(actor_id)
        current.published_at = datetime.now(timezone.utc)
        await self.db.flush()
        self._audit(
            revision=current,
            action="CALCULATION_RULE_PUBLISHED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Утверждённая редакция опубликована как production authority",
        )
        await self.db.flush()
        return current

    async def retire(
        self,
        *,
        revision: CalculationRuleRevision,
        actor_type: str,
        actor_id: int,
    ) -> CalculationRuleRevision:
        current = await self.get_for_update(revision_id=int(revision.id))
        if str(current.status).upper() != "PRODUCTION":
            raise CalculationRuleRevisionError(
                "В архив можно перевести только PRODUCTION-ревизию"
            )
        before = _audit_value(current)
        current.status = "RETIRED"
        await self.db.flush()
        self._audit(
            revision=current,
            action="CALCULATION_RULE_RETIRED",
            actor_type=actor_type,
            actor_id=int(actor_id),
            old_value=before,
            comment="Редакция исключена из новых расчётов; исторические расчёты не изменены",
        )
        await self.db.flush()
        return current

    async def resolve(self, *, calculation_date: date) -> CalculationRuleRevision:
        statement = (
            select(CalculationRuleRevision)
            .where(
                CalculationRuleRevision.status == "PRODUCTION",
                CalculationRuleRevision.published_at.is_not(None),
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
                "Для даты расчёта нет опубликованной production-ревизии юридических правил"
            )
        if len(revisions) > 1:
            raise CalculationRuleRevisionError(
                "Для даты расчёта найдено несколько production-ревизий; расчёт остановлен"
            )
        revision = revisions[0]
        if revision.rules.get("schema_version") != 2:
            raise CalculationRuleRevisionError(
                "Production-ревизия использует устаревшую схему правил"
            )
        validate_rule_payload(revision.rules)
        current_hash = rule_payload_sha256(revision.rules)
        if (
            current_hash != str(revision.rules_sha256 or "")
            or current_hash != str(revision.legal_review_sha256 or "")
        ):
            raise CalculationRuleRevisionError(
                "Production-ревизия не прошла проверку целостности и юридического SHA-256"
            )
        return revision


__all__ = [
    "CalculationRuleRevisionError",
    "CalculationRuleRevisionService",
    "canonical_rule_json",
    "rule_payload_sha256",
    "run_control_examples",
    "validate_rule_payload",
]
