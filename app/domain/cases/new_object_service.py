from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.calculator.calculator_service import CalculatorService
from app.domain.cases.case_service import CaseService
from app.models.case import Case
from app.models.user import User


class NewObjectSaveError(ValueError):
    pass


class NewObjectSaveService:
    """Create a separate case once per preview token without touching the active case."""

    def __init__(self, db: AsyncSession):
        self.db = db

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
            return existing
        # Verify inputs before creating any legal record or changing any case.
        service = CalculatorService(self.db)
        service.calculate_preview(
            contract_price=contract_price,
            planned_transfer_date=planned_transfer_date,
            calculation_date=calculation_date,
            object_transferred=object_transferred,
            actual_transfer_date=actual_transfer_date,
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
                )
        except IntegrityError:
            # Two Telegram updates may race; the unique database key is the
            # final authority. The savepoint keeps the outer session usable.
            case = await self.db.scalar(
                select(Case).where(Case.client_id == user.id, Case.creation_request_key == key)
            )
            if case is None:
                raise
        return case
