from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
)


def _clean(value: str, *, field: str, limit: int = 100) -> str:
    result = str(value or "").strip()
    if not result:
        raise CalculationRuleValidationError(f"{field} не может быть пустым")
    if len(result) > limit:
        raise CalculationRuleValidationError(f"{field} длиннее {limit} символов")
    return result


def _range_ok(start: date, end: date | None) -> None:
    if end is not None and end < start:
        raise CalculationRuleValidationError("Дата окончания раньше даты начала")


def _overlap(
    left_start: date,
    left_end: date | None,
    right_start: date,
    right_end: date | None,
) -> bool:
    return (left_end is None or left_end >= right_start) and (
        right_end is None or right_end >= left_start
    )


class LegalReferenceRuleService:
    """Lifecycle owner for date and client-type reference rules.

    The service deliberately stores strategy/coefficients but does not decide
    their legal values. Callers own commit/rollback; approval creates sealed
    AuditLog evidence in the same transaction.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_date_rule_draft(
        self,
        *,
        rule_code: str,
        revision: int,
        effective_from: date,
        effective_to: date | None,
        strategy_code: str,
        parameters: dict | None = None,
        source_reference: str | None = None,
        actor_type: str = "admin",
        actor_id: int | None = None,
    ) -> CalculationDateRule:
        if int(revision) <= 0:
            raise CalculationRuleValidationError("revision должна быть положительной")
        _range_ok(effective_from, effective_to)
        item = CalculationDateRule(
            rule_code=_clean(rule_code, field="rule_code"),
            revision=int(revision),
            status=RULE_STATUS_DRAFT,
            effective_from=effective_from,
            effective_to=effective_to,
            strategy_code=_clean(strategy_code, field="strategy_code"),
            parameters=parameters or None,
            source_reference=(source_reference or "").strip() or None,
        )
        self.db.add(item)
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_DATE_RULE_DRAFT_CREATED",
            entity_type="calculation_date_rule",
            entity_id=item.id,
            new_value=self._date_value(item),
        )
        await self.db.flush()
        return item

    async def approve_date_rule(
        self,
        *,
        rule_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationDateRule:
        target = await self._locked_date_rule(rule_id)
        siblings = (
            await self.db.execute(
                select(CalculationDateRule)
                .where(CalculationDateRule.rule_code == target.rule_code)
                .order_by(CalculationDateRule.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        item = next((row for row in siblings if row.id == target.id), target)
        self._require_draft(item.status)
        _range_ok(item.effective_from, item.effective_to)
        _clean(item.strategy_code, field="strategy_code")
        for other in siblings:
            if other.id == item.id or other.status != RULE_STATUS_APPROVED:
                continue
            if _overlap(
                item.effective_from,
                item.effective_to,
                other.effective_from,
                other.effective_to,
            ):
                raise CalculationRuleAmbiguousError(
                    "Уже существует утверждённое правило расчётных дат с пересекающимся периодом"
                )
        before = self._date_value(item)
        item.status = RULE_STATUS_APPROVED
        item.approved_at = datetime.now(timezone.utc)
        item.approved_by_actor_type = _clean(actor_type, field="actor_type")
        item.approved_by_actor_id = actor_id
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_DATE_RULE_APPROVED",
            entity_type="calculation_date_rule",
            entity_id=item.id,
            old_value=before,
            new_value=self._date_value(item),
        )
        await self.db.flush()
        return item

    async def retire_date_rule(
        self,
        *,
        rule_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationDateRule:
        item = await self._locked_date_rule(rule_id)
        if item.status != RULE_STATUS_APPROVED:
            raise CalculationRuleValidationError(
                "В архив можно перевести только утверждённое правило расчётных дат"
            )
        before = self._date_value(item)
        item.status = RULE_STATUS_RETIRED
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_DATE_RULE_RETIRED",
            entity_type="calculation_date_rule",
            entity_id=item.id,
            old_value=before,
            new_value=self._date_value(item),
        )
        await self.db.flush()
        return item

    async def create_client_type_rule_draft(
        self,
        *,
        client_type_code: str,
        revision: int,
        effective_from: date,
        effective_to: date | None,
        consumer_multiplier: Decimal,
        source_reference: str | None = None,
        actor_type: str = "admin",
        actor_id: int | None = None,
    ) -> CalculationClientTypeRule:
        if int(revision) <= 0:
            raise CalculationRuleValidationError("revision должна быть положительной")
        _range_ok(effective_from, effective_to)
        multiplier = Decimal(consumer_multiplier)
        if multiplier <= 0:
            raise CalculationRuleValidationError(
                "Коэффициент типа клиента должен быть больше нуля"
            )
        item = CalculationClientTypeRule(
            client_type_code=_clean(client_type_code, field="client_type_code"),
            revision=int(revision),
            status=RULE_STATUS_DRAFT,
            effective_from=effective_from,
            effective_to=effective_to,
            consumer_multiplier=multiplier,
            source_reference=(source_reference or "").strip() or None,
        )
        self.db.add(item)
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_CLIENT_TYPE_RULE_DRAFT_CREATED",
            entity_type="calculation_client_type_rule",
            entity_id=item.id,
            new_value=self._client_value(item),
        )
        await self.db.flush()
        return item

    async def approve_client_type_rule(
        self,
        *,
        rule_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationClientTypeRule:
        target = await self._locked_client_rule(rule_id)
        siblings = (
            await self.db.execute(
                select(CalculationClientTypeRule)
                .where(
                    CalculationClientTypeRule.client_type_code
                    == target.client_type_code
                )
                .order_by(CalculationClientTypeRule.id.asc())
                .with_for_update()
            )
        ).scalars().all()
        item = next((row for row in siblings if row.id == target.id), target)
        self._require_draft(item.status)
        _range_ok(item.effective_from, item.effective_to)
        if Decimal(item.consumer_multiplier) <= 0:
            raise CalculationRuleValidationError(
                "Коэффициент типа клиента должен быть больше нуля"
            )
        for other in siblings:
            if other.id == item.id or other.status != RULE_STATUS_APPROVED:
                continue
            if _overlap(
                item.effective_from,
                item.effective_to,
                other.effective_from,
                other.effective_to,
            ):
                raise CalculationRuleAmbiguousError(
                    "Уже существует утверждённое правило типа клиента с пересекающимся периодом"
                )
        before = self._client_value(item)
        item.status = RULE_STATUS_APPROVED
        item.approved_at = datetime.now(timezone.utc)
        item.approved_by_actor_type = _clean(actor_type, field="actor_type")
        item.approved_by_actor_id = actor_id
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_CLIENT_TYPE_RULE_APPROVED",
            entity_type="calculation_client_type_rule",
            entity_id=item.id,
            old_value=before,
            new_value=self._client_value(item),
        )
        await self.db.flush()
        return item

    async def retire_client_type_rule(
        self,
        *,
        rule_id: int,
        actor_type: str,
        actor_id: int | None,
    ) -> CalculationClientTypeRule:
        item = await self._locked_client_rule(rule_id)
        if item.status != RULE_STATUS_APPROVED:
            raise CalculationRuleValidationError(
                "В архив можно перевести только утверждённое правило типа клиента"
            )
        before = self._client_value(item)
        item.status = RULE_STATUS_RETIRED
        await self.db.flush()
        self._audit(
            actor_type=actor_type,
            actor_id=actor_id,
            action="CALCULATION_CLIENT_TYPE_RULE_RETIRED",
            entity_type="calculation_client_type_rule",
            entity_id=item.id,
            old_value=before,
            new_value=self._client_value(item),
        )
        await self.db.flush()
        return item

    async def _locked_date_rule(self, rule_id: int) -> CalculationDateRule:
        item = (
            await self.db.execute(
                select(CalculationDateRule)
                .where(CalculationDateRule.id == int(rule_id))
                .with_for_update()
            )
        ).scalars().first()
        if item is None:
            raise CalculationRuleUnavailableError("Правило расчётных дат не найдено")
        return item

    async def _locked_client_rule(self, rule_id: int) -> CalculationClientTypeRule:
        item = (
            await self.db.execute(
                select(CalculationClientTypeRule)
                .where(CalculationClientTypeRule.id == int(rule_id))
                .with_for_update()
            )
        ).scalars().first()
        if item is None:
            raise CalculationRuleUnavailableError("Правило типа клиента не найдено")
        return item

    @staticmethod
    def _require_draft(status: str) -> None:
        if status != RULE_STATUS_DRAFT:
            raise ImmutableCalculationRuleError(
                "Утверждённое или архивное правило нельзя редактировать"
            )

    @staticmethod
    def _date_value(item: CalculationDateRule) -> dict:
        return {
            "rule_code": item.rule_code,
            "revision": item.revision,
            "status": item.status,
            "effective_from": item.effective_from.isoformat(),
            "effective_to": item.effective_to.isoformat() if item.effective_to else None,
            "strategy_code": item.strategy_code,
            "parameters": item.parameters,
            "source_reference": item.source_reference,
        }

    @staticmethod
    def _client_value(item: CalculationClientTypeRule) -> dict:
        return {
            "client_type_code": item.client_type_code,
            "revision": item.revision,
            "status": item.status,
            "effective_from": item.effective_from.isoformat(),
            "effective_to": item.effective_to.isoformat() if item.effective_to else None,
            "consumer_multiplier": format(Decimal(item.consumer_multiplier), "f"),
            "source_reference": item.source_reference,
        }

    def _audit(
        self,
        *,
        actor_type: str,
        actor_id: int | None,
        action: str,
        entity_type: str,
        entity_id: int | None,
        new_value: dict,
        old_value: dict | None = None,
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
                comment="Изменение управляемого справочника юридического расчёта",
            )
        )
