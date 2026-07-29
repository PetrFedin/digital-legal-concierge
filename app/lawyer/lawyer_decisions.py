from app.domain.cases.case_service import CaseService
from app.domain.statuses.case_statuses import CaseStatus
class LawyerDecisionService:
    def __init__(self,db): self.db=db; self.cases=CaseService(db)
    async def accept_m1_case(self, *, case, lawyer_id:int, comment=None):
        await self.cases.change_status(case=case,next_status=CaseStatus.M1_ACCEPTED,actor_type='lawyer',actor_id=lawyer_id,comment=comment)
        return await self.cases.change_status(case=case,next_status=CaseStatus.M1_CONTRACT_READY,actor_type='lawyer',actor_id=lawyer_id,comment='Открыт этап договора')
    async def request_more_documents(self, *, case, lawyer_id:int, comment:str): return await self.cases.change_status(case=case,next_status=CaseStatus.M1_DOCS_REQUESTED,actor_type='lawyer',actor_id=lawyer_id,comment=comment)
    async def transfer_m1_to_m2(self, *, case, lawyer_id:int, reason:str): return await self.cases.transfer_to_m2(case=case,actor_type='lawyer',actor_id=lawyer_id,reason=reason)
