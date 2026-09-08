from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_old_exact_payment_button_cannot_act_on_non_selected_live_case():
    source = read("app/bot/screens/payment_archive_guard.py")

    assert "async def _require_selected_active_payment_case" in source
    guard = source.split("async def _require_selected_active_payment_case", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "payments_open")', 1
    )[0]
    assert "if _case_is_completed(case):" in guard
    assert "get_active_case_for_user" in guard
    assert "int(selected.id) == int(case.id)" in guard
    assert "Ссылка не открыта, платёж не сверялся и финансовый статус не изменён" in guard
    assert '("📁 Выбрать обращение", "my_cases_open")' in guard

    open_handler = source.split("async def guard_archived_payment_open", 1)[1].split(
        "async def guard_archived_fake_success", 1
    )[0]
    assert open_handler.index("_require_selected_active_payment_case") < open_handler.index(
        "_reconcile_m2_payment_view"
    )
    assert open_handler.index("_require_selected_active_payment_case") < open_handler.index(
        "create_payment_link"
    )


def test_fake_payment_button_uses_same_selected_case_guard_before_mutation():
    source = read("app/bot/screens/payment_archive_guard.py")
    handler = source.split("async def guard_archived_fake_success", 1)[1].split(
        "async def guard_success_fee_stage", 1
    )[0]

    assert handler.index("_require_selected_active_payment_case") < handler.index(
        "_reconcile_m2_payment_view"
    )
    assert handler.index("_require_selected_active_payment_case") < handler.index(
        "payment_screen.fake"
    )


def test_legacy_raw_consult_pay_is_case_scoped_before_payment_reconciliation():
    source = read("app/bot/screens/payment_archive_guard.py")
    handler = source.split("async def legacy_consult_pay_is_navigation", 1)[1].split(
        "async def guard_archived_payment_open", 1
    )[0]

    assert "resolve_case_callback_scope(" in handler
    assert 'action="consult_pay"' in handler
    assert "allow_legacy_message_case_context=True" in handler
    assert handler.index("resolve_case_callback_scope(") < handler.index(
        "guard_active_m2_payment_list"
    )


def test_payment_hold_expiry_recovery_keeps_case_number_and_bound_booking_action():
    source = read("app/bot/screens/payment_archive_guard.py")

    helper = source.split("async def _render_hold_lost", 1)[1].split(
        "async def _require_selected_active_payment_case", 1
    )[0]
    assert "case_id: int" in helper
    assert "case_number: str" in helper
    assert "Обращение № {case_number}" in helper
    assert 'bound_case_callback("consult_booking_start", case_id)' in helper

    stale_keyboard = source.split("def _m2_stale_link_keyboard", 1)[1].split(
        "async def _render_m2_reconciliation_failure", 1
    )[0]
    assert 'bound_case_callback("consult_booking_start", case_id)' in stale_keyboard
