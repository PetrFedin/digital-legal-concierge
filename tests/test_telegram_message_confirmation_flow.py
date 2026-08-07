from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.bot.screens.messages import (
    DRAFT_PREVIEW_LIMIT,
    MessageStates,
    _draft_review_text,
    message_back_urgency,
    message_discard,
    message_discard_confirm,
    message_send,
    message_urgency,
)


class FakeState:
    def __init__(self, data=None, current=None):
        self.data = dict(data or {})
        self.current = current

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def get_data(self):
        return dict(self.data)

    async def set_state(self, state):
        self.current = getattr(state, "state", state)

    async def get_state(self):
        return self.current

    async def clear(self):
        self.data.clear()
        self.current = None


class FakeBotMessage:
    def __init__(self, *, text=None, message_id=1):
        self.text = text
        self.message_id = message_id
        self.answers: list[tuple[str, object]] = []
        self.edits: list[tuple[str, object]] = []

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeBotMessage()
        self.answers: list[tuple[object, bool]] = []
        self.from_user = SimpleNamespace(id=1, username=None, full_name="Клиент")

    async def answer(self, text=None, show_alert=False):
        self.answers.append((text, show_alert))


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_message_text_becomes_a_saved_draft_before_submission():
    state = FakeState(
        {
            "category": "Документы",
            "urgency": "Обычный",
            "case_number": "M2-DRAFT-01",
        },
        current=MessageStates.waiting_message.state,
    )
    message = FakeBotMessage(
        text="Нужно проверить документы и понять, какие действия выполнить дальше.",
        message_id=8123,
    )

    await message_send(message, state)

    assert state.current == MessageStates.confirming_message.state
    assert state.data["source_message_id"] == 8123
    assert state.data["draft_text"] == message.text
    assert len(message.answers) == 1
    review_text, markup = message.answers[0]
    assert "Проверьте вопрос перед отправкой" in review_text
    assert "Ничего не будет отправлено" in review_text
    assert _callbacks(markup) == [
        "message_submit",
        "message_edit_text",
        "message_back_urgency",
        "message_back_category",
        "message_discard_confirm",
    ]


@pytest.mark.asyncio
async def test_back_to_urgency_preserves_draft_and_returns_to_review():
    draft = "Нужно сохранить этот текст, пока клиент меняет только срочность вопроса."
    state = FakeState(
        {
            "category": "Ход дела",
            "urgency": "Обычный",
            "draft_text": draft,
            "source_message_id": 9001,
        },
        current=MessageStates.confirming_message.state,
    )

    back = FakeCallback("message_back_urgency")
    await message_back_urgency(back, state, db=None)

    assert state.current == MessageStates.choosing_urgency.state
    assert state.data["draft_text"] == draft
    assert "Насколько срочно нужен ответ" in back.message.edits[-1][0]

    choose = FakeCallback("msg_urgency_soon")
    await message_urgency(choose, state)

    assert state.current == MessageStates.confirming_message.state
    assert state.data["draft_text"] == draft
    assert state.data["urgency"] == "Нужен ответ сегодня"
    assert "Проверьте вопрос перед отправкой" in choose.message.edits[-1][0]


@pytest.mark.asyncio
async def test_discard_requires_confirmation_and_finishes_with_recovery_actions():
    state = FakeState(
        {
            "category": "Документы",
            "urgency": "Обычный",
            "draft_text": "Черновик, который нельзя удалить случайным нажатием кнопки.",
            "source_message_id": 42,
        },
        current=MessageStates.confirming_message.state,
    )

    confirm = FakeCallback("message_discard_confirm")
    await message_discard_confirm(confirm, state)

    assert state.data["draft_text"].startswith("Черновик")
    assert _callbacks(confirm.message.edits[-1][1]) == [
        "message_review_return",
        "message_discard",
    ]

    discard = FakeCallback("message_discard")
    await message_discard(discard, state)

    assert state.data == {}
    assert state.current is None
    assert "Ничего не отправлено" in discard.message.edits[-1][0]
    assert _callbacks(discard.message.edits[-1][1]) == [
        "message_create",
        "contact_lawyer",
        "nav_home",
    ]


def test_long_draft_review_stays_within_telegram_message_limit():
    draft = "д" * 4000
    rendered = _draft_review_text(
        {
            "category": "Другой вопрос",
            "urgency": "Критично: срок менее 24 часов",
            "draft_text": draft,
        }
    )

    assert DRAFT_PREVIEW_LIMIT == 2800
    assert len(rendered) < 4096
    assert "будет отправлен весь сохранённый текст" in rendered
