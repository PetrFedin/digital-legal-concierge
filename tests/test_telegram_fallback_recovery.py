from pathlib import Path

import pytest

from app.bot.screens.fallback import stale_callback, unknown_message


ROOT = Path(__file__).resolve().parents[1]


class FakeState:
    def __init__(self, value=None, *, error: Exception | None = None):
        self.value = value
        self.error = error

    async def get_state(self):
        if self.error:
            raise self.error
        return self.value


class FakeCallback:
    def __init__(self):
        self.answers = []

    async def answer(self, text=None, *, show_alert=False):
        self.answers.append((text, show_alert))


class FakeMessage:
    def __init__(self):
        self.answers = []

    async def answer(self, text, **kwargs):
        self.answers.append((text, kwargs))


@pytest.mark.asyncio
async def test_unknown_callback_does_not_clear_or_replace_active_flow():
    callback = FakeCallback()
    state = FakeState("CalculatorStates:waiting_contract_price")

    await stale_callback(callback, db=None, state=state)

    assert callback.answers == [
        (
            "Эта кнопка относится к другому экрану. Завершите текущий шаг по последней инструкции или используйте /cancel.",
            True,
        )
    ]


@pytest.mark.asyncio
async def test_unknown_message_does_not_clear_active_flow():
    message = FakeMessage()
    state = FakeState("DocumentUploadStates:waiting_file")

    await unknown_message(message, db=None, state=state)

    assert len(message.answers) == 1
    assert "Данные не сброшены" in message.answers[0][0]
    assert "/cancel" in message.answers[0][0]


@pytest.mark.asyncio
async def test_fallback_fails_closed_when_fsm_cannot_be_read():
    callback = FakeCallback()
    state = FakeState(error=RuntimeError("redis unavailable"))

    await stale_callback(callback, db=None, state=state)

    assert callback.answers == [
        (
            "Не удалось проверить текущее действие. Повторите позже или используйте /menu.",
            True,
        )
    ]


def test_fallback_router_is_registered_last_and_never_clears_fsm():
    bot_source = (ROOT / "app/bot/bot.py").read_text(encoding="utf-8")
    fallback_source = (ROOT / "app/bot/screens/fallback.py").read_text(encoding="utf-8")

    router_block = bot_source[
        bot_source.index("for router in ["): bot_source.index("]:", bot_source.index("for router in ["))
    ]
    assert router_block.rstrip().endswith("fallback.router,")
    assert "state.clear" not in fallback_source
    assert "@router.callback_query()" in fallback_source
    assert "@router.message()" in fallback_source
    assert "Эта кнопка больше не актуальна" in fallback_source
    assert "Не удалось распознать действие" in fallback_source
