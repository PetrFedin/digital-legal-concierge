import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.messages.message_service import MessageService
from app.models import Base
from app.models.case import Case
from app.models.audit_log import AuditLog
from app.models.user import User


@pytest.fixture
async def message_db(tmp_path):
    database_path = tmp_path / "messages.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    try:
        yield session_factory
    finally:
        await engine.dispose()


async def create_case(session):
    user = User(
        telegram_id=900001,
        telegram_username="client_test",
        full_name="Тестовый клиент",
    )
    session.add(user)
    await session.flush()

    case = Case(
        case_number="TEST-MSG-001",
        client_id=user.id,
        route="M1",
        status="M1_ACTIVE",
        title="Тест переписки",
    )
    session.add(case)
    await session.flush()
    return user, case


@pytest.mark.asyncio
async def test_message_conversation_lifecycle(message_db):
    async with message_db() as session:
        user, case = await create_case(session)
        service = MessageService(session)

        client_message = await service.create_client_message(
            case=case,
            user_id=user.id,
            text="Нужен ответ по срокам подачи документов.",
        )
        lawyer_message = await service.create_lawyer_message(
            case=case,
            lawyer_id=None,
            text="Документы необходимо подать до указанной судом даты.",
        )
        await session.commit()

        messages = await service.list_case_messages(case.id)
        assert [message.id for message in messages] == [client_message.id, lawyer_message.id]
        assert messages[0].sender_type == "client"
        assert messages[1].sender_type == "lawyer"
        assert messages[0].is_read is False
        assert messages[1].is_read is False

        client_read_count = await service.mark_client_messages_read(case.id)
        lawyer_read_count = await service.mark_lawyer_messages_read(case.id)
        await session.commit()

        assert client_read_count == 1
        assert lawyer_read_count == 1

        refreshed = await service.list_case_messages(case.id)
        assert all(message.is_read for message in refreshed)


@pytest.mark.asyncio
async def test_read_marking_is_scoped_to_case(message_db):
    async with message_db() as session:
        user, first_case = await create_case(session)
        second_case = Case(
            case_number="TEST-MSG-002",
            client_id=user.id,
            route="M1",
            status="M1_ACTIVE",
            title="Второе тестовое дело",
        )
        session.add(second_case)
        await session.flush()

        service = MessageService(session)
        await service.create_client_message(
            case=first_case,
            user_id=user.id,
            text="Сообщение по первому делу.",
        )
        second_message = await service.create_client_message(
            case=second_case,
            user_id=user.id,
            text="Сообщение по второму делу.",
        )
        await session.commit()

        count = await service.mark_client_messages_read(first_case.id)
        await session.commit()

        assert count == 1
        second_messages = await service.list_case_messages(second_case.id)
        assert second_messages[0].id == second_message.id
        assert second_messages[0].is_read is False


@pytest.mark.asyncio
async def test_repeated_telegram_update_creates_one_client_message(message_db):
    async with message_db() as session:
        user, case = await create_case(session)
        service = MessageService(session)

        first, first_created = await service.get_or_create_client_message(
            case=case,
            user_id=user.id,
            text="Тема: Сроки\n\nКогда истекает срок подачи документов?",
            source_message_id=501,
        )
        repeated, repeated_created = await service.get_or_create_client_message(
            case=case,
            user_id=user.id,
            text="Этот повтор не должен создать новую запись.",
            source_message_id=501,
        )
        await session.commit()

        messages = await service.list_case_messages(case.id)
        assert first_created is True
        assert repeated_created is False
        assert repeated.id == first.id
        assert repeated.text == first.text
        assert repeated.source_message_id == 501
        assert [item.id for item in messages] == [first.id]


@pytest.mark.asyncio
async def test_only_visible_team_messages_are_marked_read(message_db):
    async with message_db() as session:
        _user, case = await create_case(session)
        service = MessageService(session)

        visible = await service.create_lawyer_message(
            case=case,
            lawyer_id=None,
            text="Ответ на текущей странице.",
        )
        hidden = await service.create_lawyer_message(
            case=case,
            lawyer_id=None,
            text="Ответ на более ранней странице.",
        )
        await session.commit()

        count = await service.mark_lawyer_messages_read(
            case.id,
            message_ids=[visible.id],
        )
        await session.commit()

        messages = await service.list_case_messages(case.id)
        states = {item.id: item.is_read for item in messages}
        assert count == 1
        assert states[visible.id] is True
        assert states[hidden.id] is False

        empty_count = await service.mark_lawyer_messages_read(
            case.id,
            message_ids=[],
        )
        assert empty_count == 0


@pytest.mark.asyncio
async def test_new_message_bodies_are_absent_from_immutable_audit(message_db):
    """Old audit rows remain immutable; new rows must never carry correspondence."""
    async with message_db() as session:
        user, case = await create_case(session)
        service = MessageService(session)
        client_secret = "Клиент: паспортный номер 9999 999999"
        lawyer_secret = "Юрист: конфиденциальная тактика защиты"

        client = await service.create_client_message(
            case=case, user_id=user.id, text=client_secret,
        )
        lawyer = await service.create_lawyer_message(
            case=case, lawyer_id=None, text=lawyer_secret,
        )
        await session.commit()

        # Full text remains available in the separate, retention-governed messages.
        messages = await service.list_case_messages(case.id)
        assert [item.text for item in messages] == [client_secret, lawyer_secret]
        assert [item.id for item in messages] == [client.id, lawyer.id]

        events = list((
            await session.execute(
                select(AuditLog).where(
                    AuditLog.entity_type == "case",
                    AuditLog.entity_id == case.id,
                    AuditLog.action.in_((
                        "CLIENT_MESSAGE_CREATED", "LAWYER_MESSAGE_CREATED",
                    )),
                )
                .order_by(AuditLog.id)
            )
        ).scalars().all())
        assert len(events) == 2
        assert [e.action for e in events] == [
            "CLIENT_MESSAGE_CREATED", "LAWYER_MESSAGE_CREATED",
        ]
        assert [e.new_value for e in events] == [
            {"message_id": client.id},
            {"message_id": lawyer.id},
        ]
        for event in events:
            event_dump = repr((event.old_value, event.new_value, event.comment))
            assert client_secret not in event_dump
            assert lawyer_secret not in event_dump
            assert event.event_hash is not None


