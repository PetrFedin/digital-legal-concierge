from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_live_workdesk_not_shadow_copy_contains_integrity_panel():
    source = read("app/api/assignment_queue.py")
    main = read("app/main.py")

    assert "from app.api.workdesk_integrity import inject_workdesk_integrity" in source
    ui = source.split('@router.get("/admin/workdesk/ui"', 1)[1].split(
        '@router.get("/admin/workdesk/cases/{case_id}/responsibility")', 1
    )[0]
    assert "inject_workdesk_integrity(html)" in ui
    assert main.index('(\"assignment_queue\", assignment_queue_router)') < main.index(
        '(\"workdesk_timeline\", workdesk_timeline_router)'
    )
    assert main.index('(\"assignment_queue\", assignment_queue_router)') < main.index(
        '(\"workdesk\", workdesk_router)'
    )


def test_unreachable_assignee_repair_is_snapshot_locked_and_not_manual_status_edit():
    source = read("app/api/case_assignment_repair.py")

    assert ".with_for_update()" in source
    assert "case.assigned_lawyer_id != expected_lawyer_id" in source
    assert "str(case.status) != expected_status" in source
    assert "expected_lawyer_id in operational_ids" in source
    assert "await service.unassign_case(" in source
    assert "automatic_assignment_required(case.status)" in source
    assert "await service.auto_assign_case(" in source
    assert "CaseService" not in source
    assert "next_status" not in source


def test_repair_keeps_case_controlled_when_replacement_capacity_is_absent():
    source = read("app/api/case_assignment_repair.py")

    assert '"unassigned_waiting_capacity"' in source
    assert '"obsolete_assignment_removed"' in source
    assert '"reassigned"' in source
    assert "await db.commit()" in source


def test_workdesk_assign_button_routes_existing_assignee_to_repair_endpoint():
    source = read("app/api/assignment_queue.py")

    patch = source.split('_WORKDESK_RESPONSIBILITY_PATCH = r"""', 1)[1].split('"""', 1)[0]
    assert "const originalAssign=assign" in patch
    assert "if(!currentLawyer)return originalAssign(id,b)" in patch
    assert "/repair-unreachable" in patch
    assert "expected_lawyer_id:currentLawyer" in patch
    assert "expected_status:snapshot.case.status" in patch


def test_case_assignment_composite_router_mounts_repair_before_legacy_assignment_routes():
    source = read("app/api/case_assignment.py")

    assert "case_assignment_repair_router" in source
    assert source.index("router.include_router(case_assignment_repair_router)") < source.index(
        "router.include_router(assignment_router)"
    )
