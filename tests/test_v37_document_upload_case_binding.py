from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_upload_chooser_emits_case_bound_document_type_callbacks():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    assert 'return f"doc_type:v2:{int(case_id)}:{document_type}"' in guard
    assert "document_case_id=case_id" in guard
    assert "DocumentUploadStates.choosing_type" in guard
    assert "DocumentUploadStates.waiting_file" in guard
    assert "expected_case_id" in guard
    assert "int(case.id) != int(expected_case_id)" in guard


def test_upload_entry_stale_screen_is_scoped_before_chooser_rearms_fsm():
    entry = read("app/bot/screens/document_upload_entry_scope_guard.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "documents_upload_open")' in entry
    assert 'action="documents_upload_open"' in entry
    assert "allow_legacy_message_case_context=True" in entry
    assert "if scope is None or scope.case is None" in entry
    assert "document_upload_binding_guard._render_bound_chooser" in entry
    assert bot.index("document_upload_entry_scope_guard.router,") < bot.index(
        "document_upload_binding_guard.router,"
    )


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
        "return await handler(event, data)",
        1,
    )[0]
    assert "await db.rollback()" in switched
    assert "_preserve_switched_case_upload" in switched
    assert "return None" in switched
    assert "state.clear()" not in switched


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


def test_resume_uses_server_verified_draft_case_and_preserves_waiting_file_state():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    assert "async def _draft_target_case" in guard
    assert "ctx.case_service.get_case_for_user" in guard
    assert "async def _resume_upload_draft" in guard
    resume = guard.split("async def _resume_upload_draft", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "documents_upload_open")', 1
    )[0]
    assert "current_state not in _DOCUMENT_UPLOAD_STATES" in resume
    assert 'state_data.get("document_case_id")' in resume
    assert 'state_data.get("replacement_document_id")' in resume
    assert "await db.get(Document" in resume
    assert "client_document_upload_allowed(target)" in resume
    assert "await ctx.case_service.select_case_for_user(" in resume
    assert "await db.commit()" in resume
    assert "ЗАГРУЗКА ВОССТАНОВЛЕНА" in resume

    waiting_success = resume.rsplit(
        "try:\n        await ctx.case_service.select_case_for_user",
        1,
    )[1].split("except Exception:", 1)[0]
    assert "state.clear()" not in waiting_success


def test_exact_and_draft_resume_callbacks_share_the_same_verified_recovery_path():
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    exact = guard.split("async def resume_document_upload_for_exact_case", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "document_upload_resume_draft")', 1
    )[0]
    current = guard.split("async def resume_current_document_upload_draft", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "document_upload_discard_confirm")', 1
    )[0]
    assert "_parse_resume_case_id" in exact
    assert "await _resume_upload_draft(" in exact
    assert "requested_case_id=requested_case_id" in exact
    assert "await _resume_upload_draft(callback, state, db)" in current


def test_document_upload_navigation_requires_explicit_resume_or_confirmed_discard():
    protection = read("app/bot/draft_protection.py")
    guard = read("app/bot/screens/document_upload_binding_guard.py")

    assert 'DOCUMENT_UPLOAD_DRAFT = "document_upload"' in protection
    assert "DocumentUploadStates.choosing_type.state" in protection
    assert "DocumentUploadStates.waiting_file.state" in protection
    assert '"document_upload_resume_draft"' in protection
    assert '"document_upload_discard_confirm"' in protection
    assert '"doc_skip_m2:v2:"' in protection
    assert "Продолжите загрузку либо отмените её явно" in protection

    assert "async def confirm_document_upload_discard" in guard
    confirm = guard.split("async def confirm_document_upload_discard", 1)[1].split(
        '@router.callback_query(lambda c: c.data == "document_upload_discard")', 1
    )[0]
    assert "state.clear()" not in confirm
    assert '"document_upload_discard"' in confirm

    discard = guard.split("async def discard_document_upload", 1)[1]
    assert "await state.clear()" in discard
    assert "Ранее сохранённые документы и данные обращения не изменены" in discard


def test_m2_skip_is_exact_case_bound_and_cleans_only_fsm_after_durable_transition():
    protection = read("app/bot/draft_protection.py")
    mutation = read("app/bot/screens/document_mutation_guard.py")

    assert '"doc_skip_m2:v2:"' in protection
    skip = mutation.split("async def skip_documents_for_exact_m2_case", 1)[1]
    assert '_case_id(callback, "doc_skip_m2")' in skip
    assert "await db.commit()" in skip
    cleanup = skip.split("# The legal transition is already durable.", 1)[1]
    assert "await state.clear()" in cleanup
    assert cleanup.index("await state.clear()") < cleanup.index("await callback.message.edit_text")


def test_direct_replacement_keeps_stricter_document_version_snapshot_path():
    middleware = read("app/bot/document_replacement_protection.py")

    assert '"replacement_document_id" in state_data' in middleware
    assert '"replacement_expected_version" in state_data' in middleware
    assert "replacement_snapshot_matches(" in middleware
    assert "int(latest.id) == int(document.id)" in middleware


def test_upload_binding_guards_precede_mutation_center_and_legacy_documents():
    bot = read("app/bot/bot.py")

    assert bot.index("document_upload_entry_scope_guard.router,") < bot.index(
        "document_upload_binding_guard.router,"
    )
    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "document_mutation_guard.router,"
    )
    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "document_action_center.router,"
    )
    assert bot.index("document_upload_binding_guard.router,") < bot.index(
        "documents.router,"
    )
