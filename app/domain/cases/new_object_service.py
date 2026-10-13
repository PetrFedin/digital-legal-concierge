from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.calculator_service import CalculatorService
from app.domain.calculator.preview_receipt import preview_receipt
from app.domain.cases.case_service import CaseService
from app.models.calculation import Calculation
from app.models.case import Case
from app.models.user import User


class NewObjectSaveError(ValueError):
    pass


class NewObjectSaveService:
    """Create a separate case once per preview token without touching the active case."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def _verify_saved_receipt(
        self, *, case: Case, expected_receipt: dict
    ) -> Case:
        # Never reinterpret an idempotency token as authority for different
        # dates, price, rate, formula or financial outcome.
        saved = await self.db.scalar(
            select(Calculation)
            .where(Calculation.case_id == case.id)
            .order_by(Calculation.id.asc())
            .limit(1)
        )
        if saved is None or preview_receipt(saved) != expected_receipt:
            raise NewObjectSaveError(
                "Этот ключ уже использован для другого расчёта. "
                "Откройте сохранённое дело или начните новый расчёт."
            )
        return case

    async def save(
        self,
        *,
        user: User,
        preview_case_id: int,
        request_key: str,
        contract_price: Decimal,
        planned_transfer_date: date,
        calculation_date: date,
        object_transferred: bool,
        actual_transfer_date: date | None,
        expected_receipt: dict,
    ) -> Case:
        if not request_key or len(request_key) != 32 or any(c not in "0123456789abcdef" for c in request_key):
            raise NewObjectSaveError("Недействительный идентификатор расчёта")
        cases = CaseService(self.db)
        current = await cases.get_active_case_for_user(user.id)
        # Always validate ownership and the active case, including retries.
        if current is None or current.id != preview_case_id:
            raise NewObjectSaveError("Текущее дело изменилось. Начните новый расчёт.")
        key = f"new-object:{user.id}:{request_key}"
        existing = await self.db.scalar(
            select(Case).where(Case.client_id == user.id, Case.creation_request_key == key)
        )
        if existing is not None:
            return await self._verify_saved_receipt(
                case=existing, expected_receipt=expected_receipt
            )
        # Verify inputs before creating any legal record or changing any case.
        service = CalculatorService(self.db)
        checked_result = service.calculate_preview(
            contract_price=contract_price,
            planned_transfer_date=planned_transfer_date,
            calculation_date=calculation_date,
            object_transferred=object_transferred,
            actual_transfer_date=actual_transfer_date,
        )
        if not isinstance(expected_receipt, dict) or preview_receipt(checked_result) != expected_receipt:
            raise NewObjectSaveError(
                "Условия расчёта изменились после просмотра. Пересчитайте сумму "
                "и подтвердите новый результат перед сохранением."
            )
        try:
            async with self.db.begin_nested():
                case = await cases.create_case(
                    client=user,
                    title="Обращение по другому объекту ДДУ",
                    creation_request_key=key,
                )
                await service.calculate_and_save(
                    case=case,
                    contract_price=contract_price,
                    planned_transfer_date=planned_transfer_date,
                    calculation_date=calculation_date,
                    object_transferred=object_transferred,
                    actual_transfer_date=actual_transfer_date,
                    key_rate=checked_result.key_rate,
                    consumer_multiplier=checked_result.consumer_multiplier,
                )
        except IntegrityError:
            # Two Telegram updates may race; the unique database key is the
            # final authority. The savepoint keeps the outer session usable.
            case = await self.db.scalar(
                select(Case).where(Case.client_id == user.id, Case.creation_request_key == key)
            )
            if case is None:
                raise
            return await self._verify_saved_receipt(
                case=case, expected_receipt=expected_receipt
            )
        return case
