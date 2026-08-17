from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_navigation_history_guard_owns_global_home_cancel_and_back():
    guard = read("app/bot/screens/navigation_history_guard.py")
    bot = read("app/bot/bot.py")

    assert 'c.data == "nav_home"' in guard
    assert 'c.data == "nav_cancel"' in guard
    assert 'c.data == "nav_back"' in guard
    assert "navigation_history_guard.router" in bot
    assert bot.index("navigation_history_guard.router") < bot.index("common.router")


def test_back_replays_only_explicit_read_only_screens():
    guard = read("app/bot/screens/navigation_history_guard.py")
    replay_block = guard.split("_REPLAY_SAFE = frozenset(", 1)[1].split(")\n\n\ndef _clean_history", 1)[0]

    for callback in (
        "nav_home",
        "my_case_open",
        "documents_open",
        "documents_list_open",
        "documents_history_open",
        "payments_open",
        "case_history_open",
        "message_history",
        "consultation_result_open",
        "contract_open",
    ):
        assert f'"{callback}"' in replay_block

    for mutating_callback in (
        "pay_start_30000",
        "pay_court_70000",
        "pay_success_fee",
        "consult_pay",
        "consult_booking_start",
        "contract_confirm",
        "doc_finish_upload",
        "consult_reschedule_confirm",
    ):
        assert f'"{mutating_callback}"' not in replay_block

    # consultation_booked_open intentionally stays with the terminal-result
    # filter/normal action-center router so Back history cannot shadow its
    # stale/terminal compatibility behavior.
    assert '"consultation_booked_open"' not in replay_block


def test_back_falls_back_to_my_case_then_home_without_business_write():
    guard = read("app/bot/screens/navigation_history_guard.py")
    block = guard.split("async def logical_back", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "my_case_open")', 1
    )[0]

    assert "await _pop(state)" in block
    assert "await _render_target(" in block
    assert 'current == "my_case_open"' in block
    assert "await common.home(callback, db, state)" in block
    assert "await my_case.my_case(callback, db)" in block
    assert "change_status" not in block
    assert "get_or_create_payment" not in block
    assert "reserve" not in block


def test_unsent_message_draft_blocks_back_and_navigation_history_recording():
    guard = read("app/bot/screens/navigation_history_guard.py")

    assert "common._guard_callback_draft(callback, state)" in guard
    assert "common._has_unsent_message_draft(state)" in guard


def test_calculator_draft_restore_keeps_post_navigation_breadcrumbs():
    draft = read("app/bot/calculator_draft.py")

    assert '_NAV_DATA_PREFIX = "_client_nav_"' in draft
    assert "post_navigation_data = dict(await state.get_data())" in draft
    assert "startswith(_NAV_DATA_PREFIX)" in draft
    assert "current_data[key] = value" in draft


def test_contract_presentation_adds_visible_back_without_changing_contract_transition():
    patch = read("app/bot/client_wording_patch.py")
    contract = read("app/bot/screens/service_contract.py")

    assert 'back_button = ("⬅️ Назад", "nav_back")' in patch
    assert "service_contract._show = contract_show_with_back" in patch
    assert "confirm_service_contract(" in contract
    assert "contract_show_with_back" not in contract


def test_active_my_case_keeps_primary_action_first_and_adds_back_before_home():
    patch = read("app/bot/client_wording_patch.py")

    assert "original_case_buttons = my_case._case_buttons" in patch
    assert "items = list(original_case_buttons(view))" in patch
    assert 'back_button = ("⬅️ Назад", "nav_back")' in patch
    assert 'home_index = callbacks.index("nav_home")' in patch
    assert "items.insert(home_index, back_button)" in patch
    assert "my_case._case_buttons = case_buttons_with_back" in patch
