from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unassigned_workdesk_queue_uses_product_assignment_policy():
    guard = read("app/api/workdesk_ui_guard.py")

    assert '@router.get("/admin/work-queues/unassigned")' in guard
    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in guard
    assert "Case.assigned_lawyer_id.is_(None)" in guard
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in guard
    assert '_case_row(case, queue="unassigned", lawyer_name=None)' in guard


def test_dashboard_and_unassigned_list_share_same_status_policy():
    guard = read("app/api/workdesk_ui_guard.py")
    dashboard = read("app/admin/admin_dashboard.py")

    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in guard
    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in dashboard
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in guard
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in dashboard


def test_legacy_generic_queue_is_shadowed_by_early_exact_route():
    operator = read("app/api/operator_guard.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(workdesk_ui_guard_router)" in operator
    assert "router.include_router(operator_guard_router)" in initial_setup
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"web_admin\", web_admin_router)'
    )
