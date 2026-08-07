from __future__ import annotations

import inspect

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens.documents import (
    _present_committed_result,
    choose,
    finish,
    skip,
    upload_menu,
)
from app.bot.states import DocumentUploadStates


class FakeState:
    def __init__(self, data=None, current=None):
        self.data = dict(data or {})
        self.current = current
        self.clear_count = 0

    async def clear(self):
        self.data.clear()
        self.current = None
        self.clear_count += 1

    async def set_state(self, state):
        self.current = getattr(state, "state", state)

    async def update_data(self, **kwargs):
        self.data.update(kwargs)


class FakeMessage:
    def __init__(self, edit_error: Exception | None = None):
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
    def __init__(self, data: str = "", edit_error: Exception | None = None):
        self.data = data
        self.message = FakeMessage(edit_error=edit_error)
        self.callback_answers = []

    async def answer(self, text=None, show_alert=False):
        self.callback_answers.append((text, show_alert))


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def test_document_upload_has_distinct_type_choice_and_file_states():
    assert DocumentUploadStates.choosing_type.state != DocumentUploadStates.waiting_file.state

    source = inspect.getsource(upload_menu)
    clear = source.index("await state.clear()")
    chooser = source.index("await state.set_state(DocumentUploadStates.choosing_type)")
    assert clear < chooser

    choose_source = inspect.getsource(choose)
    data = choose_source.index("await state.update_data(document_type=document_type)")
    waiting = choose_source.index("await state.set_state(DocumentUploadStates.waiting_file)")
    assert data < waiting


@pytest.mark.asyncio
async def test_stale_unknown_document_type_never_enters_file_upload_state():
    state = FakeState(
        {"document_type": "DDU"},
        current=DocumentUploadStates.waiting_file.state,
    )
    callback = FakeCallback("doc_type:REMOVED_TYPE")

    await choose(callback, state)

    assert state.clear_count == 1
    assert state.data == {}
    assert state.current == DocumentUploadStates.choosing_type.state
    assert "больше недоступен" in callback.message.edits[-1][0]
    assert _callbacks(callback.message.edits[-1][1]) == [
        "documents_upload_open",
        "documents_open",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_valid_document_type_moves_to_waiting_file_only_after_choice():
    state = FakeState(current=DocumentUploadStates.choosing_type.state)
    callback = FakeCallback("doc_type:DDU")

    await choose(callback, state)

    assert state.data == {"document_type": "DDU"}
    assert state.current == DocumentUploadStates.waiting_file.state
    assert "Прикрепите PDF" in callback.message.edits[-1][0]


@pytest.mark.asyncio
async def test_committed_result_falls_back_to_new_message_when_edit_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_result(
        callback,
        "✅ Изменение уже сохранено.",
        reply_markup=None,
        saved_notice="Изменение сохранено.",
    )

    assert callback.message.answers == [("✅ Изменение уже сохранено.", None)]
    assert callback.callback_answers[-1][0] == "Изменение сохранено. Результат открыт новым сообщением."


def test_finish_commits_before_rendering_durable_result():
    source = inspect.getsource(finish)
    commit = source.index("await db.commit()")
    present = source.index("await _present_committed_result(")

    assert commit < present
    assert "_present_committed_result" not in source[:commit]
    assert "Document review submission failed" in source
    assert "Документы временно не переданы. Загруженные файлы сохранены." in source


def test_skip_commits_before_rendering_durable_result():
    source = inspect.getsource(skip)
    commit = source.index("await db.commit()")
    present = source.index("await _present_committed_result(")

    assert commit < present
    assert "Переход не выполнен" in source
    assert "Переход уже сохранён" in source