@pytest.mark.asyncio
async def test_client_message_cannot_write_to_another_clients_case(message_db):
    from app.domain.messages.message_service import ClientMessageAccessDenied
    from app.models.message import Message

    async with message_db() as session:
        owner, own_case = await create_case(session)
        outsider = User(telegram_id=900002)
        session.add(outsider)
        await session.flush()
        foreign_case = Case(
            case_number="MSG-FOREIGN-001",
            client_id=outsider.id,
            status="M1_DOCUMENTS_PENDING",
            route="M1",
        )
        session.add(foreign_case)
        await session.flush()

        service = MessageService(session)
        # Both attack orientations must fail even if caller supplies a Case.
        with pytest.raises(ClientMessageAccessDenied):
            await service.get_or_create_client_message(
                case=foreign_case,
                user_id=owner.id,
                text="Do not write into another client's case.",
                source_message_id=19001,
            )
        with pytest.raises(ClientMessageAccessDenied):
            await service.create_client_message(
                case=own_case,
                user_id=outsider.id,
                text="Do not impersonate owner.",
                source_message_id=19002,
            )
        assert (await session.scalars(select(Message))).all() == []
        assert (await session.scalars(select(AuditLog).where(
            AuditLog.action == "CLIENT_MESSAGE_CREATED",
        ))).all() == []


@pytest.mark.asyncio
async def test_idempotent_telegram_message_cannot_move_between_owned_cases(message_db):
    from app.domain.messages.message_service import ClientMessageAccessDenied

    async with message_db() as session:
        owner, first = await create_case(session)
        second = Case(
            case_number="MSG-SECOND-001",
            client_id=owner.id,
            route="M1",
            status="M1_DOCUMENTS_PENDING",
        )
        session.add(second)
        await session.flush()

        service = MessageService(session)
        source_id = 21001
        original, new = await service.get_or_create_client_message(
            case=first, user_id=owner.id, text="Original message",
            source_message_id=source_id,
        )
        assert new is True
        # A repeated Telegram source id must not silently return the old
        # message as if it belonged to another object of the same owner.
        with pytest.raises(ClientMessageAccessDenied):
            await service.get_or_create_client_message(
                case=second, user_id=owner.id, text="Forged case binding",
                source_message_id=source_id,
            )

        same, created = await service.get_or_create_client_message(
            case=first, user_id=owner.id, text="Duplicate retry payload",
            source_message_id=source_id,
        )
        assert same.id == original.id and created is False
        assert await service.list_case_messages(second.id) == []
        assert len(await service.list_case_messages(first.id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", ("M1_CLOSED", "M2_CLOSED", "ARCHIVED", "ERROR"),
)
async def test_terminal_or_error_case_rejects_new_and_replayed_messages(
    message_db, status,
):
    from app.domain.messages.message_service import ClientMessageAccessDenied

    async with message_db() as session:
        owner, case = await create_case(session)
        service = MessageService(session)
        first, _ = await service.get_or_create_client_message(
            case=case, user_id=owner.id, text="Existing before close",
            source_message_id=22001,
        )
        case.status = status
        await session.flush()

        for source_id in (22001, 22002):
            with pytest.raises(ClientMessageAccessDenied):
                await service.get_or_create_client_message(
                    case=case, user_id=owner.id, text="Denied",
                    source_message_id=source_id,
                )
        assert [item.id for item in await service.list_case_messages(case.id)] == [first.id]


@pytest.mark.asyncio
async def test_deleted_case_and_blocked_owner_cannot_send_messages(message_db):
    from datetime import datetime, timezone
    from app.domain.messages.message_service import ClientMessageAccessDenied

    async with message_db() as session:
        owner, case = await create_case(session)
        service = MessageService(session)

        owner.is_blocked = True
        await session.flush()
        with pytest.raises(ClientMessageAccessDenied):
            await service.create_client_message(
                case=case, user_id=owner.id, text="Blocked",
            )

        owner.is_blocked = False
        case.content_deleted_at = datetime.now(timezone.utc)
        await session.flush()
        with pytest.raises(ClientMessageAccessDenied):
            await service.create_client_message(
                case=case, user_id=owner.id, text="Erased",
            )
        assert await service.list_case_messages(case.id) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ("APPROVED", "EXECUTING", "FAILED", "COMPLETED"))
async def test_retention_deletion_blocks_client_message_even_with_open_case(
    message_db, status,
):
    from datetime import datetime, timezone
    from app.domain.messages.message_service import ClientMessageAccessDenied
    from app.models.case_retention import CaseRetentionRecord

    async with message_db() as session:
        owner, case = await create_case(session)
        session.add(CaseRetentionRecord(
            case_id=case.id,
            status=status,
            retention_due_at=datetime.now(timezone.utc),
        ))
        await session.flush()

        with pytest.raises(ClientMessageAccessDenied):
            await MessageService(session).get_or_create_client_message(
                case=case, user_id=owner.id,
                text="Do not send when retention has reached deletion admission.",
                source_message_id=24001,
            )
        assert await MessageService(session).list_case_messages(case.id) == []
