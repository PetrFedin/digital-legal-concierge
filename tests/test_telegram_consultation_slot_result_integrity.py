from __future__ import annotations

import inspect

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.screens.consultation_intake import (
    _safe_edit,
    _show_booked,
    booking_start,
    choose_date,
    choose_slot,
    legacy_description_start,
    subject_start,
)


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
    def __init__(self, edit_error: Exception | None = None):
        self.message = FakeMessage(edit_error=edit_error)
        self.callback_answers = []

    async def answer(self, text=None, show_alert=False):
        self.callback_answers.append((text, show_alert))


@pytest.mark.asyncio
async def test_safe_edit_falls_back_to_new_message_when_old_message_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _safe_edit(callback, "✅ Запись сохранена.", reply_markup=None)

    assert callback.message.answers == [("✅ Запись сохранена.", None)]


@pytest.mark.asyncio
async def test_safe_edit_does_not_duplicate_unchanged_message():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message is not modified",
        )
    )

    await _safe_edit(callback, "Экран уже актуален.", reply_markup=None)

    assert callback.message.answers == []
    assert callback.callback_answers[-1][0] == "Экран уже актуален."


def test_slot_reservation_commits_before_result_presentation():
    source = inspect.getsource(choose_slot)
    commit = source.index("await db.commit()")
    first_post_commit = source.index("if payments_disabled():", commit)

    assert commit < first_post_commit
    assert "await _show_booked(callback, consultation, slot)" in source[first_post_commit:]
    assert "await _safe_edit(" in source[first_post_commit:]
    assert "await callback.message.edit_text(" not in source[first_post_commit:]


def test_booked_result_uses_recoverable_presentation():
    source = inspect.getsource(_show_booked)

    assert "await _safe_edit(" in source
    assert "callback.message.edit_text" not in source
    assert "📁 Моё дело" in source
    assert "🏠 Главная" in source


def test_slot_selection_screens_after_prepare_commit_use_safe_rendering():
    booking_source = inspect.getsource(booking_start)
    date_source = inspect.getsource(choose_date)

    assert booking_source.count("await _safe_edit(") >= 2
    assert "callback.message.edit_text" not in booking_source
    assert "await _safe_edit(" in date_source


def test_post_commit_description_entry_uses_safe_rendering():
    for handler in (subject_start, legacy_description_start):
        source = inspect.getsource(handler)
        commit = source.index("await db.commit()")
        safe_edit = source.index("await _safe_edit(")
        assert commit < safe_edit
