from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.models.consultation import Consultation
class ConsultationService:
    def __init__(self,db:AsyncSession): self.db=db
    async def get_or_create_for_case(self,case):
        res=await self.db.execute(select(Consultation).where(Consultation.case_id==case.id).order_by(Consultation.created_at.desc())); c=res.scalars().first()
        if c: return c
        c=Consultation(case_id=case.id,status=ConsultationStatus.DESCRIPTION_PENDING); self.db.add(c); await self.db.flush(); return c
    async def save_description(self, *, consultation, case, client_id:int, description:str):
        consultation.client_description=description; consultation.status=ConsultationStatus.DOCUMENTS_OPTIONAL
        await add_case_history_event(self.db,actor_type='client',actor_id=client_id,case_id=case.id,action='CONSULTATION_DESCRIPTION_SAVED',new_value={'description':description}); await self.db.flush(); return consultation
    async def reserve_slot(self, *, consultation, case, client_id:int, scheduled_at):
        consultation.scheduled_at=scheduled_at; consultation.status=ConsultationStatus.PAYMENT_PENDING
        await add_case_history_event(self.db,actor_type='client',actor_id=client_id,case_id=case.id,action='CONSULTATION_SLOT_RESERVED',new_value={'scheduled_at':scheduled_at.isoformat()}); await self.db.flush(); return consultation

    async def mark_booked_after_payment(self, *, consultation, case):
        consultation.status = ConsultationStatus.BOOKED
        await add_case_history_event(self.db, actor_type='system', actor_id=None, case_id=case.id, action='CONSULTATION_BOOKED', new_value={'consultation_id': consultation.id})
        await self.db.flush()
        return consultation
    async def mark_done(self, *, consultation, case, lawyer_id:int, result:str, decision:str):
        consultation.status=ConsultationStatus.DONE; consultation.lawyer_id=lawyer_id; consultation.lawyer_result=result; consultation.decision=decision; await self.db.flush(); return consultation
