from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest

from app.bot.bot import build_dispatcher
from app.bot.screens import consent_flow
from app.bot.screens.consent_flow import (
    _present_committed_accept,
    consent_accept,
    consent_decline,
    consent_decline_confirm,
    consent_open,
)
from app.domain.statuses.case_statuses import CaseStatus


class FakeDb:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class FakeCaseService:
    def __init__(self, case):
        self.case = case
        self.transitions = []

    async def get_active_case_for_user(self, _user_id):
        return self.case

    async def change_status(self, **kwargs):
        self.transitions.append(kwargs)
        self.case.status = kwargs["next_status"]
        if str(kwargs["next_status"]).startswith("M1_"):
            self.case.route = "M1"
        return self.case


class FakeContext:
    def __init__(self, case):
        self.case_service = FakeCaseService(case)

    async def get_user_from_callback(self, _callback):
        return SimpleNamespace(id=77)


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
        self.data = None
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


def _patch_context(monkeypatch, case):
    fake = FakeContext(case)
    monkeypatch.setattr(consent_flow, "BotContextService", lambda _db: fake)
    return fake


@pytest.mark.asyncio
async def test_calculated_case_does_not_skip_route_choice_when_old_consent_button_opens(
    monkeypatch,
):
    case = SimpleNamespace(status=CaseStatus.CALCULATED, route=None)
    ctx = _patch_context(monkeypatch, case)
    callback = FakeCallback()

    await consent_open(callback, FakeDb())

    text, markup = callback.message.edits[-1]
    assert "Сначала выберите дальнейший путь" in text
    assert _callbacks(markup)[0] == "calc_decision_open"
    assert ctx.case_service.transitions == []


@pytest.mark.asyncio
async def test_client_decision_opens_explicit_consent_and_safe_back_path(monkeypatch):
    case = SimpleNamespace(status=CaseStatus.CLIENT_DECISION, route=None)
    _patch_context(monkeypatch, case)
    callback = FakeCallback()

    await consent_open(callback, FakeDb())

    text, markup = callback.message.edits[-1]
    assert "Согласие на обработку персональных данных" in text
    assert _callbacks(markup) == [
        "consent_accept",
        "consent_decline",
        "calc_decision_open",
        "my_case_open",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_accept_without_active_case_never_creates_case(monkeypatch):
    ctx = _patch_context(monkeypatch, None)
    db = FakeDb()
    callback = FakeCallback()

    await consent_accept(callback, db)

    assert ctx.case_service.transitions == []
    assert db.commits == 0
    assert "не создаётся" in callback.message.edits[-1][0]
    source = inspect.getsource(consent_accept)
    assert "get_or_create_active_case_for_user" not in source


@pytest.mark.asyncio
async def test_accept_commits_m1_documents_transition_before_result(monkeypatch):
    case = SimpleNamespace(status=CaseStatus.CLIENT_DECISION, route=None)
    ctx = _patch_context(monkeypatch, case)
    db = FakeDb()
    callback = FakeCallback()

    await consent_accept(callback, db)

    assert len(ctx.case_service.transitions) == 1
    transition = ctx.case_service.transitions[0]
    assert transition["next_status"] == CaseStatus.M1_DOCUMENTS_PENDING
    assert transition["actor_type"] == "client"
    assert db.commits == 1
    assert db.rollbacks == 0
    assert "✅ Согласие сохранено" in callback.message.edits[-1][0]
    assert _callbacks(callback.message.edits[-1][1])[0] == "documents_open"


@pytest.mark.asyncio
async def test_old_calculated_accept_is_explicit_legacy_m1_consent_and_still_safe(
    monkeypatch,
):
    case = SimpleNamespace(status=CaseStatus.CALCULATED, route=None)
    ctx = _patch_context(monkeypatch, case)
    db = FakeDb()
    callback = FakeCallback()

    await consent_accept(callback, db)

    assert ctx.case_service.transitions[0]["next_status"] == CaseStatus.M1_DOCUMENTS_PENDING
    assert db.commits == 1
    assert case.route == "M1"


@pytest.mark.asyncio
async def test_decline_first_click_is_confirmation_only(monkeypatch):
    case = SimpleNamespace(status=CaseStatus.CLIENT_DECISION, route=None)
    ctx = _patch_context(monkeypatch, case)
    db = FakeDb()
    callback = FakeCallback()

    await consent_decline(callback, db)

    text, markup = callback.message.edits[-1]
    assert "Подтвердите отказ" in text
    assert _callbacks(markup)[0] == "consent_decline_confirm"
    assert ctx.case_service.transitions == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_confirmed_decline_returns_to_saved_calculation(monkeypatch):
    case = SimpleNamespace(status=CaseStatus.CLIENT_DECISION, route=None)
    ctx = _patch_context(monkeypatch, case)
    db = FakeDb()
    callback = FakeCallback()

    await consent_decline_confirm(callback, db)

    assert ctx.case_service.transitions[0]["next_status"] == CaseStatus.CALCULATED
    assert db.commits == 1
    text, markup = callback.message.edits[-1]
    assert "Согласие не предоставлено" in text
    assert _callbacks(markup)[:2] == ["calc_decision_open", "calc_to_m2"]


@pytest.mark.asyncio
async def test_stale_decline_cannot_roll_back_started_m1_case(monkeypatch):
    case = SimpleNamespace(status=CaseStatus.M1_DOCUMENTS_PENDING, route="M1")
    ctx = _patch_context(monkeypatch, case)
    db = FakeDb()
    callback = FakeCallback()

    await consent_decline_confirm(callback, db)

    assert ctx.case_service.transitions == []
    assert db.commits == 0
    text, markup = callback.message.edits[-1]
    assert "Автоматический откат не выполнен" in text
    assert _callbacks(markup)[0] == "message_create"


@pytest.mark.asyncio
async def test_committed_accept_falls_back_to_new_message_when_old_one_is_stale():
    callback = FakeCallback(
        edit_error=TelegramBadRequest(
            method=None,
            message="Bad Request: message can't be edited",
        )
    )

    await _present_committed_accept(callback)

    assert callback.message.answers
    assert "✅ Согласие сохранено" in callback.message.answers[-1][0]
    assert callback.callback_answers[-1][0] == (
        "Согласие сохранено. Результат открыт новым сообщением."
    )


def test_mutating_consent_handlers_commit_before_result_presentation():
    accept_source = inspect.getsource(consent_accept)
    decline_source = inspect.getsource(consent_decline_confirm)

    assert accept_source.index("await db.commit()") < accept_source.index(
        "await _present_committed_accept(callback)"
    )
    assert decline_source.index("await db.commit()") < decline_source.index(
        "await _present_committed_decline(callback)"
    )
    assert "await db.rollback()" in accept_source
    assert "await db.rollback()" in decline_source


def test_guarded_consent_router_runs_before_legacy_m1_handlers():
    source = inspect.getsource(build_dispatcher)
    assert source.index("consent_flow.router") < source.index("m1_stages.router")
