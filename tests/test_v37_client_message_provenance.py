from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_message_entry_resolves_exact_case_before_form_opens():
    source = read("app/bot/client_message_provenance.py")

    assert '"message_create"' in source
    assert "callback_matches_action" in source
    assert "resolve_case_callback_scope(" in source
    assert "allow_legacy_message_case_context=True" in source
    assert "client_message_case_id=int(case.id)" in source


def test_delayed_message_cannot_be_written_into_another_active_case():
    source = read("app/bot/client_message_provenance.py")

    validation = source.split("async def _validate_snapshot", 1)[1].split(
        "async def _clear_provenance_if_flow_finished", 1
    )[0]
    assert 'snapshot.get("client_message_case_id")' in validation
    assert "int(current_case.id) == expected_case_id" in validation
    assert "await db.rollback()" in validation
    assert "_preserve_text_after_target_change(" in validation
    assert "return False" in validation


def test_case_switch_preserves_draft_and_requires_explicit_retarget():
    source = read("app/bot/client_message_provenance.py")

    recovery = source.split("async def _render_preserved_draft", 1)[1].split(
        "async def _preserve_text_after_target_change", 1
    )[0]
    assert "Он НЕ отправлен" in recovery
    assert 'f"message_retarget_current:v2:{int(current_case.id)}"' in recovery
    assert '"my_cases_open"' in recovery
    assert '"message_discard_confirm"' in recovery


def test_message_flow_provenance_survives_review_and_clears_outside_flow():
    source = read("app/bot/client_message_provenance.py")

    assert "def _message_flow_states" in source
    clear = source.split("async def _clear_provenance_if_flow_finished", 1)[1].split(
        "class ClientMessageProvenanceMiddleware", 1
    )[0]
    assert "current_state not in _message_flow_states()" in clear
    assert "client_message_case_id=None" in clear

    message_branch = source.split("if not isinstance(event, Message)", 1)[1]
    assert "current_state not in _message_flow_states()" in message_branch
    assert "await _clear_provenance_if_flow_finished(state)" in message_branch


def test_old_workflow_callback_without_case_provenance_fails_closed():
    source = read("app/bot/client_message_provenance.py")

    validation = source.split("async def _validate_snapshot", 1)[1].split(
        "async def _clear_provenance_if_flow_finished", 1
    )[0]
    assert "Этот старый шаг формы больше не содержит точного контекста обращения" in validation
    assert "valid = False" in validation


def test_message_provenance_is_mounted_for_callback_and_message_boundaries():
    bot = read("app/bot/bot.py")

    assert "ClientMessageProvenanceMiddleware" in bot
    assert "dispatcher.message.middleware(ClientMessageProvenanceMiddleware())" in bot
    assert "dispatcher.callback_query.middleware(ClientMessageProvenanceMiddleware())" in bot
    assert bot.index("ClientMessageProvenanceMiddleware())") < bot.index("for router in [")
