from datetime import date
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.calculator.penalty_calculator import PenaltyCalculator, PenaltyCalculationInput
from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.case_statuses import CaseStatus
from app.models.calculation import Calculation
from app.models.case import Case
class CalculatorService:
    def __init__(self,db:AsyncSession): self.db=db; self.calculator=PenaltyCalculator()
    async def calculate_and_save(self, *, case:Case, contract_price:Decimal, planned_transfer_date:date, object_transferred:bool, actual_transfer_date:date|None=None):
        result=self.calculator.calculate(PenaltyCalculationInput(contract_price,planned_transfer_date,object_transferred,actual_transfer_date))
        res=await self.db.execute(select(Calculation).where(Calculation.case_id==case.id)); calc=res.scalars().first()
        if not calc: calc=Calculation(case_id=case.id); self.db.add(calc)
        calc.contract_price=result.contract_price; calc.planned_transfer_date=result.planned_transfer_date; calc.actual_transfer_date=result.actual_transfer_date; calc.object_transferred=result.object_transferred; calc.delay_days=result.delay_days; calc.penalty_amount=result.penalty_amount; calc.is_preliminary=True
        case.status=CaseStatus.CALCULATED; case.next_action='Выбрать дальнейший маршрут'
        await add_case_history_event(self.db,actor_type='client',actor_id=case.client_id,case_id=case.id,action='CALCULATION_COMPLETED',new_value={'penalty_amount':str(result.penalty_amount),'delay_days':result.delay_days})
        await self.db.flush(); return result
