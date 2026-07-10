from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit_log import AuditLog
async def add_case_history_event(db: AsyncSession, *, actor_type: str, actor_id: int|None, case_id: int, action: str, old_value: dict|None=None, new_value: dict|None=None, comment: str|None=None):
    event=AuditLog(actor_type=actor_type,actor_id=actor_id,action=action,entity_type='case',entity_id=case_id,old_value=old_value,new_value=new_value,comment=comment)
    db.add(event); await db.flush(); return event
