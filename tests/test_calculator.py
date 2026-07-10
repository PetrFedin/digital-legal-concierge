from datetime import date, timedelta
from decimal import Decimal
from app.domain.calculator.penalty_calculator import PenaltyCalculator, PenaltyCalculationInput

def test_calculator_positive_delay():
    result=PenaltyCalculator().calculate(PenaltyCalculationInput(contract_price=Decimal('1000000'),planned_transfer_date=date.today()-timedelta(days=10),object_transferred=False))
    assert result.delay_days==10
    assert result.penalty_amount > 0
