from sqlalchemy import select, func
from app.models.case import Case
from app.models.payment import Payment
from app.models.consultation import Consultation
class AdminDashboardService:
    def __init__(self,db): self.db=db
    async def build(self):
        async def count(stmt):
            r=await self.db.execute(stmt); return r.scalar_one()
        return {'new_cases':await count(select(func.count(Case.id)).where(Case.status=='NEW')),'active_cases':await count(select(func.count(Case.id)).where(Case.status.notin_(['M1_CLOSED','M2_CLOSED','ARCHIVED']))),'waiting_payment':await count(select(func.count(Payment.id)).where(Payment.status=='WAITING_CONFIRMATION')),'consultations_booked':await count(select(func.count(Consultation.id)).where(Consultation.status=='BOOKED'))}
