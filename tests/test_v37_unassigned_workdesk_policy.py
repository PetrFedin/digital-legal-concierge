from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_unassigned_workdesk_queue_uses_product_assignment_policy():
    implementation = read("app/api/assignment_queue.py")
    product = read("app/api/assignment_queue_product.py")

    assert "async def actionable_unassigned_queue(" in implementation
    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in implementation
    assert "Case.assigned_lawyer_id.is_(None)" in implementation
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in implementation
    assert 'queue="unassigned"' in implementation
    assert '"/admin/work-queues/unassigned"' in product
    assert "actionable_unassigned_queue" in product


def test_dashboard_and_unassigned_list_share_same_status_policy():
    implementation = read("app/api/assignment_queue.py")
    dashboard = read("app/admin/admin_dashboard.py")

    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in implementation
    assert "AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES" in dashboard
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in implementation
    assert "Case.status.in_(AUTO_ASSIGNMENT_REQUIRED_STATUS_VALUES)" in dashboard


def test_exact_unassigned_queue_has_explicit_product_owner_not_shadow_precedence():
    product = read("app/api/assignment_queue_product.py")
    operator = read("app/api/operator_guard.py")
    main = read("app/main.py")

    assert '"/admin/work-queues/unassigned"' in product
    assert "actionable_unassigned_queue" in product
    assert "workdesk_ui_guard_router" not in operator
    assert '("assignment_queue_product", assignment_queue_product_router)' in main
    assert "assignment_queue_router" not in main
