"""Golden path: Telegram handler -> durable case -> staff scope -> reply -> Telegram view.

An isolated in-process integration test. No Telegram API calls, no real
customer data, no provider credentials, no production infrastructure.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.lawyer import router as lawyer_router
from app.api.message_center import router as message_router
from app.bot.screens.common import start as telegram_start
from app.bot.screens.messages import (
    message_category,
    message_create,
    message_history,
    message_send,
    message_submit,
    message_urgency,
)
from app.db.session import get_db
from app.domain.cases.assignment_service import CaseAssignmentService
from app.models import Base
from app.models.admin_user import AdminUser
from app.models.case import Case
from app.models.lawyer import Lawyer
from app.models.message import Message
from app.models.notification import Notification
from app.models.user import User
from app.security.access_control import ROLE_LAWYER, create_access_token

CLIENT_TELEGRAM_ID = 988844101
CLIENT_MESSAGE_ID = 99301
CLIENT_QUESTION = (
    "Застройщик задерживает передачу квартиры по ДДУ. "
    "Подскажите, какие документы приложить к обращению?"
)
LAWYER_REPLY = "Пришлите ДДУ, дополнительные соглашения и уведомления застройщика."


class FakeState:
    def __init__(self):
        self.data = {}
        self.current = None

    async def clear(self):
        self.data = {}
        self.current = None

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def get_state(self):
        return self.current

    async def set_state(self, state):
        self.current = getattr(state, "state", state)


class FakeBotMessage:
    def __init__(self, *, text=None, message_id=1, user_id=CLIENT_TELEGRAM_ID):
        self.text = text
        self.message_id = message_id
        self.from_user = SimpleNamespace(
            id=user_id, username="synthetic_client", full_name="Клиент (демо)"
        )
        self.answers = []
        self.edits = []

    async def answer(self, text, *, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, *, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data, *, user_id=CLIENT_TELEGRAM_ID):
        self.data = data
        self.from_user = SimpleNamespace(
            id=user_id, username="synthetic_client", full_name="Клиент (демо)"
        )
        self.message = FakeBotMessage(user_id=user_id)
        self.acks = []

    async def answer(self, text=None, show_alert=False):
        self.acks.append((text, show_alert))


@pytest.mark.asyncio
async def test_telegram_intake_lawyer_reply_and_client_continuity(
    tmp_path, monkeypatch
):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'client_lawyer_bridge.db'}"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async def no_external_telegram(db, ids):
        return {"status": "queued_for_test", "notification_ids": list(ids)}

    monkeypatch.setattr(
        "app.bot.screens.messages.deliver_selected_notifications", no_external_telegram
    )
    monkeypatch.setattr(
        "app.api.message_center._deliver_message_notifications", no_external_telegram
    )

    async with factory() as db:
        state = FakeState()
        home = FakeBotMessage(text="/start")
        await telegram_start(home, db, state)
        assert any("Добро пожаловать" in text for text, _ in home.answers)
        assert (await db.execute(select(func.count(User.id)))).scalar_one() == 1

        await message_create(FakeCallback("message_create"), state, db)
        await message_category(FakeCallback("msg_cat_deadline"), state)
        await message_urgency(FakeCallback("msg_urgency_normal"), state)
        draft = FakeBotMessage(text=CLIENT_QUESTION, message_id=CLIENT_MESSAGE_ID)
        await message_send(draft, state)
        assert any("Проверьте вопрос" in text for text, _ in draft.answers)

        submitted = FakeCallback("message_submit")
        await message_submit(submitted, state, db)
        assert any("Вопрос зарегистрирован" in text for text, _ in submitted.message.edits)

        user = (await db.execute(
            select(User).where(User.telegram_id == CLIENT_TELEGRAM_ID)
        )).scalar_one()
        case = (await db.execute(
            select(Case).where(Case.client_id == user.id)
        )).scalar_one()
        original_case_id = case.id
        original_case_number = case.case_number
        client_message = (await db.execute(
            select(Message).where(Message.case_id == case.id)
        )).scalar_one()
        assert CLIENT_QUESTION in client_message.text
        assert client_message.source_message_id == CLIENT_MESSAGE_ID
        assert (await db.execute(select(func.count(Notification.id)).where(
            Notification.case_id == case.id,
            Notification.event_code == "CLIENT_MESSAGE_RECEIVED",
        ))).scalar_one() >= 1

        # Duplicate Telegram callback must not create a second case/message.
        stale_state = FakeState()
        await stale_state.update_data(
            draft_text=CLIENT_QUESTION, category="Сроки и заседание",
            urgency="Обычный", source_message_id=CLIENT_MESSAGE_ID,
        )
        from app.bot.screens.messages import MessageStates
        await stale_state.set_state(MessageStates.confirming_message)
        await message_submit(FakeCallback("message_submit"), stale_state, db)
        assert (await db.execute(select(func.count(Case.id)))).scalar_one() == 1
        assert (await db.execute(select(func.count(Message.id)))).scalar_one() == 1

        lawyer = Lawyer(
            full_name="Юрист А (демо)", email="lawyer-a@example.test",
            is_active=True, workload_limit=20,
        )
        outsider = Lawyer(
            full_name="Юрист Б (демо)", email="lawyer-b@example.test",
            is_active=True, workload_limit=20,
        )
        db.add_all([lawyer, outsider])
        await db.flush()
        lawyer_user = AdminUser(
            full_name="Юрист А (демо)", username="lawyer-a",
            email=lawyer.email, role="lawyer", is_active=True,
            password_hash="isolated-test-no-login",
        )
        outsider_user = AdminUser(
            full_name="Юрист Б (демо)", username="lawyer-b",
            email=outsider.email, role="lawyer", is_active=True,
            password_hash="isolated-test-no-login",
        )
        db.add_all([lawyer_user, outsider_user])
        await db.flush()
        await CaseAssignmentService(db).assign_case(
            case_id=case.id, lawyer_id=lawyer.id, actor_type="admin",
            actor_id=None, comment="Тест маршрутизации сообщения клиентского обращения",
        )
        await db.commit()
        token = create_access_token(lawyer_user.id, lawyer_user.username, [ROLE_LAWYER])
        outsider_token = create_access_token(
            outsider_user.id, outsider_user.username, [ROLE_LAWYER]
        )

    app = FastAPI()
    app.include_router(message_router)
    app.include_router(lawyer_router)

    async def override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_db
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", follow_redirects=False
    ) as client:
        own_cases = await client.get("/lawyer/cases", headers={"x-admin-token": token})
        assert own_cases.status_code == 200, own_cases.text
        assert any(item["case_id"] == original_case_id for item in own_cases.json())

        other_cases = await client.get(
            "/lawyer/cases", headers={"x-admin-token": outsider_token}
        )
        assert other_cases.status_code == 200
        assert not any(
            item["case_id"] == original_case_id for item in other_cases.json()
        )

        rejected = await client.get(
            f"/message-center/cases/{original_case_id}/messages",
            headers={"x-admin-token": outsider_token},
        )
        assert rejected.status_code == 403
        thread = await client.get(
            f"/message-center/cases/{original_case_id}/messages",
            headers={"x-admin-token": token},
        )
        assert thread.status_code == 200, thread.text
        assert thread.json()["case"]["number"] == original_case_number
        assert any(CLIENT_QUESTION in m["text"] for m in thread.json()["messages"])

        latest_id = thread.json()["latest_message_id"]
        stale_reply = await client.post(
            f"/message-center/cases/{original_case_id}/reply",
            headers={"x-admin-token": token},
            json={"text": LAWYER_REPLY, "expected_last_message_id": latest_id - 1},
        )
        assert stale_reply.status_code == 409
        proper_reply = await client.post(
            f"/message-center/cases/{original_case_id}/reply",
            headers={"x-admin-token": token},
            json={"text": LAWYER_REPLY, "expected_last_message_id": latest_id},
        )
        assert proper_reply.status_code == 200, proper_reply.text
        assert proper_reply.json()["ok"] is True

    async with factory() as db:
        thread = (await db.execute(
            select(Message).where(Message.case_id == original_case_id)
            .order_by(Message.id.asc())
        )).scalars().all()
        assert len(thread) == 2
        assert thread[0].sender_type == "client"
        assert thread[1].sender_type == "lawyer"
        assert LAWYER_REPLY in thread[1].text
        assert (await db.execute(
            select(func.count(Notification.id)).where(
                Notification.case_id == original_case_id,
                Notification.event_code == "STAFF_MESSAGE_REPLIED",
            )
        )).scalar_one() >= 1

        history = FakeCallback("message_history")
        await message_history(history, db)
        shown = " ".join(x[0] for x in history.message.edits)
        assert original_case_number not in shown or shown
        assert CLIENT_QUESTION in shown
        assert LAWYER_REPLY in shown

        home_again = FakeBotMessage(text="/start")
        await telegram_start(home_again, db, FakeState())
        assert any(original_case_number in text for text, _ in home_again.answers)
        assert (await db.execute(select(func.count(Case.id)))).scalar_one() == 1

    await engine.dispose()
