from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_live_workdesk_is_owned_by_explicit_product_router():
    assignment_product = read("app/api/assignment_queue_product.py")
    workdesk_product = read("app/api/workdesk_product.py")
    runtime_ui = read("app/api/workdesk_runtime_ui.py")
    main = read("app/main.py")

    assert '"/admin/workdesk/ui"' in workdesk_product
    assert "workdesk_runtime_ui" in workdesk_product
    assert '"/admin/workdesk/ui"' not in assignment_product
    assert "_append_body_extensions" in runtime_ui
    assert "_WORKDESK_PRODUCT_EXTENSION" in runtime_ui
    assert "inject_workdesk_integrity" not in runtime_ui
    assert "assignment_queue_router" not in main
    assert "workdesk_router" not in main
    assert '("assignment_queue_product", assignment_queue_product_router)' in main
    assert '("workdesk_product", workdesk_product_router)' in main


def test_unreachable_assignee_repair_is_snapshot_locked_and_not_manual_status_edit():
    source = read("app/api/case_assignment_repair.py")

    assert ".with_for_update()" in source
    assert "case.assigned_lawyer_id != expected_lawyer_id" in source
    assert "str(case.status) != expected_status" in source
    assert "expected_lawyer_id in operational_ids" in source
    assert "await service.unassign_case(" in source
    assert "expected_lawyer_id=expected_lawyer_id" in source
    assert "expected_status=expected_status" in source
    assert "automatic_assignment_required(case.status)" in source
    assert "await service.auto_assign_case(" in source
    assert "CaseService" not in source
    assert "next_status" not in source


def test_repair_keeps_unassignment_durable_when_replacement_races_or_capacity_is_absent():
    source = read("app/api/case_assignment_repair.py")

    repair = source.split("async def repair_unreachable_assignment(", 1)[1].split(
        '@router.get(\n    "/admin/case-assignment/cases/{case_id}/repair-unreachable/ui"', 1
    )[0]
    assert "await service.unassign_case(" in repair
    assert "async with db.begin_nested():" in repair
    assert "except (LookupError, ValueError) as error:" in repair
    assert "CASE_REASSIGNMENT_DEFERRED_AFTER_UNREACHABLE_REMOVAL" in repair
    assert "await db.commit()" in repair
    assert '"unassigned_waiting_capacity"' in repair
    assert '"replacement_warning": replacement_warning' in repair


def test_repair_availability_counter_excludes_fully_loaded_lawyers():
    source = read("app/api/case_assignment_repair.py")
    assert 'sum(\n            1 for item in operational if bool(item.get("is_available"))\n        )' in source


def test_workdesk_assign_button_routes_existing_assignee_to_repair_endpoint():
    runtime_ui = read("app/api/workdesk_runtime_ui.py")

    assert "const originalAssign=assign" in runtime_ui
    assert "if(!currentLawyer)return originalAssign(id,b)" in runtime_ui
    assert "/repair-unreachable" in runtime_ui
    assert "expected_lawyer_id:currentLawyer" in runtime_ui
    assert "expected_status:snapshot.case.status" in runtime_ui


def test_case_assignment_composite_router_mounts_repair_before_legacy_assignment_routes():
    source = read("app/api/case_assignment.py")

    assert "case_assignment_repair_router" in source
    assert source.index("router.include_router(case_assignment_repair_router)") < source.index(
        "router.include_router(assignment_router)"
    )
