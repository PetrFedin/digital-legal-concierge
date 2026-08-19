from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


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
        ("case_assignment", "payment_review_center"),
        ("case_assignment", "sla_center"),
        ("contract_workspace_ui", "lawyer"),
        ("contract_workspace_ui", "lawyer_workspace"),
        ("guided_message_center", "message_center"),
        ("guided_refund_center", "refund_center"),
    ]
    for early, legacy in required_order:
        assert source.index(f'(\"{early}\",') < source.index(f'(\"{legacy}\",')


def test_consultation_outcomes_no_longer_depend_on_router_include_order():
    source = MAIN.read_text(encoding="utf-8")

    assert "from app.api.consultation_outcomes_product import router as consultation_outcomes_product_router" in source
    assert '("consultation_outcomes_product", consultation_outcomes_product_router)' in source
    assert "guided_consultation_outcomes_router" not in source
    assert "consultation_outcomes_ui_guard_router" not in source


def test_public_app_level_health_ready_are_shadowed_by_early_minimal_routes():
    main = MAIN.read_text(encoding="utf-8")
    setup = read("app/api/initial_setup_wizard.py")
    assert '@router.get("/health")' in setup
    assert '@router.get("/ready")' in setup
    assert "JSONResponse(status_code=503" in setup
    assert main.index('(\"initial_setup_wizard\",') < main.index("for _, router in router_specs:")


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
