from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.domain.cases.case_history import add_case_history_event
from app.models.case import Case
from app.models.message import Message

class MessageService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_client_message(self, *, case: Case, user_id: int, text: str) -> Message:
        msg = Message(case_id=case.id, sender_type='client', sender_id=user_id, text=text, is_read=False)
        self.db.add(msg)
        await self.db.flush()
        await add_case_history_event(self.db, actor_type='client', actor_id=user_id, case_id=case.id, action='CLIENT_MESSAGE_CREATED', new_value={'message_id': msg.id, 'text': text[:500]})
        return msg

    async def list_case_messages(self, case_id: int) -> list[Message]:
        res = await self.db.execute(select(Message).where(Message.case_id == case_id).order_by(Message.created_at.asc()))
        return list(res.scalars().all())
