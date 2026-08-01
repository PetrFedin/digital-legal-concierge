from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.models.case import Case
from app.models.message import Message


class MessageService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_case(self, case_id: int) -> Case:
        case = (
            await self.db.execute(
                select(Case)
                .where(Case.id == int(case_id))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if not case:
            raise LookupError("Дело не найдено")
        return case

    async def latest_message_id(self, case_id: int) -> int | None:
        return (
            await self.db.execute(
                select(Message.id)
                .where(Message.case_id == int(case_id))
                .order_by(Message.created_at.desc(), Message.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

    async def create_client_message(
        self,
        *,
        case: Case,
        user_id: int,
        text: str,
    ) -> Message:
        case = await self.lock_case(case.id)
        msg = Message(
            case_id=case.id,
            sender_type="client",
            sender_id=user_id,
            text=text,
            is_read=False,
        )
        self.db.add(msg)
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=user_id,
            case_id=case.id,
            action="CLIENT_MESSAGE_CREATED",
            new_value={"message_id": msg.id, "text": text[:500]},
        )
        return msg

    async def create_lawyer_message(
        self,
        *,
        case: Case,
        lawyer_id: int | None,
        text: str,
    ) -> Message:
        case = await self.lock_case(case.id)
        msg = Message(
            case_id=case.id,
            sender_type="lawyer",
            sender_id=lawyer_id,
            text=text,
            is_read=False,
        )
        self.db.add(msg)
        await self.db.flush()
        await add_case_history_event(
            self.db,
            actor_type="lawyer",
            actor_id=lawyer_id,
            case_id=case.id,
            action="LAWYER_MESSAGE_CREATED",
            new_value={"message_id": msg.id, "text": text[:500]},
        )
        return msg

    async def list_case_messages(
        self,
        case_id: int,
        limit: int = 100,
    ) -> list[Message]:
        result = await self.db.execute(
            select(Message)
            .where(Message.case_id == case_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_client_messages_read(self, case_id: int) -> int:
        result = await self.db.execute(
            select(Message).where(
                Message.case_id == case_id,
                Message.sender_type == "client",
                Message.is_read.is_(False),
            )
        )
        messages = list(result.scalars().all())
        for message in messages:
            message.is_read = True
        await self.db.flush()
        return len(messages)

    async def mark_lawyer_messages_read(self, case_id: int) -> int:
        result = await self.db.execute(
            select(Message).where(
                Message.case_id == case_id,
                Message.sender_type == "lawyer",
                Message.is_read.is_(False),
            )
        )
        messages = list(result.scalars().all())
        for message in messages:
            message.is_read = True
        await self.db.flush()
        return len(messages)
