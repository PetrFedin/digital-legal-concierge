from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.bot.bot import build_dispatcher
from app.bot.screens import consent_decision_guard, consent_flow
from app.bot.screens.consent_decision_guard import (
    _bound,
    _v3_binding,
    guarded_consent_accept,
    guarded_consent_decline,
    guarded_consent_decline_prompt,
    guarded_consent_open,
    legacy_unbound_consent_refresh,
)
from app.domain.cases.consent_contract import CONSENT_CALLBACK_TOKEN, CONSENT_VERSION
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

    async def get_active_case_for_user(self, _user_id):
        return self.case

    async def get_case_for_user(self, *, user_id, case_id):
        if self.case is None:
            return None
        if int(self.case.id) != int(case_id):
            return None
        return self.case


class FakeContext:
    def __init__(self, case):
        self.case_service = FakeCaseService(case)

    async def get_user_from_callback(self, _callback):
        return SimpleNamespace(id=77)


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self, *, data=None):
        self.data = data
        self.message = FakeMessage()
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
    monkeypatch.setattr(consent_decision_guard, "BotContextService", lambda _db: fake)
    return fake


@pytest.mark.asyncio
async def test_calculated_case_old_unbound_consent_is_navigation_only(monkeypatch):
    case = SimpleNamespace(id=88, status=CaseStatus.CALCULATED, route=None)
    _patch_context(monkeypatch, case)
    callback = FakeCallback(data="consent_accept")
    db = FakeDb()

    await legacy_unbound_consent_refresh(callback, db)

    text, markup = callback.message.edits[-1]
    assert "Сначала выберите дальнейший путь" in text
    assert _callbacks(markup)[0] == "calc_decision_open"
    assert db.commits == 0
    assert callback.callback_answers[-1][0] == "Открываем актуальный текст согласия."


@pytest.mark.asyncio
async def test_client_decision_opens_exact_version_bound_consent(monkeypatch):
    case = SimpleNamespace(id=89, status=CaseStatus.CLIENT_DECISION, route=None)
    _patch_context(monkeypatch, case)
    callback = FakeCallback(data="consent_open")

    await guarded_consent_open(callback, FakeDb())

    text, markup = callback.message.edits[-1]
    callbacks = _callbacks(markup)
    assert "Согласие на обработку персональных данных" in text
    assert f"Версия текста: {CONSENT_VERSION}" in text
    assert callbacks[0] == _bound("consent_accept", case.id)
    assert callbacks[1] == _bound("consent_decline", case.id)
    assert callbacks[-2:] == ["my_case_open", "nav_home"]
    assert CONSENT_CALLBACK_TOKEN in callbacks[0]


def test_v3_binding_rejects_unbound_or_malformed_legal_decision():
    exact = _bound("consent_accept", 91)
    assert _v3_binding(SimpleNamespace(data=exact), "consent_accept") == (
        91,
        CONSENT_CALLBACK_TOKEN,
    )
    assert _v3_binding(SimpleNamespace(data="consent_accept"), "consent_accept") is None
    assert _v3_binding(SimpleNamespace(data="consent_accept:v3:0:bad"), "consent_accept") is None
    assert _v3_binding(SimpleNamespace(data="consent_accept:v3:91:"), "consent_accept") is None


@pytest.mark.asyncio
async def test_decline_first_bound_click_is_confirmation_only(monkeypatch):
    case = SimpleNamespace(id=92, status=CaseStatus.CLIENT_DECISION, route=None)
    _patch_context(monkeypatch, case)
    callback = FakeCallback(data=_bound("consent_decline", case.id))
    db = FakeDb()

    await guarded_consent_decline_prompt(callback, db)

    text, markup = callback.message.edits[-1]
    assert "Подтвердите отказ" in text
    assert _callbacks(markup)[0] == _bound("consent_decline_confirm", case.id)
    assert db.commits == 0


@pytest.mark.asyncio
async def test_stale_decline_prompt_cannot_roll_back_started_m1_case(monkeypatch):
    case = SimpleNamespace(id=93, status=CaseStatus.M1_DOCUMENTS_PENDING, route="M1")
    _patch_context(monkeypatch, case)
    callback = FakeCallback(data=_bound("consent_decline", case.id))
    db = FakeDb()

    await guarded_consent_decline_prompt(callback, db)

    assert db.commits == 0
    assert db.rollbacks == 1
    text, markup = callback.message.edits[-1]
    assert "Старая кнопка ничего не изменила" in text
    assert "message_create" in _callbacks(markup)


def test_mutating_consent_handlers_use_domain_service_and_commit_before_success_view():
    accept_source = inspect.getsource(guarded_consent_accept)
    decline_source = inspect.getsource(guarded_consent_decline)

    for source in (accept_source, decline_source):
        assert "ConsentDecisionService(db).apply" in source
        assert "await db.commit()" in source
        assert "await db.rollback()" in source

    accept_success = accept_source.split("await db.commit()", 1)[1]
    decline_success = decline_source.split("await db.commit()", 1)[1]
    assert "✅ Согласие сохранено" in accept_success
    assert "Согласие не предоставлено" in decline_success


def test_retired_compatibility_module_cannot_mutate_legal_consent():
    source = inspect.getsource(consent_flow)

    assert "Retired compatibility module" in source
    assert "ConsentDecisionService" not in source
    assert "change_status" not in source
    assert "commit()" not in source
    assert '__all__ = ["router"]' in source


def test_guarded_consent_router_runs_before_compatibility_handlers():
    source = inspect.getsource(build_dispatcher)
    assert source.index("consent_decision_guard.router") < source.index(
        "consent_stale_guard.router"
    )
    assert source.index("consent_decision_guard.router") < source.index(
        "consent_flow.router"
    )
