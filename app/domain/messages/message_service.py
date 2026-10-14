from __future__ import annotations

from collections.abc import Collection
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.cases.case_history import add_case_history_event
from app.domain.cases.case_transition_policy import TERMINAL_STATUSES
from app.domain.statuses.case_statuses import CaseStatus
from app.models.case import Case
from app.models.case_retention import CaseRetentionRecord
from app.models.message import Message
from app.models.user import User


# Closed, erased and system-error cases are not client message targets.
_CLIENT_MESSAGE_DENIED_STATUSES = frozenset(
    {str(item) for item in TERMINAL_STATUSES} | {str(CaseStatus.ERROR)}
)
RESTRICTED_MESSAGE_RETENTION_STATES = frozenset(
    {"APPROVED", "EXECUTING", "FAILED", "COMPLETED"}
)


class ClientMessageAccessDenied(ValueError):
    """Uniform denial: do not disclose foreign case or stale message identities."""

    def __init__(self):
        super().__init__("Сообщение по этому делу недоступно.")


class StaffMessageReadOnlyError(ValueError):
    """Staff cannot send replies after a Case is closed or content is erased."""

    def __init__(self):
        super().__init__("Переписка доступна только для чтения. Новые ответы не принимаются.")


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

    async def unread_lawyer_summary(
        self,
        case_id: int,
    ) -> tuple[int, datetime | None]:
        unread_result = await self.db.execute(
            select(func.count(Message.id)).where(
                Message.case_id == int(case_id),
                Message.sender_type == "lawyer",
                Message.is_read.is_(False),
            )
        )
        latest_result = await self.db.execute(
            select(func.max(Message.created_at)).where(
                Message.case_id == int(case_id),
                Message.sender_type == "lawyer",
            )
        )
        return (
            int(unread_result.scalar_one() or 0),
            latest_result.scalar_one_or_none(),
        )

    async def find_client_message_by_source(
        self,
        *,
        user_id: int,
        source_message_id: int | None,
    ) -> Message | None:
        if source_message_id is None:
            return None
        return (
            await self.db.execute(
                select(Message).where(
                    Message.sender_type == "client",
                    Message.sender_id == int(user_id),
                    Message.source_message_id == int(source_message_id),
                )
            )
        ).scalar_one_or_none()

    async def case_private_content_unavailable(self, case: Case) -> bool:
        """A tombstone or deletion approval prohibits private content access."""
        if case.content_deleted_at is not None:
            return True
        restricted = await self.db.scalar(
            select(CaseRetentionRecord.id).where(
                CaseRetentionRecord.case_id == case.id,
                CaseRetentionRecord.status.in_(RESTRICTED_MESSAGE_RETENTION_STATES),
            ).limit(1)
        )
        return restricted is not None

    async def case_messages_read_only(self, case: Case) -> bool:
        """Shared client/staff write fence, checked under the Case row lock."""
        if str(case.status) in _CLIENT_MESSAGE_DENIED_STATUSES:
            return True
        return await self.case_private_content_unavailable(case)

    async def _assert_client_message_access(self, *, case: Case, user_id: int) -> None:
        """Check current database authority under the already locked Case row."""
        if (
            isinstance(user_id, bool)
            or not isinstance(user_id, int)
            or user_id <= 0
            or int(case.client_id) != user_id
            or await self.case_messages_read_only(case)
        ):
            raise ClientMessageAccessDenied()

        blocked = await self.db.scalar(
            select(User.is_blocked).where(User.id == user_id)
        )
        if blocked is None or bool(blocked):
            raise ClientMessageAccessDenied()

    @staticmethod
    def _assert_idempotent_case(existing: Message | None, case_id: int) -> Message | None:
        # Telegram message IDs are unique for a sender, not transferable
        # between distinct legal matters even when the sender owns both.
        if existing is not None and int(existing.case_id) != int(case_id):
            raise ClientMessageAccessDenied()
        return existing

    async def get_or_create_client_message(
        self,
        *,
        case: Case,
        user_id: int,
        text: str,
        source_message_id: int | None = None,
    ) -> tuple[Message, bool]:
        # Lock and authorize before any replay lookup. Otherwise a guessed
        # source ID could return a Message from a different or closed Case.
        case = await self.lock_case(case.id)
        await self._assert_client_message_access(case=case, user_id=user_id)
        existing = self._assert_idempotent_case(
            await self.find_client_message_by_source(
                user_id=user_id,
                source_message_id=source_message_id,
            ),
            case.id,
        )
        if existing is not None:
            return existing, False

        msg = Message(
            case_id=case.id,
            sender_type="client",
            sender_id=user_id,
            source_message_id=source_message_id,
            text=text,
            is_read=False,
        )
        try:
            async with self.db.begin_nested():
                self.db.add(msg)
                await self.db.flush()
        except IntegrityError:
            existing = self._assert_idempotent_case(
                await self.find_client_message_by_source(
                    user_id=user_id,
                    source_message_id=source_message_id,
                ),
                case.id,
            )
            if existing is None:
                raise
            return existing, False

        await add_case_history_event(
            self.db,
            actor_type="client",
            actor_id=user_id,
            case_id=case.id,
            action="CLIENT_MESSAGE_CREATED",
            # Audit proves the event, not its sensitive body. The separately
            # retention-governed Message row is the sole business text source.
            new_value={"message_id": msg.id},
        )
        return msg, True

    async def create_client_message(
        self,
        *,
        case: Case,
        user_id: int,
        text: str,
        source_message_id: int | None = None,
    ) -> Message:
        message, _created = await self.get_or_create_client_message(
            case=case,
            user_id=user_id,
            text=text,
            source_message_id=source_message_id,
        )
        return message

    async def create_lawyer_message(
        self,
        *,
        case: Case,
        lawyer_id: int | None,
        text: str,
    ) -> Message:
        case = await self.lock_case(case.id)
        if await self.case_messages_read_only(case):
            raise StaffMessageReadOnlyError()
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
            # Never duplicate privileged legal correspondence in immutable audit.
            new_value={"message_id": msg.id},
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

    async def list_case_messages_page(
        self, case_id: int, *, page: int = 0, page_size: int = 5
    ) -> tuple[list[Message], int, int]:
        """Read-only pagination. The Telegram route must authorize case ownership first."""
        bounded_size = min(max(int(page_size), 1), 10)
        total = int(
            await self.db.scalar(
                select(func.count(Message.id)).where(Message.case_id == case_id)
            ) or 0
        )
        total_pages = max(1, (total + bounded_size - 1) // bounded_size)
        selected = min(max(int(page), 0), total_pages - 1)
        rows = (
            await self.db.execute(
                select(Message)
                .where(Message.case_id == case_id)
                .order_by(Message.created_at.desc(), Message.id.desc())
                .offset(selected * bounded_size)
                .limit(bounded_size)
            )
        ).scalars().all()
        return list(rows), selected, total_pages

    async def mark_client_messages_read(self, case_id: int) -> int:
        case = await self.db.scalar(
            select(Case).where(Case.id == int(case_id)).with_for_update()
        )
        if case is None or await self.case_messages_read_only(case):
            return 0
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

    async def mark_lawyer_messages_read(
        self,
        case_id: int,
        *,
        message_ids: Collection[int] | None = None,
    ) -> int:
        case = await self.db.scalar(
            select(Case).where(Case.id == int(case_id)).with_for_update()
        )
        if case is None or await self.case_messages_read_only(case):
            return 0
        query = select(Message).where(
            Message.case_id == case_id,
            Message.sender_type == "lawyer",
            Message.is_read.is_(False),
        )
        if message_ids is not None:
            ids = sorted({int(item) for item in message_ids})
            if not ids:
                return 0
            query = query.where(Message.id.in_(ids))
        result = await self.db.execute(query)
        messages = list(result.scalars().all())
        for message in messages:
            message.is_read = True
        await self.db.flush()
        return len(messages)
