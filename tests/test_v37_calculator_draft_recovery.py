from __future__ import annotations

import asyncio
import inspect

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage

from app.bot import bot as bot_module
from app.bot import calculator_draft as calculator_draft_module
from app.bot.calculator_draft import (
    CALCULATOR_CASE_ID,
    CALCULATOR_DRAFTS_BY_CASE,
    DRAFT_MARKER,
    activate_calculator_case_draft,
    draft_step,
    draft_step_label,
    finish_calculator_case,
    has_saved_calculator_draft,
    start_fresh_calculator_case,
)
from app.bot.screens import calculator, calculator_active_case_recovery
from app.bot.states import CalculatorStates


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

    module_source = inspect.getsource(calculator_draft_module)
    for persistent_action in (
        "🧮 Рассчитать неустойку",
        "📁 Мое дело",
        "📁 Моё дело",
        "📄 Документы",
        "💬 Связаться с юристом",
    ):
        assert persistent_action in module_source


def test_existing_case_recovery_is_explicit_and_distinct_from_global_new_calculation():
    recovery = inspect.getsource(calculator_active_case_recovery)
    global_start = inspect.getsource(calculator.calc_start)

    assert '"calc_recover"' in recovery
    assert 'action="calc_recover"' in recovery
    assert "select_case_for_user" in recovery
    assert "activate_calculator_case_draft" in recovery
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


def test_delayed_start_replay_cannot_steal_selected_case_or_flat_draft():
    source = inspect.getsource(calculator.calc_start)

    assert "get_selected_case_for_user" in source
    assert "selected_case_id != case_id" in source
    assert "Текущее выбранное дело и незавершённый расчёт не изменены" in source
    replay_guard = source.split("if selected_case_id != case_id:", 1)[1].split(
        "if previous_case_id == case_id:", 1
    )[0]
    assert "_start_fresh" not in replay_guard
    assert "state.clear" not in replay_guard


def test_restart_confirmation_returns_to_same_case_bound_draft_not_global_new_case():
    confirm_source = inspect.getsource(calculator.confirm_restart_calculation)

    assert "calc_restart" in confirm_source
    assert "удалены" in confirm_source
    assert 'bound_case_callback("calc_resume", case_id)' in confirm_source
    assert 'bound_case_callback("calc_restart", case_id)' in confirm_source
    assert '("↩️ Вернуться к черновику", "calc_resume")' not in confirm_source
    assert '("↩️ Вернуться к черновику", "calc_start")' not in confirm_source


def test_calculator_exit_copy_says_saved_not_cancelled():
    source = inspect.getsource(calculator)
    assert "💾 Сохранить и выйти" in source


def test_two_unfinished_case_drafts_round_trip_without_cross_case_loss():
    async def scenario() -> None:
        storage = MemoryStorage()
        state = FSMContext(
            storage=storage,
            key=StorageKey(bot_id=1, chat_id=10, user_id=10),
        )
        try:
            # Case A is partially completed.
            await state.set_data(
                {
                    CALCULATOR_CASE_ID: 101,
                    "contract_price": "8100000",
                }
            )
            await state.set_state(CalculatorStates.waiting_planned_transfer_date)

            # Explicitly start Case B. A must move into the namespaced draft map,
            # while B becomes the only flat active calculator working set.
            await start_fresh_calculator_case(state, case_id=202)
            data = await state.get_data()
            assert int(data[CALCULATOR_CASE_ID]) == 202
            assert "contract_price" not in data
            assert data[CALCULATOR_DRAFTS_BY_CASE]["101"]["contract_price"] == "8100000"

            # B also becomes partially completed.
            await state.update_data(contract_price="9200000")
            await state.set_state(CalculatorStates.waiting_planned_transfer_date)

            # Recover A: B is snapshotted first; A returns with its own answer.
            restored_a = await activate_calculator_case_draft(state, case_id=101)
            assert int(restored_a[CALCULATOR_CASE_ID]) == 101
            assert restored_a["contract_price"] == "8100000"
            assert (
                restored_a[CALCULATOR_DRAFTS_BY_CASE]["202"]["contract_price"]
                == "9200000"
            )

            # Switch back to B and verify neither answer crossed Cases.
            restored_b = await activate_calculator_case_draft(state, case_id=202)
            assert int(restored_b[CALCULATOR_CASE_ID]) == 202
            assert restored_b["contract_price"] == "9200000"
            assert (
                restored_b[CALCULATOR_DRAFTS_BY_CASE]["101"]["contract_price"]
                == "8100000"
            )

            # Completing B removes only B. A remains recoverable.
            await finish_calculator_case(state, case_id=202)
            after_finish = await state.get_data()
            assert CALCULATOR_CASE_ID not in after_finish
            assert "202" not in after_finish[CALCULATOR_DRAFTS_BY_CASE]
            assert (
                after_finish[CALCULATOR_DRAFTS_BY_CASE]["101"]["contract_price"]
                == "8100000"
            )

            restored_a_again = await activate_calculator_case_draft(state, case_id=101)
            assert int(restored_a_again[CALCULATOR_CASE_ID]) == 101
            assert restored_a_again["contract_price"] == "8100000"
        finally:
            await storage.close()

    asyncio.run(scenario())