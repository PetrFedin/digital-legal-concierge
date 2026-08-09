from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens.consultation_results import _render_result, _safe_edit
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus


class FakeMessage:
    def __init__(self, *, edit_error: Exception | None = None):
        self.edit_error = edit_error
        self.edits = []
        self.answers = []

    async def edit_text(self, text, *, reply_markup):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, reply_markup))

    async def answer(self, text, *, reply_markup):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, *, edit_error: Exception | None = None):
        self.message = FakeMessage(edit_error=edit_error)
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
async def test_result_edit_falls_back_to_new_message_when_old_message_is_not_editable():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _safe_edit(
        callback,
        "Итог сохранён",
        reply_markup=SimpleNamespace(inline_keyboard=[]),
    )

    assert callback.message.answers == [
        ("Итог сохранён", callback.message.answers[0][1])
    ]
    assert callback.answers[-1] == ("Итог открыт новым сообщением.", False)


@pytest.mark.asyncio
async def test_terminal_result_has_action_center_hierarchy_and_one_primary_next_step():
    callback = FakeCallback()
    case = SimpleNamespace(
        case_number="M2-RESULT-17",
        status=CaseStatus.M2_CONSULTATION_DONE,
    )
    consultation = SimpleNamespace(
        status=ConsultationStatus.DONE,
        decision="follow_up",
        lawyer_result="Нужна повторная встреча после получения ответа застройщика.",
        scheduled_at=None,
    )

    await _render_result(callback, case=case, consultation=consultation)

    text, markup = callback.message.edits[-1]
    assert text.startswith("👨‍⚖ ИТОГ КОНСУЛЬТАЦИИ")
    assert "\nРЕЗУЛЬТАТ\n" in text
    assert "\nГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ\n" in text
    assert "Заключение юриста:" in text
    callbacks = _callbacks(markup)
    assert callbacks[0] == "consult_follow_up_start"
    assert callbacks.count("consult_follow_up_start") == 1
    assert "my_case_open" in callbacks
    assert "message_create" in callbacks
    assert "nav_home" in callbacks


@pytest.mark.asyncio
async def test_closed_case_result_does_not_offer_stale_follow_up_or_messages():
    callback = FakeCallback()
    case = SimpleNamespace(
        case_number="M2-CLOSED-17",
        status=CaseStatus.M2_CLOSED,
    )
    consultation = SimpleNamespace(
        status=ConsultationStatus.DONE,
        decision="follow_up",
        lawyer_result="Результат сохранён, но дело уже закрыто.",
        scheduled_at=None,
    )

    await _render_result(callback, case=case, consultation=consultation)

    text, markup = callback.message.edits[-1]
    assert "Это дело уже закрыто" in text
    assert _callbacks(markup) == ["nav_home"]
