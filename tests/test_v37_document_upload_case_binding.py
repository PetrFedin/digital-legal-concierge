from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_upload_chooser_emits_case_bound_document_type_callbacks():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    assert 'return f"doc_type:v2:{int(case_id)}:{document_type}"' in guard
    assert "document_case_id=int(case.id)" in guard
    assert "DocumentUploadStates.choosing_type" in guard
    assert "DocumentUploadStates.waiting_file" in guard
    assert "expected_case_id" in guard
    assert "int(case.id) != int(expected_case_id)" in guard


def test_legacy_raw_document_type_callbacks_only_refresh_bound_chooser():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    handler = guard.split("async def bound_document_type_choice", 1)[1]
    legacy = handler.split("if parsed is None:", 1)[1].split(
        "expected_case_id, document_type = parsed", 1
    )[0]
    assert "_render_bound_chooser" in legacy
    assert "waiting_file" not in legacy
    assert "document_type=" not in legacy


def test_file_message_is_rejected_before_legacy_processing_if_case_binding_changed():
    middleware = read("app/bot/document_replacement_protection.py")

    assert 'state_data.get("document_case_id")' in middleware
    assert "expected_case_id != int(case.id)" in middleware
    block = middleware.split("if is_file_message:", 1)[1].split(
        "return await handler(event, data)", 1
    )[0]
    assert "await _stage_recovery(event, state)" in block
    assert "has_replacement_marker" in block


def test_direct_replacement_keeps_stricter_document_version_snapshot_path():
    middleware = read("app/bot/document_replacement_protection.py")

    assert '"replacement_document_id" in state_data' in middleware
    assert '"replacement_expected_version" in state_data' in middleware
    assert "replacement_snapshot_matches(" in middleware
    assert "int(latest.id) == int(document.id)" in middleware


def test_upload_binding_guard_precedes_mutation_center_and_legacy_documents():
    bot = read("app/bot/bot.py")

    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "document_mutation_guard.router,"
    )
    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "document_action_center.router,"
    )
    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "documents.router,"
    )
