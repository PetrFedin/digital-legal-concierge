from __future__ import annotations

import inspect

from app.bot import bot as bot_module
from app.bot.calculator_draft import (
    DRAFT_MARKER,
    draft_step,
    draft_step_label,
    has_saved_calculator_draft,
)
from app.bot.screens import calculator, calculator_active_case_recovery


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


def test_existing_case_recovery_is_explicit_and_distinct_from_global_new_calculation():
    recovery = inspect.getsource(calculator_active_case_recovery)
    global_start = inspect.getsource(calculator.calc_start)

    assert '"calc_recover"' in recovery
    assert 'action="calc_recover"' in recovery
    assert "select_case_for_user" in recovery
    assert "never create a replacement Case" in recovery

    assert "create_case_from_callback" in global_start
    assert 'purpose="calculator_start"' in global_start
    assert "previous_case_id" in global_start
    assert "explicit new operation/new Case" in global_start
    assert "select_case_for_user" not in global_start


def test_duplicate_same_start_callback_preserves_its_current_draft():
    source = inspect.getsource(calculator.calc_start)

    assert "if previous_case_id == case_id:" in source
    assert "has_saved_calculator_draft(data)" in source
    assert "await _show_saved_draft(callback, state)" in source
    assert "await _resume_draft(callback, state)" in source


def test_restart_confirmation_returns_to_same_draft_not_global_new_case():
    confirm_source = inspect.getsource(calculator.confirm_restart_calculation)

    assert "calc_restart" in confirm_source
    assert "удалены" in confirm_source
    assert '("↩️ Вернуться к черновику", "calc_resume")' in confirm_source
    assert '("↩️ Вернуться к черновику", "calc_start")' not in confirm_source


def test_calculator_exit_copy_says_saved_not_cancelled():
    source = inspect.getsource(calculator)
    assert "💾 Сохранить и выйти" in source
