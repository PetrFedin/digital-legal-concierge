from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_client_document_handoff_mutations_are_exact_case_bound():
    guard = read("app/bot/screens/document_mutation_guard.py")

    assert "doc_finish_upload:v2:" in guard
    assert "doc_skip_m2:v2:" in guard
    assert "Case.id == int(case_id)" in guard
    assert "Case.client_id == int(user.id)" in guard
    assert ".with_for_update()" in guard
    assert "int(active.id) != int(case.id)" in guard
    assert "client_document_upload_allowed(case)" in guard


def test_historical_unbound_document_mutations_are_navigation_only():
    guard = read("app/bot/screens/document_mutation_guard.py")

    assert '_RAW_MUTATIONS = {"doc_finish_upload", "doc_skip_m2"}' in guard
    legacy = guard.split("async def legacy_unbound_document_mutation_refresh", 1)[1].split(
        "async def finish_documents_for_exact_case", 1
    )[0]
    assert "await _refresh_documents(" in legacy
    assert "send_documents_to_review" not in legacy
    assert "change_status(" not in legacy
    assert "db.commit" not in legacy


def test_client_m1_handoff_stops_at_received_not_lawyer_review():
    guard = read("app/bot/screens/document_mutation_guard.py")

    m1 = guard.split('if str(case.route) == "M1":', 1)[1].split(
        "elif status in {CaseStatus.M2_DESCRIPTION_PENDING", 1
    )[0]
    assert "next_status=CaseStatus.M1_DOCUMENTS_RECEIVED" in m1
    assert "M1_LAWYER_REVIEW" not in m1
    assert "фактическая проверка юристом ещё не начата" in m1
    assert "ждём назначения ответственного и фактического начала проверки" in m1


def test_first_valid_staff_review_decision_establishes_lawyer_review_boundary():
    service = read("app/domain/documents/document_review_service.py")

    assert "async def _start_m1_review_if_needed" in service
    boundary = service.split("async def _start_m1_review_if_needed", 1)[1].split(
        "async def _request_new_version", 1
    )[0]
    assert "CaseStatus.M1_DOCUMENTS_RECEIVED" in boundary
    assert "next_status=CaseStatus.M1_LAWYER_REVIEW" in boundary
    assert "actor_type=\"lawyer\" if actor.role == \"lawyer\" else \"admin_user\"" in boundary

    review = service.split("async def review(", 1)[1]
    assert "current_status != DocumentStatus.ON_REVIEW" in review
    assert review.index("self.assert_snapshot(") < review.index("_start_m1_review_if_needed")
    assert review.index("current_status != DocumentStatus.ON_REVIEW") < review.index(
        "_start_m1_review_if_needed"
    )


def test_live_document_center_emits_bound_handoff_actions_and_guard_has_route_priority():
    wording = read("app/bot/client_wording_patch.py")
    bot = read("app/bot/bot.py")

    assert 'callback = f"doc_finish_upload:v2:{case_id}"' in wording
    assert 'callback = f"doc_skip_m2:v2:{case_id}"' in wording
    assert bot.index("document_mutation_guard.router,") < bot.index(
        "document_action_center.router,"
    )
    assert bot.index("document_mutation_guard.router,") < bot.index("documents.router,")


def test_document_review_html_is_personally_guarded_before_legacy_shell():
    guard = read("app/api/staff_ui_guards.py")
    case_assignment = read("app/api/case_assignment.py")
    main = read("app/main.py")

    assert '@router.get("/document-access/review/ui"' in guard
    assert "ROLE_LAWYER" in guard
    assert "ROLE_ADMIN" in guard
    assert "ROLE_SUPERADMIN" in guard
    assert "resolve_document_actor" in guard
    assert "REVIEW_HTML" in guard
    assert "router.include_router(staff_ui_guards_router)" in case_assignment
    assert main.index('(\"case_assignment\", case_assignment_router)') < main.index(
        '(\"document_access\", document_access_router)'
    )
