from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config import settings
from app.db.session import get_db
from app.models.case import Case
from app.lawyer.lawyer_decisions import LawyerDecisionService
router=APIRouter(prefix='/lawyer',tags=['lawyer'])
def check(token):
    if token != settings.admin_api_token: raise HTTPException(401,'bad token')
@router.post('/cases/{case_id}/accept')
async def accept(case_id:int, lawyer_id:int=1, db:AsyncSession=Depends(get_db), x_admin_token:str|None=Header(default=None)):
    check(x_admin_token); case=(await db.execute(select(Case).where(Case.id==case_id))).scalars().first(); await LawyerDecisionService(db).accept_m1_case(case=case,lawyer_id=lawyer_id); await db.commit(); return {'ok':True}
@router.post('/cases/{case_id}/request-documents')
async def request_docs(case_id:int,payload:dict, lawyer_id:int=1, db:AsyncSession=Depends(get_db), x_admin_token:str|None=Header(default=None)):
    check(x_admin_token); case=(await db.execute(select(Case).where(Case.id==case_id))).scalars().first(); await LawyerDecisionService(db).request_more_documents(case=case,lawyer_id=lawyer_id,comment=payload.get('comment','Нужны дополнительные документы')); await db.commit(); return {'ok':True}
