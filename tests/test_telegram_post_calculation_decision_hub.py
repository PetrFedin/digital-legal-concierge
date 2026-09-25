from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.bot.bot import build_dispatcher
from app.bot.client_case_view import (
    CLIENT_ACTIONS,
    _calculation_summary,
    effective_client_route,
)
from app.bot.screens import post_calculation
from app.bot.screens.post_calculation import decision_open
from app.domain.statuses.case_statuses import CaseStatus


class FakeMessage:
    def __init__(self):
        self.edits = []

    async def edit_text(self, text, reply_markup=None):
        self.edits.append((text, reply_markup))


class FakeCallback:
    def __init__(self):
        self.message = FakeMessage()
        self.callback_answers = []

    async def answer(self, text=None, show_alert=False):
        self.callback_answers.append((text, show_alert))


class FakeCaseService:
    def __init__(self, case):
        self.case = case

    async def get_active_case_for_user(self, _user_id):
        return self.case


class FakeContext:
    def __init__(self, case):
        self.case_service = FakeCaseService(case)

    async def get_user_from_callback(self, _callback):
        return SimpleNamespace(id=77)


def _callbacks(markup) -> list[str]:
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
    ]


def _patch_context(monkeypatch, case):
    monkeypatch.setattr(
        post_calculation,
        "BotContextService",
        lambda _db: FakeContext(case),
    )


def test_saved_calculation_and_selected_m1_have_distinct_recoverable_actions():
    assert CLIENT_ACTIONS["CALCULATED"].callback == "calc_decision_open"
    assert CLIENT_ACTIONS["CALCULATED"].label == "Выбрать дальнейший путь"
    assert CLIENT_ACTIONS["CLIENT_DECISION"].callback == "consent_open"
    assert CLIENT_ACTIONS["CLIENT_DECISION"].label == "Подтвердить согласие"


def test_legacy_client_decision_is_rendered_as_m1_without_faking_calculated_route():
    legacy_m1 = SimpleNamespace(status=CaseStatus.CLIENT_DECISION, route=None)
    undecided = SimpleNamespace(status=CaseStatus.CALCULATED, route=None)

    assert effective_client_route(legacy_m1) == "M1"
    assert effective_client_route(undecided) is None


def test_saved_calculation_remains_visible_before_route_choice():
    case = SimpleNamespace(status=CaseStatus.CALCULATED, route=None)
    calculation = SimpleNamespace(penalty_amount=125000, delay_days=42)

    summary = _calculation_summary(case, calculation)

    assert "125 000.00 ₽" in summary
    assert "42 дн." in summary
    assert "Не требуется" not in summary

    case.route = "M2"
    assert _calculation_summary(case, calculation) == "Не требуется для консультации"


@pytest.mark.asyncio
async def test_calculated_case_reopens_all_three_post_calculation_choices(monkeypatch):
    _patch_context(
        monkeypatch,
        SimpleNamespace(
            id=41,
            status=CaseStatus.CALCULATED,
            route=None,
            service_mode=None,
        ),
    )
    callback = FakeCallback()

    await decision_open(callback, db=None)

    text, markup = callback.message.edits[-1]
    assert "Что делать после расчёта" in text
    assert _callbacks(markup) == [
        "calc_continue_m1:v2:41",
        "calc_self_filing:v2:41",
        "calc_to_m2:v2:41",
        "calc_postpone:v2:41",
        "my_case_open",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_selected_m1_can_continue_to_consent_or_switch_to_consultation(monkeypatch):
    _patch_context(
        monkeypatch,
        SimpleNamespace(
            id=42,
            status=CaseStatus.CLIENT_DECISION,
            route="M1",
            service_mode="FULL_REPRESENTATION",
        ),
    )
    callback = FakeCallback()

    await decision_open(callback, db=None)

    text, markup = callback.message.edits[-1]
    assert "выбрали ведение дела" in text
    assert _callbacks(markup) == [
        "consent_open:v2:42",
        "calc_self_filing:v2:42",
        "calc_to_m2:v2:42",
        "calc_postpone:v2:42",
        "my_case_open",
        "nav_home",
    ]


@pytest.mark.asyncio
async def test_stale_decision_button_recovers_to_current_m1_case(monkeypatch):
    _patch_context(
        monkeypatch,
        SimpleNamespace(
            id=43,
            status=CaseStatus.M1_DOCUMENTS_PENDING,
            route="M1",
            service_mode="FULL_REPRESENTATION",
        ),
    )
    callback = FakeCallback()

    await decision_open(callback, db=None)

    text, markup = callback.message.edits[-1]
    assert "более раннему этапу" in text
    assert _callbacks(markup) == [
        "my_case_open",
        "documents_open",
        "nav_home",
    ]


def test_post_calculation_router_is_registered_in_dispatcher():
    source = inspect.getsource(build_dispatcher)
    assert "post_calculation.router" in source

@pytest.mark.asyncio
async def test_selected_self_filing_reopens_its_own_consent_and_can_switch_mode(monkeypatch):
    _patch_context(
        monkeypatch,
        SimpleNamespace(
            id=44,
            status=CaseStatus.CLIENT_DECISION,
            route="M1",
            service_mode="SELF_FILING_PACKAGE",
        ),
    )
    callback = FakeCallback()

    await decision_open(callback, db=None)

    text, markup = callback.message.edits[-1]
    assert "пакет для самостоятельной подачи" in text.lower()
    assert "не будет представлять вас в суде" in text.lower()
    assert _callbacks(markup) == [
        "consent_open:v2:44",
        "calc_continue_m1:v2:44",
        "calc_to_m2:v2:44",
        "calc_postpone:v2:44",
        "my_case_open",
        "nav_home",
    ]

