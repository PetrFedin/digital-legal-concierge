"""Dynamic Telegram UI regression for a Case write rejected by domain authority."""
from types import SimpleNamespace

import pytest

from app.bot.screens import messages
from app.domain.messages.message_service import ClientMessageAccessDenied


class State:
    def __init__(self):
        self.data = {
            "draft_text": "Sensitive message that must never switch cases.",
            "source_message_id": 581002,
            "case_id": 34,
            "category": "Ход дела",
            "urgency": "Обычный",
        }
        self.current = messages.MessageStates.confirming_message.state
        self.clear_count = 0

    async def get_data(self):
        return dict(self.data)

    async def get_state(self):
        return self.current

    async def clear(self):
        self.data.clear()
        self.current = None
        self.clear_count += 1

    async def set_state(self, state):
        self.current = getattr(state, "state", state)


class SentMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class Callback:
    data = "message_submit"

    def __init__(self):
        self.message = SentMessage()
        self.answers = []

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


class Session:
    def __init__(self):
        self.rollbacks = 0
        self.commits = 0

    async def rollback(self):
        self.rollbacks += 1

    async def commit(self):
        self.commits += 1


class CaseService:
    async def get_active_case_for_user(self, user_id):
        assert user_id == 900
        return SimpleNamespace(id=34, case_number="TEST-34", client_id=user_id)


class Context:
    def __init__(self, _db):
        self.case_service = CaseService()

    async def get_user_from_callback(self, callback):
        return SimpleNamespace(id=900)


class DenyingMessageService:
    def __init__(self, db):
        pass

    async def get_or_create_client_message(self, **kwargs):
        raise ClientMessageAccessDenied()


def _callbacks(markup):
    return [item.callback_data for row in markup.inline_keyboard for item in row]


@pytest.mark.asyncio
async def test_domain_denial_discards_case_bound_draft_and_does_not_claim_delivery(monkeypatch):
    monkeypatch.setattr(messages, "BotContextService", Context)
    monkeypatch.setattr(messages, "MessageService", DenyingMessageService)

    async def no_notification(*args, **kwargs):
        raise AssertionError("Notification delivery must never happen after access denial")

    monkeypatch.setattr(messages, "deliver_selected_notifications", no_notification)

    db = Session()
    state = State()
    callback = Callback()
    await messages.message_submit(callback, state, db)

    assert db.rollbacks == 1
    assert db.commits == 0
    assert state.clear_count == 1
    assert state.data == {}
    text, markup = callback.message.edits[-1]
    assert "Вопрос не отправлен" in text
    assert "Выберите действующее обращение" in text
    assert "Sensitive message" not in text
    assert _callbacks(markup) == ["cases_list", "nav_home"]
    assert callback.answers[-1][0] == "Отправка по делу недоступна."
