from __future__ import annotations

import ast
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _owners(method: str, path: str):
    return [
        route
        for route in create_app().routes
        if getattr(route, "path", None) == path
        and method in (getattr(route, "methods", None) or set())
    ]


def test_every_app_api_module_imported_by_main_physically_exists():
    source = MAIN.read_text(encoding="utf-8")
    tree = ast.parse(source)
    missing: list[str] = []
    imported: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        module = str(node.module or "")
        if not module.startswith("app.api."):
            continue
        relative = module.split("app.api.", 1)[1].replace(".", "/")
        imported.add(relative)
        py_file = ROOT / "app" / "api" / f"{relative}.py"
        package = ROOT / "app" / "api" / relative / "__init__.py"
        if not py_file.exists() and not package.exists():
            missing.append(module)

    assert imported
    assert sorted(missing) == []


def test_remaining_compatibility_guards_precede_only_their_historical_surfaces():
    source = MAIN.read_text(encoding="utf-8")
    required_order = [
        ("initial_setup_wizard", "admin"),
        ("initial_setup_wizard", "operator"),
        ("contract_workspace_ui", "lawyer"),
        ("contract_workspace_ui", "lawyer_workspace"),
    ]
    for early, legacy in required_order:
        assert source.index(f'(\"{early}\",') < source.index(f'(\"{legacy}\",')


def test_message_center_has_one_runtime_product_owner():
    main = MAIN.read_text(encoding="utf-8")
    product = read("app/api/message_center_product.py")

    assert "from app.api.message_center_product import router as message_center_product_router" in main
    assert '("message_center_product", message_center_product_router)' in main
    assert "guided_message_center_router" not in main
    assert "message_center_router" not in main
    assert "guided_message_center_status" in product
    assert "mark_message_read" in product

    for method, path in (
        ("GET", "/message-center/status"),
        ("GET", "/message-center/cases/{case_id}/messages"),
        ("POST", "/message-center/cases/{case_id}/reply"),
        ("POST", "/message-center/{message_id}/read"),
        ("GET", "/message-center/ui"),
    ):
        owners = _owners(method, path)
        assert len(owners) == 1, (method, path, [route.name for route in owners])


def test_payment_review_and_sla_have_single_product_owners():
    main = MAIN.read_text(encoding="utf-8")
    assignment = read("app/api/case_assignment.py")
    staff_guards = read("app/api/staff_ui_guards.py")

    assert "payment_review_product_router" in main
    assert "sla_product_router" in main
    assert "payment_review_center_router" not in main
    assert "sla_center_router" not in main
    assert "staff_ui_guards_router" not in assignment
    assert '@router.get("/admin/payment-reviews/ui")' not in staff_guards
    assert '@router.get("/admin/sla/ui")' not in staff_guards

    for method, path in (
        ("GET", "/admin/payment-reviews"),
        ("GET", "/admin/payment-reviews/slots"),
        ("POST", "/admin/payment-reviews/{payment_id}/resolve"),
        ("GET", "/admin/payment-reviews/ui"),
        ("GET", "/admin/sla"),
        ("POST", "/admin/sla/{case_id}/acknowledge"),
        ("POST", "/admin/sla/run"),
        ("GET", "/admin/sla/ui"),
    ):
        owners = _owners(method, path)
        assert len(owners) == 1, (method, path, [route.name for route in owners])


def test_consultation_outcomes_no_longer_depend_on_router_include_order():
    source = MAIN.read_text(encoding="utf-8")

    assert "from app.api.consultation_outcomes_product import router as consultation_outcomes_product_router" in source
    assert '("consultation_outcomes_product", consultation_outcomes_product_router)' in source
    assert "guided_consultation_outcomes_router" not in source
    assert "consultation_outcomes_ui_guard_router" not in source


def test_refunds_have_one_runtime_product_owner():
    main = MAIN.read_text(encoding="utf-8")
    operator_guard = read("app/api/operator_guard.py")
    product = read("app/api/refund_product.py")
    assignment = read("app/api/case_assignment.py")

    assert "from app.api.refund_product import router as refund_product_router" in main
    assert '("refund_product", refund_product_router)' in main
    assert "guided_refund_center_router" not in main
    assert "refund_center_router" not in main
    assert "refund_resolution_guard_router" not in operator_guard
    assert "refund_resolution_guard_router" not in assignment
    assert 'prefix="/admin/refunds"' in product

    for method, path in (
        ("GET", "/admin/refunds"),
        ("GET", "/admin/refunds/context"),
        ("GET", "/admin/refunds/declined"),
        ("GET", "/admin/refunds/ui"),
        ("POST", "/admin/refunds/{payment_id}/resolve"),
        ("POST", "/admin/refunds/{payment_id}/retry"),
    ):
        owners = _owners(method, path)
        assert len(owners) == 1, (method, path, [route.name for route in owners])


def test_workdesk_integrity_no_longer_depends_on_compatibility_mounts():
    main = MAIN.read_text(encoding="utf-8")
    setup = read("app/api/initial_setup_wizard.py")
    assignment = read("app/api/case_assignment.py")
    product = read("app/api/workdesk_integrity_product.py")

    assert "from app.api.workdesk_integrity_product import router as workdesk_integrity_product_router" in main
    assert '("workdesk_integrity_product", workdesk_integrity_product_router)' in main
    assert "workdesk_integrity_guard_router" not in setup
    assert "workdesk_integrity_guard_router" not in assignment
    assert '"/admin/workdesk/integrity"' in product
    assert "workdesk_integrity_guard" in product


def test_launch_health_and_ready_each_have_one_runtime_owner():
    main = MAIN.read_text(encoding="utf-8")
    setup = read("app/api/initial_setup_wizard.py")
    staff_guards = read("app/api/staff_ui_guards.py")

    assert '@router.get("/launch-check")' in setup
    assert '@app.get("/launch-check")' not in main
    assert '@router.get("/health")' not in setup
    assert '@router.get("/ready")' not in setup
    assert '@app.get("/health")' in main
    assert '@app.get("/ready")' in main
    assert "protected_health_center_ui" not in staff_guards

    for method, path in (
        ("GET", "/launch-check"),
        ("GET", "/health"),
        ("GET", "/ready"),
        ("GET", "/health-center/ui"),
        ("GET", "/admin/workdesk/integrity"),
    ):
        owners = _owners(method, path)
        assert len(owners) == 1, (method, path, [route.name for route in owners])


def test_legacy_demo_panels_are_not_sources_of_live_readiness():
    files = {
        "task": read("app/api/task_center.py"),
        "release": read("app/api/release_manager.py"),
        "handover": read("app/api/handover.py"),
        "ops": read("app/api/ops_guide.py"),
        "scenario": read("app/api/scenario_map.py"),
        "template": read("app/api/template_builder.py"),
        "calculator": read("app/api/calculator_builder.py"),
    }
    assert "consolidated_into_workdesk" in files["task"]
    assert "live_verification_required" in files["release"]
    assert "live_operational_contour" in files["handover"]
    assert "live_operations_only" in files["ops"]
    assert "live_e2e_is_source_of_truth" in files["scenario"]
    assert "consolidated_into_live_configuration" in files["template"]
    assert "calculator_is_runtime_flow_not_builder" in files["calculator"]
