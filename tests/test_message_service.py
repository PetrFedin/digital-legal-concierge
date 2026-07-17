import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.messages.message_service import MessageService
from app.models import Base
from app.models.case import Case
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
