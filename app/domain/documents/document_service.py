from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.cases.case_history import add_case_history_event
from app.domain.statuses.document_statuses import DocumentStatus
from app.models.document import Document
DOC_TITLES={'DDU':'ДДУ','APPENDIX':'Приложение','ADDITIONAL_AGREEMENT':'Допсоглашение','TRANSFER_ACT':'Акт','PAYMENT_PROOF':'Платежный документ','CORRESPONDENCE':'Переписка','OTHER':'Другой документ'}
class DocumentService:
    def __init__(self,db:AsyncSession): self.db=db
    async def list_case_documents(self,case_id:int):
        res=await self.db.execute(select(Document).where(Document.case_id==case_id).order_by(Document.created_at.desc())); return list(res.scalars().all())
    async def get_document(self,document_id:int):
        res=await self.db.execute(select(Document).where(Document.id==document_id)); return res.scalars().first()
    async def create_document(self, *, case, uploaded_by_user_id:int|None, document_type:str, file_name:str, file_path:str, mime_type:str|None, file_size:int|None):
        res=await self.db.execute(select(Document).where(Document.case_id==case.id).where(Document.document_type==document_type).order_by(Document.version.desc())); latest=res.scalars().first(); version=(latest.version+1 if latest else 1)
        d=Document(case_id=case.id,uploaded_by_user_id=uploaded_by_user_id,document_type=document_type,title=DOC_TITLES.get(document_type,'Документ'),file_name=file_name,file_path=file_path,mime_type=mime_type,file_size=file_size,version=version,status=DocumentStatus.UPLOADED)
        self.db.add(d); await self.db.flush(); await add_case_history_event(self.db,actor_type='client',actor_id=uploaded_by_user_id,case_id=case.id,action='DOCUMENT_UPLOADED',new_value={'document_id':d.id,'type':document_type,'version':version}); return d
    async def send_documents_to_review(self, *, case, actor_id:int):
        docs=await self.list_case_documents(case.id)
        for d in docs:
            if d.status==DocumentStatus.UPLOADED: d.status=DocumentStatus.ON_REVIEW
        await add_case_history_event(self.db,actor_type='client',actor_id=actor_id,case_id=case.id,action='DOCUMENTS_SENT_TO_REVIEW',new_value={'count':len(docs)}); await self.db.flush()
