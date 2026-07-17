from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.domain.messages.message_service import MessageService
from app.models import Base
from app.models.case import Case
from app.models.message import Message
from app.models.user import User


@pytest.fixture
async def message_db(tmp_path):
    database_path = tmp_path / "messages.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with engine.begin() as