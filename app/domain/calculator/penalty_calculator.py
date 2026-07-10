from dataclasses import dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from app.config import settings
@dataclass
class PenaltyCalculationInput:
    contract_price: Decimal; planned_transfer_date: date; object_transferred: bool; actual_transfer_date: date|None=None
@dataclass
class PenaltyCalculationResult:
    contract_price: Decimal; planned_transfer_date: date; object_transferred: bool; actual_transfer_date: date|None; delay_days:int; penalty_amount: Decimal; recommended_route:str; is_preliminary: bool=True; warning:str|None=None
class PenaltyCalculator:
    def calculate(self,data:PenaltyCalculationInput):
        end=data.actual_transfer_date if data.object_transferred and data.actual_transfer_date else date.today()
        delay=max((end-data.planned_transfer_date).days,0)
        amount=(data.contract_price*Decimal(str(settings.legal_key_rate))/Decimal('300')*Decimal(delay)*Decimal('2')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        return PenaltyCalculationResult(data.contract_price,data.planned_transfer_date,data.object_transferred,data.actual_transfer_date,delay,amount,'M1' if delay>0 else 'M2',True,None if delay>0 else 'Просрочка не обнаружена. Лучше обсудить ситуацию с юристом.')
def parse_money(value:str)->Decimal:
    cleaned=value.replace(' ','').replace('₽','').replace(',','.').strip(); amount=Decimal(cleaned)
    if amount<=0: raise ValueError('Стоимость должна быть больше нуля')
    return amount
