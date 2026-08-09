from __future__ import annotations

from types import SimpleNamespace

import pytest

import app.bot.screens.m1_stages as m1_stages


@pytest.mark.asyncio
async def test_completed_m1_stale_callback_has_archive_actions_and_cannot_start_message(
    monkeypatch,
):
    completed = SimpleNamespace(case_number="M1-CLOSED-2026")
    captured: dict[str, object] = {}

    async def latest_completed(_db, *, user_id: int):
        assert user_id == 42
        return completed

    async def present(callback, text, *, reply_markup):
        captured["callback"] = callback
        captured["text"] = text
        captured["reply_markup"] = reply_markup

    monkeypatch.setattr(
        m1_stages,
        "latest_completed_m1_case_for_user",
        latest_completed,
    )
    monkeypatch.setattr(m1_stages, "_present_committed_result", present)

    callback = object()
    await m1_stages._show_missing_or_completed_stage(
        callback,
        object(),
        SimpleNamespace(id=42),
        "Активное дело не найдено",
        include_documents=True,
    )

    assert captured["callback"] is callback
    assert "M1-CLOSED-2026" in captured["text"]
    assert "старая кнопка больше не выполняет действий" in captured["text"]

    markup = captured["reply_markup"]
    callback_data = [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if button.callback_data
    ]
    assert "documents_open" in callback_data
    assert "my_case_open" in callback_data
    assert "payments_open" in callback_data
    assert "case_history_open" in callback_data
    assert "nav_home" in callback_data
    assert "message_create" not in callback_data


@pytest.mark.asyncio
async def test_no_completed_case_keeps_normal_stale_guidance(monkeypatch):
    captured: dict[str, object] = {}

    async def latest_completed(_db, *, user_id: int):
        assert user_id == 43
        return None

    async def stale(callback, text, *, include_documents=False):
        captured["callback"] = callback
        captured["text"] = text
        captured["include_documents"] = include_documents

    monkeypatch.setattr(
        m1_stages,
        "latest_completed_m1_case_for_user",
        latest_completed,
    )
    monkeypatch.setattr(m1_stages, "_show_stale_stage", stale)

    callback = object()
    await m1_stages._show_missing_or_completed_stage(
        callback,
        object(),
        SimpleNamespace(id=43),
        "Активное дело не найдено",
        include_documents=True,
    )

    assert captured == {
        "callback": callback,
        "text": "Активное дело не найдено",
        "include_documents": True,
    }


def test_all_m1_no_active_case_handlers_use_completed_archive_guard():
    source = __import__("pathlib").Path("app/bot/screens/m1_stages.py").read_text(
        encoding="utf-8"
    )

    assert source.count("await _show_missing_or_completed_stage(") >= 7
    assert "async def contract_open(callback: CallbackQuery, db):" in source
    assert "async def poa_instruction(callback: CallbackQuery, db):" in source
    assert "Фактически взыскано" in source
    assert "Success fee:" in source
