from pathlib import Path

import pytest

from app.bot.draft_protection import (
    DraftProtectionMiddleware,
    is_draft_flow_callback,
)


ROOT = Path(__file__).resolve().parents[1]


class FakeState:
    def __init__(self, data=None, *, error: Exception | None = None):
        self.data = data or {}
        self.error = error

    async def get_data(self):
        if self.error:
            raise self.error
        return self.data


class FakeMessage:
    def __init__(self):
        self.edits = []
        self.answers = []

    async def edit_text(self, text, *, reply_markup):
        self.edits.append((text, reply_markup))

    async def answer(self, text, *, reply_markup):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, *, show_alert=False):
        self.answers.append((text, show_alert))


@pytest.mark.asyncio
async def test_saved_draft_blocks_stale_inline_navigation_without_calling_handler():
    middleware = DraftProtectionMiddleware()
    event = FakeCallback("calc_start")
    state = FakeState({"draft_text": "Важный вопрос по сроку сдачи"})
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True
        return "handled"

    result = await middleware(handler, event, {"state": state})

    assert result is None
    assert called is False
    assert len(event.message.edits) == 1
    text, markup = event.message.edits[0]
    assert "неотправленный черновик" in text
    callbacks = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]
    assert callbacks == ["message_review_return", "message_discard_confirm"]
    assert event.answers[-1] == ("Черновик сохранён.", False)


@pytest.mark.asyncio
async def test_draft_flow_callbacks_are_allowed_to_finish_or_edit_the_draft():
    middleware = DraftProtectionMiddleware()
    state = FakeState({"draft_text": "Важный вопрос"})

    for callback_data in [
        "message_review_return",
        "message_submit",
        "message_edit_text",
        "message_discard_confirm",
        "message_discard",
        "msg_cat_documents",
        "msg_urgency_critical",
    ]:
        event = FakeCallback(callback_data)
        calls = []

        async def handler(_event, _data):
            calls.append(callback_data)
            return "handled"

        assert await middleware(handler, event, {"state": state}) == "handled"
        assert calls == [callback_data]
        assert event.message.edits == []


def test_draft_callback_classifier_is_narrow():
    assert is_draft_flow_callback("message_submit")
    assert is_draft_flow_callback("msg_cat_case")
    assert is_draft_flow_callback("msg_urgency_normal")
    assert not is_draft_flow_callback("message_create")
    assert not is_draft_flow_callback("contact_lawyer")
    assert not is_draft_flow_callback("documents_open")
    assert not is_draft_flow_callback("nav_home")


@pytest.mark.asyncio
async def test_callback_without_saved_draft_is_not_intercepted():
    middleware = DraftProtectionMiddleware()
    event = FakeCallback("documents_open")
    state = FakeState({"draft_text": "  "})

    async def handler(_event, _data):
        return "handled"

    assert await middleware(handler, event, {"state": state}) == "handled"
    assert event.message.edits == []


@pytest.mark.asyncio
async def test_fsm_read_failure_fails_closed_before_navigation_mutation():
    middleware = DraftProtectionMiddleware()
    event = FakeCallback("nav_home")
    state = FakeState(error=RuntimeError("redis unavailable"))
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True

    result = await middleware(handler, event, {"state": state})

    assert result is None
    assert called is False
    assert event.answers[-1] == (
        "Не удалось проверить сохранённый черновик. Повторите действие позже.",
        True,
    )


def test_dispatcher_registers_draft_protection_before_other_callback_handlers():
    source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")

    protection = source.index(
        "dispatcher.callback_query.middleware(DraftProtectionMiddleware())"
    )
    flood = source.index("dispatcher.callback_query.middleware(flood_control)")
    acknowledge = source.index(
        "dispatcher.callback_query.middleware(CallbackAcknowledgeMiddleware())"
    )
    assert protection < flood < acknowledge
