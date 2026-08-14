from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_message_entry_records_exact_active_case_snapshot():
    source = read("app/bot/client_message_provenance.py")

    assert '"message_create"' in source
    assert "get_active_case_for_user(user.id)" in source
    assert "client_message_case_id=int(case.id)" in source


def test_delayed_message_cannot_be_written_into_another_active_case():
    source = read("app/bot/client_message_provenance.py")

    block = source.split("if not isinstance(event, Message)", 1)[1]
    assert 'snapshot.get("client_message_case_id")' in block
    assert "int(case.id) != expected_case_id" in block
    assert "await db.rollback()" in block
    assert "Текст не был записан в другое обращение" in block
    assert "return None" in block


def test_message_recovery_clears_old_fsm_and_routes_to_current_case():
    source = read("app/bot/client_message_provenance.py")

    recovery = source.split("async def _recover", 1)[1].split(
        "class ClientMessageProvenanceMiddleware", 1
    )[0]
    assert "await state.clear()" in recovery
    assert '"message_create"' in recovery
    assert '"my_case_open"' in recovery


def test_message_provenance_is_mounted_for_callback_and_message_boundaries():
    bot = read("app/bot/bot.py")

    assert "ClientMessageProvenanceMiddleware" in bot
    assert "dispatcher.message.middleware(ClientMessageProvenanceMiddleware())" in bot
    assert "dispatcher.callback_query.middleware(ClientMessageProvenanceMiddleware())" in bot
    assert bot.index("ClientMessageProvenanceMiddleware())") < bot.index("for router in [")
