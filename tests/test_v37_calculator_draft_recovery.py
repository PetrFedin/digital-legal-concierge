from __future__ import annotations

import inspect

from app.bot import bot as bot_module
from app.bot.calculator_draft import (
    DRAFT_MARKER,
    draft_step,
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.screens import calculator


def test_calculator_draft_resumes_from_first_incomplete_step():
    assert draft_step({}) == "price"
    assert draft_step({"contract_price": "8500000"}) == "planned_date"
    assert draft_step(
        {
            "contract_price": "8500000",
            "planned_transfer_date": "2025-01-01",
        }
    ) == "transfer_status"
    assert draft_step(
        {
            "contract_price": "8500000",
            "planned_transfer_date": "2025-01-01",
            "object_transferred": True,
        }
    ) == "actual_date"
    assert draft_step_label(
        {
            "contract_price": "8500000",
            "planned_transfer_date": "2025-01-01",
        }
    ) == "статус передачи объекта"


def test_only_meaningful_marked_calculator_data_is_offered_as_saved_draft():
    assert not has_saved_calculator_draft({DRAFT_MARKER: True})
    assert has_saved_calculator_draft(
        {DRAFT_MARKER: True, "contract_price": "8500000"}
    )


def test_global_navigation_has_calculator_draft_persistence_middleware():
    source = inspect.getsource(bot_module.build_dispatcher)
    assert source.count("CalculatorDraftNavigationMiddleware()") == 2
    assert "dispatcher.message.middleware(CalculatorDraftNavigationMiddleware())" in source
    assert "dispatcher.callback_query.middleware(CalculatorDraftNavigationMiddleware())" in source


def test_calculator_entry_offers_resume_and_destructive_restart_confirmation():
    source = inspect.getsource(calculator.calc_start)
    assert "has_saved_calculator_draft" in source
    assert "calc_resume" in source
    assert "calc_restart_confirm" in source
    confirm_source = inspect.getsource(calculator.confirm_restart_calculation)
    assert "calc_restart" in confirm_source
    assert "удалены" in confirm_source


def test_calculator_exit_copy_says_saved_not_cancelled():
    source = inspect.getsource(calculator)
    assert "💾 Сохранить и выйти" in source
