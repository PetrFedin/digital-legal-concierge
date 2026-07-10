from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.cases.case_history import add_case_history_event
from app.domain.payments.payment_types import PaymentCode
from app.domain.statuses.payment_statuses import PaymentStatus
from app.models.payment import Payment
from app.models.case import Case
from app.system.settings_service import SettingsService
from app.domain.payments.providers import get_payment_provider
class PaymentService:
    def __init__(self,db:AsyncSession): self.db=db
    async def amount_for_code(self,code:str):
        settings=SettingsService(self.db)
        if code==PaymentCode.M1_INITIAL_PAYMENT: return Decimal(str(await settings.get_value('payments.m1_initial_payment')))
        if code==PaymentCode.M1_COURT_PAYMENT: return Decimal(str(await settings.get_value('payments.m1_court_payment')))
        if code==PaymentCode.M2_CONSULTATION_PAYMENT: return Decimal(str(await settings.get_value('payments.m2_consultation_payment')))
        return Decimal('0')
    def title_for_code(self,code:str): return {PaymentCode.M1_INITIAL_PAYMENT:'Первый платеж М1',PaymentCode.M1_COURT_PAYMENT:'Второй платеж М1',PaymentCode.M1_SUCCESS_FEE:'Success fee',PaymentCode.M2_CONSULTATION_PAYMENT:'Оплата консультации'}.get(code,'Платеж')
    async def list_case_payments(self,case_id:int):
        res=await self.db.execute(select(Payment).where(Payment.case_id==case_id).order_by(Payment.created_at.desc())); return list(res.scalars().all())
    async def get_payment(self,payment_id:int):
        res=await self.db.execute(select(Payment).where(Payment.id==payment_id)); return res.scalars().first()
    async def get_or_create_payment(self, *, case:Case, payment_code:str, amount:Decimal|None=None):
        res=await self.db.execute(select(Payment).where(Payment.case_id==case.id).where(Payment.payment_code==payment_code).where(Payment.status.in_([PaymentStatus.PENDING,PaymentStatus.WAITING_CONFIRMATION])))
        p=res.scalars().first()
        if p: return p
        final_amount = amount
        if final_amount is None:
            if payment_code == PaymentCode.M1_SUCCESS_FEE:
                final_amount = await self.estimate_success_fee_for_case(case.id)
            else:
                final_amount = await self.amount_for_code(payment_code)
        p=Payment(case_id=case.id,payment_code=payment_code,title=self.title_for_code(payment_code),amount=final_amount,currency='RUB',status=PaymentStatus.PENDING)
        self.db.add(p); await self.db.flush(); await add_case_history_event(self.db,actor_type='system',actor_id=None,case_id=case.id,action='PAYMENT_CREATED',new_value={'payment_id':p.id,'code':payment_code,'amount':str(p.amount)}); return p

    async def estimate_success_fee_for_case(self, case_id: int):
        from sqlalchemy import select
        from app.models.calculation import Calculation
        settings = SettingsService(self.db)
        percent = Decimal(str(await settings.get_value('payments.m1_success_fee_percent')))
        res = await self.db.execute(select(Calculation).where(Calculation.case_id == case_id))
        calc = res.scalars().first()
        base = calc.penalty_amount if calc and calc.penalty_amount else Decimal('0')
        amount = (base * percent / Decimal('100')).quantize(Decimal('0.01'))
        return amount if amount > 0 else Decimal('1.00')
    async def create_payment_link(self,payment:Payment):
        if not payment.payment_url:
            provider = get_payment_provider()
            result = await provider.create_payment(
                payment_id=payment.id,
                amount=payment.amount,
                currency=payment.currency,
                title=payment.title,
                metadata={"case_id": payment.case_id, "payment_code": payment.payment_code},
            )
            payment.provider=result.provider
            payment.provider_payment_id=result.provider_payment_id
            payment.payment_url=result.payment_url
            payment.status=PaymentStatus.WAITING_CONFIRMATION
            await self.db.flush()
        return payment
    async def mark_paid(self, *, payment:Payment, case:Case, actor_type='system', actor_id:int|None=None):
        old=payment.status; payment.status=PaymentStatus.PAID
        await add_case_history_event(self.db,actor_type=actor_type,actor_id=actor_id,case_id=case.id,action='PAYMENT_PAID',old_value={'status':old},new_value={'payment_id':payment.id,'code':payment.payment_code,'amount':str(payment.amount)})
        await self.db.flush(); return payment
