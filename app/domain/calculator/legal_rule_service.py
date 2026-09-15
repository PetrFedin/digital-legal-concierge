from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.calculator.legal_rule_errors import (
    CalculationRuleAmbiguousError,
    CalculationRuleUnavailableError,
    CalculationRuleValidationError,
    ImmutableCalculationRuleError,
)
from app.models.audit_log import AuditLog
from app.models.calculation_rule import (
    RULE_STATUS_APPROVED,
    RULE_STATUS_DRAFT,
    RULE_STATUS_RETIRED,
    CalculationClientTypeRule,
    CalculationDateRule,
    CalculationExclusionPeriod,
    CalculationRatePeriod,
    CalculationRuleSet,
)

_UNSET = object()


def _clean_code(value: str, *, field: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise CalculationRuleValidationError(f"{field} не может быть пустым")
    if len(cleaned) > 100:
        raise CalculationRuleValidationError(f"{field} длиннее 100 символов")
    return cleaned


def _validate_effective_range(
    effective_from: date,
    effective_to: date | None,
    *,
    label: str,
) -> None:
    if effective_to is not None and effective_to < effective_from:
        raise CalculationRuleValidationError(
            f"{label}: дата окончания раньше даты начала"
        )


def _periods_overlap(
    left_from: date,
    left_to: date | None,
    right_from: date,
    right_to: date | None,
) -> bool:
    return (left_to is None or left_to >= right_from) and (
        right_to is None or right_to >= left_from
    )


def _effective_on(
    effective_from: date,
    effective_to: date | None,
    as_of: date,
) -> bool:
    return effective_from <= as_of and (
        effective_to is None or effective_to >= as_of
    )


def _rule_set_audit_value(rule: CalculationRuleSet) -> dict[str, Any]:
    return {
        "code": rule.code,
        "revision": rule.revision,
        "status": rule.status,
        "effective_from": rule.effective_from.isoformat(),
        "effective_to": rule.effective_to.isoformat() if rule.effective_to else None,
        "formula_code": rule.formula_code,
        "formula_parameters": rule.formula_parameters,
        "rounding_code": rule.rounding_code,
        "date_rule_id": rule.date_rule_id,
        "client_type_rule_id": rule.client_type_rule_id,
        "source_reference": rule.source_reference,
    }


class LegalRuleService:
    """Own versioned legal calculation rules without committing transactions.

    Callers own commit/rollback, matching other domain services in the project.
    New calculations may resolve only APPROVED revisions. Historical reads may
    address an exact retired revision, but retirement never rewrites evidence.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def resolve_rule_set(
        self,
        *,
        as_of: date,
        code: str | None = None,
    ) -> CalculationRuleSet:
        conditions = [
            CalculationRuleSet.status == RULE_STATUS_APPROVED,
            CalculationRuleSet.effective_from <= as_of,
            or_(
                CalculationRuleSet.effective_to.is_(None),
                CalculationRuleSet.effective_to >= as_of,
            ),
        ]
        if code is not None:
            conditions.append(CalculationRuleSet.code == _clean_code(code, field="code"))

        rows = (
            await self.db.execute(
                select(CalculationRuleSet)
                .where(*conditions)
                .options(
                    selectinload(CalculationRuleSet.date_rule),
                    selectinload(CalculationRuleSet.client_type_rule),
                    selectinload(CalculationRuleSet.rate_periods),
                    selectinload(CalculationRuleSet.exclusion_periods),
                )
                .order_by(CalculationRuleSet.code.asc(), CalculationRuleSet.revision.asc())
            )
        ).scalars().unique().all()

        if not rows:
            raise CalculationRuleUnavailableError(
                "Нет утверждённой редакции правил расчёта для указанной даты"
            )
        if len(rows) > 1:
            raise CalculationRuleAmbiguousError(
                "Найдено несколько утверждённых редакций правил расчёта; расчёт остановлен"
            )

        rule = rows[0]
        self._validate_resolved_dependencies(rule=rule, as_of=as_of)
        return rule

    async def get_exact_revision(
        self,
        *,
        rule_set_id: int,
    ) -> CalculationRuleSet:
        rule = (
            await self.db.execute(
                select(CalculationRuleSet)
                .where(CalculationRuleSet.id == int(rule_set_id))
                .options(
                    selectinload(CalculationRuleSet.date_rule),
                    selectinload(CalculationRuleSet.client_type_rule),
                    selectinload(CalculationRuleSet.rate_periods),
                    selectinload(CalculationRuleSet.exclusion_periods),
                )
            )
        ).scalars().first()
        if rule is None:
            raise CalculationRuleUnavailableError("Редакция правил расчёта не найдена")
        return rule

    async def create_draft(
        self,
        *,
        code: str,
        revision: int,
        effective_from: date,
        effective_to: date | None,
        formula_code: str,
        rounding_code: str,
        formula_parameters: dict | None = None,
        date_rule_id: int | None = None,
        client_type_rule_id: int | None = None,
        source_reference: str | None = None,
        actor_type: str = "admin",
        actor_id: int | None = None,
    ) -> CalculationRuleSet:
        if int(revision) <= 0:
            raise CalculationRuleValidationError("revision должна быть положительной")
        _validate_effective_range(
            effective_from,
            effective_to,
            label="Редакция правил",
        )
        rule = CalculationRuleSet(
            code=_clean_code(code, field="code"),
            revision=int(revision),
            status=RULE_STATUS_DRAFT,
            effective_from=effective_from,
            effective_to=effective_to,
            formula_code=_clean_code(formula_code, field="formula_code"),
            formula_parameters=formula_parameters or None,
            rounding_code=_clean_code(rounding_code, field="rounding_code"),
            date_rule_id=date_rule_id,
            client_type_rule_id=client_type_rule_id,
            source_reference=(source_reference or "").strip() or None,
        )
        self.db.add(rule)
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_RULE_DRAFT_CREATED",
            entity_type="calculation_rule_set",
            entity_id=rule.id,
            old_value=None,
            new_value=_rule_set_audit_value(rule),
            comment="Создан черновик редакции правил расчёта",
        )
        await self.db.flush()
        return rule

    async def update_draft(
        self,
        *,
        rule_set_id: int,
        actor_type: str = "admin",
        actor_id: int | None = None,
        effective_from: date | object = _UNSET,
        effective_to: date | None | object = _UNSET,
        formula_code: str | object = _UNSET,
        formula_parameters: dict | None | object = _UNSET,
        rounding_code: str | object = _UNSET,
        date_rule_id: int | None | object = _UNSET,
        client_type_rule_id: int | None | object = _UNSET,
        source_reference: str | None | object = _UNSET,
    ) -> CalculationRuleSet:
        rule = await self._locked_rule_set(rule_set_id)
        self._require_draft(rule)
        before = _rule_set_audit_value(rule)

        next_from = rule.effective_from if effective_from is _UNSET else effective_from
        next_to = rule.effective_to if effective_to is _UNSET else effective_to
        if not isinstance(next_from, date):
            raise CalculationRuleValidationError("effective_from должен быть датой")
        if next_to is not None and next_to is not _UNSET and not isinstance(next_to, date):
            raise CalculationRuleValidationError("effective_to должен быть датой или null")
        _validate_effective_range(next_from, next_to, label="Редакция правил")
        rule.effective_from = next_from
        rule.effective_to = next_to

        if formula_code is not _UNSET:
            rule.formula_code = _clean_code(str(formula_code), field="formula_code")
        if formula_parameters is not _UNSET:
            rule.formula_parameters = formula_parameters
        if rounding_code is not _UNSET:
            rule.rounding_code = _clean_code(str(rounding_code), field="rounding_code")
        if date_rule_id is not _UNSET:
            rule.date_rule_id = date_rule_id
        if client_type_rule_id is not _UNSET:
            rule.client_type_rule_id = client_type_rule_id
        if source_reference is not _UNSET:
            rule.source_reference = (str(source_reference or "").strip() or None)

        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_RULE_DRAFT_UPDATED",
            entity_type="calculation_rule_set",
            entity_id=rule.id,
            old_value=before,
            new_value=_rule_set_audit_value(rule),
            comment="Изменён черновик редакции правил расчёта",
        )
        await self.db.flush()
        return rule

    async def add_rate_period(
        self,
        *,
        rule_set_id: int,
        valid_from: date,
        valid_to: date | None,
        rate_code: str,
        rate_value: Decimal,
        source_reference: str | None = None,
        actor_type: str = "admin",
        actor_id: int | None = None,
    ) -> CalculationRatePeriod:
        rule = await self._locked_rule_set(rule_set_id)
        self._require_draft(rule)
        _validate_effective_range(valid_from, valid_to, label="Период ставки")
        normalized_rate = Decimal(rate_value)
        if normalized_rate < 0:
            raise CalculationRuleValidationError("Ставка не может быть отрицательной")

        existing = (
            await self.db.execute(
                select(CalculationRatePeriod)
                .where(CalculationRatePeriod.rule_set_id == rule.id)
                .order_by(CalculationRatePeriod.valid_from.asc())
                .with_for_update()
            )
        ).scalars().all()
        for item in existing:
            if _periods_overlap(valid_from, valid_to, item.valid_from, item.valid_to):
                raise CalculationRuleValidationError(
                    "Периоды ставок одной редакции не должны пересекаться"
                )

        period = CalculationRatePeriod(
            rule_set_id=rule.id,
            valid_from=valid_from,
            valid_to=valid_to,
            rate_code=_clean_code(rate_code, field="rate_code"),
            rate_value=normalized_rate,
            source_reference=(source_reference or "").strip() or None,
        )
        self.db.add(period)
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_RATE_PERIOD_ADDED",
            entity_type="calculation_rate_period",
            entity_id=period.id,
            old_value=None,
            new_value={
                "rule_set_id": rule.id,
                "valid_from": valid_from.isoformat(),
                "valid_to": valid_to.isoformat() if valid_to else None,
                "rate_code": period.rate_code,
                "rate_value": format(normalized_rate, "f"),
                "source_reference": period.source_reference,
            },
            comment="Добавлен период ставки в черновик правил расчёта",
        )
        await self.db.flush()
        return period

    async def add_exclusion_period(
        self,
        *,
        rule_set_id: int,
        date_from: date,
        date_to: date,
        exclusion_type: str,
        source_reference: str | None = None,
        actor_type: str = "admin",
        actor_id: int | None = None,
    ) -> CalculationExclusionPeriod:
        rule = await self._locked_rule_set(rule_set_id)
        self._require_draft(rule)
        if date_to < date_from:
            raise CalculationRuleValidationError(
                "Исключаемый период заканчивается раньше начала"
            )
        period = CalculationExclusionPeriod(
            rule_set_id=rule.id,
            date_from=date_from,
            date_to=date_to,
            exclusion_type=_clean_code(exclusion_type, field="exclusion_type"),
            source_reference=(source_reference or "").strip() or None,
        )
        self.db.add(period)
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_EXCLUSION_PERIOD_ADDED",
            entity_type="calculation_exclusion_period",
            entity_id=period.id,
            old_value=None,
            new_value={
                "rule_set_id": rule.id,
                "date_from": date_from.isoformat(),
                "date_to": date_to.isoformat(),
                "exclusion_type": period.exclusion_type,
                "source_reference": period.source_reference,
            },
            comment="Добавлен исключаемый период в черновик правил расчёта",
        )
        await self.db.flush()
        return period

    async def approve_rule_set(
        self,
        *,
        rule_set_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationRuleSet:
        target = await self._locked_rule_set(rule_set_id)
        # Lock every revision of the same code in stable order so competing
        # approvals serialize on PostgreSQL instead of both observing an empty
        # approved set.
        siblings = (
            await self.db.execute(
                select(CalculationRuleSet)
                .where(CalculationRuleSet.code == target.code)
                .order_by(CalculationRuleSet.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        rule = next((item for item in siblings if item.id == target.id), target)
        self._require_draft(rule)

        await self._validate_draft_for_approval(rule)
        for other in siblings:
            if other.id == rule.id or other.status != RULE_STATUS_APPROVED:
                continue
            if _periods_overlap(
                rule.effective_from,
                rule.effective_to,
                other.effective_from,
                other.effective_to,
            ):
                raise CalculationRuleAmbiguousError(
                    "Утверждённая редакция с пересекающимся периодом уже существует"
                )

        before = _rule_set_audit_value(rule)
        rule.status = RULE_STATUS_APPROVED
        rule.approved_at = datetime.now(timezone.utc)
        rule.approved_by_actor_type = _clean_code(actor_type, field="actor_type")
        rule.approved_by_actor_id = actor_id
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_RULE_APPROVED",
            entity_type="calculation_rule_set",
            entity_id=rule.id,
            old_value=before,
            new_value=_rule_set_audit_value(rule),
            comment="Утверждена редакция юридических правил расчёта",
        )
        await self.db.flush()
        return rule

    async def retire_rule_set(
        self,
        *,
        rule_set_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationRuleSet:
        rule = await self._locked_rule_set(rule_set_id)
        if rule.status != RULE_STATUS_APPROVED:
            raise CalculationRuleValidationError(
                "В архив можно перевести только утверждённую редакцию"
            )
        before = _rule_set_audit_value(rule)
        rule.status = RULE_STATUS_RETIRED
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_RULE_RETIRED",
            entity_type="calculation_rule_set",
            entity_id=rule.id,
            old_value=before,
            new_value=_rule_set_audit_value(rule),
            comment="Редакция правил расчёта выведена из применения для новых расчётов",
        )
        await self.db.flush()
        return rule

    async def _locked_rule_set(self, rule_set_id: int) -> CalculationRuleSet:
        rule = (
            await self.db.execute(
                select(CalculationRuleSet)
                .where(CalculationRuleSet.id == int(rule_set_id))
                .with_for_update()
            )
        ).scalars().first()
        if rule is None:
            raise CalculationRuleUnavailableError("Редакция правил расчёта не найдена")
        return rule

    @staticmethod
    def _require_draft(rule: CalculationRuleSet) -> None:
        if rule.status != RULE_STATUS_DRAFT:
            raise ImmutableCalculationRuleError(
                "Утверждённую или архивную редакцию нельзя редактировать"
            )

    async def _validate_draft_for_approval(self, rule: CalculationRuleSet) -> None:
        _validate_effective_range(
            rule.effective_from,
            rule.effective_to,
            label="Редакция правил",
        )
        _clean_code(rule.formula_code, field="formula_code")
        _clean_code(rule.rounding_code, field="rounding_code")
        if rule.date_rule_id is None:
            raise CalculationRuleValidationError(
                "Перед утверждением требуется утверждённое правило расчётных дат"
            )

        date_rule = await self.db.get(CalculationDateRule, rule.date_rule_id)
        if date_rule is None or date_rule.status != RULE_STATUS_APPROVED:
            raise CalculationRuleValidationError(
                "Правило расчётных дат отсутствует или не утверждено"
            )
        if not _effective_on(
            date_rule.effective_from,
            date_rule.effective_to,
            rule.effective_from,
        ):
            raise CalculationRuleValidationError(
                "Правило расчётных дат не действует на начало редакции правил"
            )

        if rule.client_type_rule_id is not None:
            client_rule = await self.db.get(
                CalculationClientTypeRule,
                rule.client_type_rule_id,
            )
            if client_rule is None or client_rule.status != RULE_STATUS_APPROVED:
                raise CalculationRuleValidationError(
                    "Правило типа клиента отсутствует или не утверждено"
                )
            if not _effective_on(
                client_rule.effective_from,
                client_rule.effective_to,
                rule.effective_from,
            ):
                raise CalculationRuleValidationError(
                    "Правило типа клиента не действует на начало редакции правил"
                )

        rates = (
            await self.db.execute(
                select(CalculationRatePeriod)
                .where(CalculationRatePeriod.rule_set_id == rule.id)
                .order_by(CalculationRatePeriod.valid_from.asc())
                .with_for_update()
            )
        ).scalars().all()
        if not rates:
            raise CalculationRuleValidationError(
                "Перед утверждением добавьте хотя бы один период ставки"
            )
        previous: CalculationRatePeriod | None = None
        for rate in rates:
            _validate_effective_range(
                rate.valid_from,
                rate.valid_to,
                label="Период ставки",
            )
            if Decimal(rate.rate_value) < 0:
                raise CalculationRuleValidationError("Ставка не может быть отрицательной")
            if previous and _periods_overlap(
                previous.valid_from,
                previous.valid_to,
                rate.valid_from,
                rate.valid_to,
            ):
                raise CalculationRuleValidationError(
                    "Периоды ставок одной редакции пересекаются"
                )
            previous = rate

    def _validate_resolved_dependencies(
        self,
        *,
        rule: CalculationRuleSet,
        as_of: date,
    ) -> None:
        if rule.date_rule is None or rule.date_rule.status != RULE_STATUS_APPROVED:
            raise CalculationRuleUnavailableError(
                "У утверждённой редакции нет действующего правила расчётных дат"
            )
        if not _effective_on(
            rule.date_rule.effective_from,
            rule.date_rule.effective_to,
            as_of,
        ):
            raise CalculationRuleUnavailableError(
                "Правило расчётных дат не действует на дату расчёта"
            )
        if rule.client_type_rule is not None:
            if rule.client_type_rule.status != RULE_STATUS_APPROVED or not _effective_on(
                rule.client_type_rule.effective_from,
                rule.client_type_rule.effective_to,
                as_of,
            ):
                raise CalculationRuleUnavailableError(
                    "Правило типа клиента не действует на дату расчёта"
                )
        if not rule.rate_periods:
            raise CalculationRuleUnavailableError(
                "В утверждённой редакции отсутствуют периоды ставок"
            )

    def _audit(
        self,
        *,
        actor_type: str,
        actor_id: int | None,
        action: str,
        entity_type: str,
        entity_id: int | None,
        old_value: dict | None,
        new_value: dict | None,
        comment: str,
    ) -> None:
        self.db.add(
            AuditLog(
                actor_type=str(actor_type or "system"),
                actor_id=actor_id,
                action=action,
                entity_type=entity_type,
                entity_id=entity_id,
                old_value=old_value,
                new_value=new_value,
                comment=comment,
            )
        )
