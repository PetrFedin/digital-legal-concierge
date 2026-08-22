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


def test_file_message_for_another_selected_case_preserves_exact_upload_draft():
    middleware = read("app/bot/document_replacement_protection.py")

    assert "async def _draft_target_case" in middleware
    assert 'state_data.get("document_case_id")' in middleware
    assert 'state_data.get("replacement_document_id")' in middleware
    assert "ctx.case_service.get_case_for_user" in middleware
    assert "client_document_upload_allowed(target_case)" in middleware
    assert 'f"document_upload_resume:v2:{int(case_id)}"' in middleware

    switched = middleware.split(
        "if selected_case is None or int(selected_case.id) != int(target_case.id):",
        1,
    )[1].split(
        "# The exact selected Case is also the server-verified draft owner.",
        1,
    )[0]
    assert "await db.rollback()" in switched
    assert "_preserve_switched_case_upload" in switched
    assert "return None" in switched
    assert "state.clear()" not in switched
    assert "return await handler(event, data)" not in switched


def test_stale_or_stage_invalid_original_upload_case_still_clears_fail_closed():
    middleware = read("app/bot/document_replacement_protection.py")

    stale = middleware.split(
        "if target_case is None or not client_document_upload_allowed(target_case):",
        1,
    )[1].split(
        "try:\n                selected_case =",
        1,
    )[0]
    assert "await db.rollback()" in stale
    assert "await _stage_recovery(event, state)" in stale
    assert "return None" in stale


def test_resume_callback_reselects_only_server_verified_draft_case_without_clearing_state():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    assert "async def resume_document_upload_for_exact_case" in guard
    resume = guard.split("async def resume_document_upload_for_exact_case", 1)[1]
    assert "current_state != DocumentUploadStates.waiting_file.state" in resume
    assert 'state_data.get("document_case_id")' in resume
    assert 'state_data.get("replacement_document_id")' in resume
    assert "await db.get(Document" in resume
    assert "int(replacement.case_id) == int(target.id)" in resume
    assert "client_document_upload_allowed(target)" in resume
    assert "await ctx.case_service.select_case_for_user(" in resume
    assert "await db.commit()" in resume
    assert "ЗАГРУЗКА ВОССТАНОВЛЕНА" in resume

    success = resume.split("try:\n        await ctx.case_service.select_case_for_user", 1)[1].split(
        "except Exception:", 1
    )[0]
    assert "state.clear()" not in success


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
