from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_my_case_binds_m1_contextual_primary_actions():
    scope = read("app/bot/case_callback_scope.py")

    for action in ("contract_open", "poa_instruction", "court_status"):
        assert f'"{action}"' in scope
    assert "if clean_action in CASE_BOUND_MUTATING_ACTIONS" in scope
    assert "return bound_case_callback(clean_action, case_id)" in scope


def test_contract_entry_accepts_bound_case_and_fails_closed_for_stale_legacy_context():
    source = read("app/bot/screens/service_contract.py")

    entry = source.split("async def open_service_contract", 1)[1].split(
        "async def legacy_contract_confirmation", 1
    )[0]
    assert 'callback_matches_action(c.data, "contract_open")' in source
    assert 'action="contract_open"' in entry
    assert "allow_legacy_message_case_context=True" in entry
    assert 'bound_case_callback("contract_open", case_id)' in entry
    assert 'bound_case_callback("message_create", case_id)' in entry


def test_legacy_generic_contract_confirmation_cannot_confirm_or_create_payment():
    source = read("app/bot/screens/service_contract.py")

    handler = source.split("async def legacy_contract_confirmation", 1)[1].split(
        "async def confirm_exact_service_contract", 1
    )[0]
    assert 'action="contract_sign"' in handler
    assert "allow_legacy_message_case_context=True" in handler
    assert "Подтверждение не выполнено" in handler
    assert 'bound_case_callback("contract_open", case_id)' in handler
    assert "confirm_service_contract(" not in handler


def test_poa_and_court_contextual_screens_are_guarded_before_legacy_renderers():
    source = read("app/bot/screens/m1_legal_stages.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "poa_instruction")' in source
    assert 'callback_matches_action(c.data, "court_status")' in source
    assert "resolve_case_callback_scope(" in source
    assert "allow_legacy_message_case_context=True" in source
    assert "renderer = getattr(m1_stages, renderer_name)" in source
    assert bot.index("m1_legal_stages.router") < bot.index("m1_stages.router")


def test_legacy_message_with_visible_other_case_fails_closed_even_if_one_case_active():
    source = read("app/bot/case_callback_scope.py")

    assert "def _legacy_message_names_other_case" in source
    assert "legacy_message_conflict" in source
    assert "Эта старая кнопка относится к другому обращению" in source
    assert "старый экран не может быть перенесён в новый контекст автоматически" in source
