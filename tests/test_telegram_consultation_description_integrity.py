from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.bot import build_dispatcher
from app.bot.screens.consultation_description import (
    _present_committed_description,
    capture_description,
    confirm_description,
    subject_start,
)
from app.bot.states import ConsultationDescriptionStates
from app.domain.consultations import consultation_service as consultation_service_module
from app.domain.consultations.consultation_service import ConsultationService
from app.domain.statuses.consultation_statuses import ConsultationStatus


class FakeState:
    def __init__(self, data=None, current=None):
        self.data = dict(data or {})
        self.current = current
        self.clear_count = 0

    async def get_data(self):
        return dict(self.data)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_state(self, state):
        self.current = getattr(state, "state", state)

    async def clear(self):
        self.data.clear()
        self.current = None
        self.clear_count += 1


class FakeMessage:
    def __init__(self, text="", edit_error: Exception | None = None):
        self.text = text
        self.edit_error = edit_error
        self.edits = []
        self.answers = []

    async def edit_text(self, text, reply_markup=None):
        if self.edit_error is not None:
            raise self.edit_error
        self.edits.append((text, reply_markup))

    async def answer(self, text, reply_markup=None):
        self.answers.append((text, reply_markup))


class FakeCallback:
    def __init__(self, data="", edit_error: Exception | None = None):
        self.data = data
        self.message = FakeMessage(edit_error=edit_error)
        self.callback_answers = []

    async def answer(self, text=None, show_alert=False):
        self.callback_answers.append((text, show_alert))


class FakeDb:
    def __init__(self):
        self.flush_count = 0

    async def flush(self):
        self.flush_count += 1


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def test_consultation_description_has_distinct_review_state():
    assert (
        ConsultationDescriptionStates.reviewing_description.state
        != ConsultationDescriptionStates.waiting_description.state
    )


@pytest.mark.asyncio
async def test_typed_description_becomes_reviewable_draft_without_db_write():
    state = FakeState(
        {
            "subject_type": "new_or_other",
            "related_case_id": None,
        },
        current=ConsultationDescriptionStates.waiting_description.state,
    )
    message = FakeMessage(
        "Застройщик нарушил срок передачи квартиры, хочу понять порядок действий."
    )

    await capture_description(message, state)

    assert state.data["description_draft"] == message.text
    assert state.current == ConsultationDescriptionStates.reviewing_description.state
    review_text, markup = message.answers[-1]
    assert "шаг 3 из 3" in review_text
    assert "Проверьте текст перед сохранением" in review_text
    assert _callbacks(markup)[:4] == [
        "consult_description_confirm",
        "consult_description_edit",
        "consult_subject_start",
        "nav_cancel",
    ]
    source = inspect.getsource(capture_description)
    assert "ConsultationIntakeService" not in source
    assert "db.commit" not in source


@pytest.mark.asyncio
async def test_committed_description_falls_back_to_new_message_when_edit_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_description(callback, booked=False)

    assert callback.message.answers
    assert "✅ Вопрос сохранён" in callback.message.answers[-1][0]
    assert callback.callback_answers[-1][0] == (
        "Вопрос сохранён. Результат открыт новым сообщением."
    )


def test_confirm_commits_before_fsm_cleanup_and_result_presentation():
    source = inspect.getsource(confirm_description)
    commit = source.index("await db.commit()")
    clear = source.index("await state.clear()", commit)
    present = source.index("await _present_committed_description(", commit)

    assert commit < clear < present
    assert "await db.rollback()" in source
    assert "reviewing_description" in source
    assert "Черновик остался на шаге проверки" in source


def test_subject_back_path_explicitly_preserves_valid_draft():
    source = inspect.getsource(subject_start)
    draft_read = source.index("draft = _valid_draft(previous)")
    restore = source.index("await _reset_to_subject_choice(state, draft=draft)")
    assert draft_read < restore
    assert "ранее введённый текст сохранён" in source


def test_dedicated_description_router_runs_before_legacy_intake_router():
    source = inspect.getsource(build_dispatcher)
    assert source.index("consultation_description.router") < source.index(
        "consultation_intake.router"
    )


@pytest.mark.asyncio
async def test_description_edit_does_not_regress_payment_pending_or_duplicate_history(
    monkeypatch,
):
    db = FakeDb()
    service = ConsultationService(db)
    service._validate_related_case = AsyncMock(return_value=None)
    history_calls = []

    async def fake_history(*args, **kwargs):
        history_calls.append((args, kwargs))

    monkeypatch.setattr(
        consultation_service_module,
        "add_case_history_event",
        fake_history,
    )
    consultation = SimpleNamespace(
        status=ConsultationStatus.PAYMENT_PENDING,
        client_description="Старый текст вопроса, который уже был сохранён.",
        subject_type="new_or_other",
        related_case_id=None,
    )
    case = SimpleNamespace(id=17)
    updated = "Обновлённый текст вопроса перед подтверждением консультации."

    await service.save_description(
        consultation=consultation,
        case=case,
        client_id=23,
        description=updated,
        subject_type="new_or_other",
        related_case_id=None,
    )

    assert consultation.status == ConsultationStatus.PAYMENT_PENDING
    assert consultation.client_description == updated
    assert len(history_calls) == 1
    assert db.flush_count == 1

    await service.save_description(
        consultation=consultation,
        case=case,
        client_id=23,
        description=updated,
        subject_type="new_or_other",
        related_case_id=None,
    )

    assert consultation.status == ConsultationStatus.PAYMENT_PENDING
    assert len(history_calls) == 1
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_description_pending_advances_once_even_when_payload_matches(monkeypatch):
    db = FakeDb()
    service = ConsultationService(db)
    service._validate_related_case = AsyncMock(return_value=None)
    history_calls = []

    async def fake_history(*args, **kwargs):
        history_calls.append((args, kwargs))

    monkeypatch.setattr(
        consultation_service_module,
        "add_case_history_event",
        fake_history,
    )
    text = "Уже введённый вопрос достаточной длины для консультации."
    consultation = SimpleNamespace(
        status=ConsultationStatus.DESCRIPTION_PENDING,
        client_description=text,
        subject_type="new_or_other",
        related_case_id=None,
    )

    await service.save_description(
        consultation=consultation,
        case=SimpleNamespace(id=91),
        client_id=7,
        description=text,
        subject_type="new_or_other",
        related_case_id=None,
    )

    assert consultation.status == ConsultationStatus.DOCUMENTS_OPTIONAL
    assert len(history_calls) == 1
    assert db.flush_count == 1
