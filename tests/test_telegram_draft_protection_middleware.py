from pathlib import Path

import pytest

from app.bot.draft_protection import (
    CONSULTATION_DRAFT,
    DraftMessageNavigationProtectionMiddleware,
    DraftProtectionMiddleware,
    is_draft_flow_callback,
    is_protected_navigation_message,
    protected_draft_kind,
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
    def __init__(self, text=""):
        self.text = text
        self.edits = []
        self.answers = []

    async def edit_text(self, text, *, reply_markup):
        self.edits.append((text, reply_markup))

    async def answer(self, text, *, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data: str):
        self.data = data
        self.message = FakeMessage()
        self.answers = []

    async def answer(self, text=None, *, show_alert=False):
        self.answers.append((text, show_alert))


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


@pytest.mark.asyncio
async def test_saved_message_draft_blocks_stale_inline_navigation_without_calling_handler():
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
    assert _callbacks(markup) == ["message_review_return", "message_discard_confirm"]
    assert event.answers[-1] == ("Черновик сохранён.", False)


@pytest.mark.asyncio
async def test_saved_consultation_draft_blocks_stale_inline_navigation():
    middleware = DraftProtectionMiddleware()
    event = FakeCallback("my_case_open")
    state = FakeState(
        {"description_draft": "Подробный вопрос юристу по новой ситуации."}
    )
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True

    result = await middleware(handler, event, {"state": state})

    assert result is None
    assert called is False
    text, markup = event.message.edits[-1]
    assert "черновик вопроса к консультации" in text
    assert _callbacks(markup) == [
        "consult_description_review",
        "consult_description_discard_confirm",
    ]


@pytest.mark.asyncio
async def test_message_draft_flow_callbacks_are_allowed_to_finish_or_edit_the_draft():
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


@pytest.mark.asyncio
async def test_consultation_draft_allows_only_its_own_edit_confirm_and_discard_flow():
    middleware = DraftProtectionMiddleware()
    state = FakeState({"description_draft": "Достаточно длинный вопрос для консультации."})

    allowed = [
        "consult_description_review",
        "consult_description_edit",
        "consult_description_confirm",
        "consult_description_discard_confirm",
        "consult_description_discard",
        "consult_subject_start",
        "consult_subject_new",
        "consult_subject_case:17",
    ]
    for callback_data in allowed:
        event = FakeCallback(callback_data)
        calls = []

        async def handler(_event, _data):
            calls.append(callback_data)
            return "handled"

        assert await middleware(handler, event, {"state": state}) == "handled"
        assert calls == [callback_data]
        assert event.message.edits == []

    blocked = FakeCallback("message_create")
    called = False

    async def blocked_handler(_event, _data):
        nonlocal called
        called = True

    assert await middleware(blocked_handler, blocked, {"state": state}) is None
    assert called is False
    assert blocked.message.edits


def test_draft_callback_classifier_is_kind_specific_and_narrow():
    assert is_draft_flow_callback("message_submit")
    assert is_draft_flow_callback("msg_cat_case")
    assert is_draft_flow_callback("msg_urgency_normal")
    assert not is_draft_flow_callback("message_create")
    assert not is_draft_flow_callback("contact_lawyer")
    assert not is_draft_flow_callback("documents_open")
    assert not is_draft_flow_callback("nav_home")

    assert is_draft_flow_callback("consult_description_review", CONSULTATION_DRAFT)
    assert is_draft_flow_callback("consult_subject_case:5", CONSULTATION_DRAFT)
    assert is_draft_flow_callback("consult_description_discard", CONSULTATION_DRAFT)
    assert not is_draft_flow_callback("message_submit", CONSULTATION_DRAFT)
    assert not is_draft_flow_callback("nav_cancel", CONSULTATION_DRAFT)


def test_protected_draft_kind_detects_both_supported_draft_types():
    assert protected_draft_kind({"draft_text": "вопрос"}) == "message"
    assert protected_draft_kind({"description_draft": "описание"}) == "consultation"
    assert protected_draft_kind({"draft_text": "  ", "description_draft": "описание"}) == "consultation"
    assert protected_draft_kind({}) is None


@pytest.mark.asyncio
async def test_callback_without_saved_draft_is_not_intercepted():
    middleware = DraftProtectionMiddleware()
    event = FakeCallback("documents_open")
    state = FakeState({"draft_text": "  ", "description_draft": ""})

    async def handler(_event, _data):
        return "handled"

    assert await middleware(handler, event, {"state": state}) == "handled"
    assert event.message.edits == []


@pytest.mark.asyncio
async def test_persistent_reply_navigation_cannot_drop_consultation_draft():
    middleware = DraftMessageNavigationProtectionMiddleware()
    event = FakeMessage("🏠 Главная")
    state = FakeState({"description_draft": "Достаточно длинный вопрос для консультации."})
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True

    result = await middleware(handler, event, {"state": state})

    assert result is None
    assert called is False
    assert event.answers
    text, markup = event.answers[-1]
    assert "черновик вопроса к консультации" in text
    assert _callbacks(markup) == [
        "consult_description_review",
        "consult_description_discard_confirm",
    ]


@pytest.mark.asyncio
async def test_regular_consultation_text_is_not_blocked_by_reply_navigation_guard():
    middleware = DraftMessageNavigationProtectionMiddleware()
    event = FakeMessage("Хочу уточнить ответственность за нарушение срока передачи квартиры")
    state = FakeState({"description_draft": "Предыдущий черновик вопроса достаточной длины."})

    async def handler(_event, _data):
        return "handled"

    assert await middleware(handler, event, {"state": state}) == "handled"
    assert event.answers == []


def test_reply_navigation_classifier_covers_only_actions_that_can_leave_the_flow():
    assert is_protected_navigation_message("🏠 Главная")
    assert is_protected_navigation_message("📁 Моё дело")
    assert is_protected_navigation_message("💬 Связаться с юристом")
    assert is_protected_navigation_message("/cancel")
    assert not is_protected_navigation_message("/help")
    assert not is_protected_navigation_message("Обычный текст вопроса")


@pytest.mark.asyncio
async def test_callback_fsm_read_failure_fails_closed_before_navigation_mutation():
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


@pytest.mark.asyncio
async def test_reply_navigation_fsm_read_failure_also_fails_closed():
    middleware = DraftMessageNavigationProtectionMiddleware()
    event = FakeMessage("/cancel")
    state = FakeState(error=RuntimeError("redis unavailable"))
    called = False

    async def handler(_event, _data):
        nonlocal called
        called = True

    result = await middleware(handler, event, {"state": state})

    assert result is None
    assert called is False
    assert event.answers[-1][0] == (
        "Не удалось проверить сохранённый черновик. Повторите действие позже."
    )


def test_dispatcher_registers_draft_protection_before_other_handlers():
    source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")

    message_protection = source.index(
        "dispatcher.message.middleware(DraftMessageNavigationProtectionMiddleware())"
    )
    message_flood = source.index("dispatcher.message.middleware(flood_control)")
    callback_protection = source.index(
        "dispatcher.callback_query.middleware(DraftProtectionMiddleware())"
    )
    callback_flood = source.index("dispatcher.callback_query.middleware(flood_control)")
    acknowledge = source.index(
        "dispatcher.callback_query.middleware(CallbackAcknowledgeMiddleware())"
    )
    assert message_protection < message_flood
    assert callback_protection < callback_flood < acknowledge
