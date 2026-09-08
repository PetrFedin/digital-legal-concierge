from __future__ import annotations

import ast
from collections import defaultdict
from pathlib import Path

from fastapi.routing import iter_route_contexts

from app.api.admin_queue_guard import (
    retire_legacy_lawyer_creation,
    safe_legacy_admin_queue,
    safe_legacy_lawyer_list,
    safe_manual_scheduler_run_once,
)
from app.api.backup_center import router as retired_backup_center_router
from app.api.backup_manager import (
    backup_status_override,
    backup_ui_override,
    verify_backup_override,
)
from app.api.guided_lawyer_ui import guided_client_no_show
from app.api.lawyer_consultation_decision_guard import guarded_complete_consultation
from app.api.message_center_role_ui import (
    role_safe_message_center_ui,
    router as retired_message_center_ui_router,
)
from app.api.operator_guard import router as retired_operator_guard_router
from app.api.payment_safety_guard import (
    guarded_fake_payment_page,
    guarded_fake_payment_success,
    guarded_fake_payment_webhook,
)
from app.api.payment_webhooks import payment_result, yookassa_payment_webhook
from app.api.staff_ui_guards import retired_technical_cases_ui
from app.api.workdesk_runtime_ui import workdesk_runtime_ui
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "app" / "main.py"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _effective_routes():
    return tuple(iter_route_contexts(create_app().routes))


def _owners(method: str, path: str):
    return [
        route
        for route in _effective_routes()
        if route.path == path and method in (route.methods or set())
    ]


def _only(method: str, path: str):
    owners = _owners(method, path)
    assert len(owners) == 1, (method, path, [route.name for route in owners])
    return owners[0]


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


def test_every_runtime_method_path_has_exactly_one_owner():
    seen: dict[tuple[str, str], list[str]] = defaultdict(list)
    for route in _effective_routes():
        for method in (route.methods or set()) - {"HEAD", "OPTIONS"}:
            seen[(method, route.path)].append(str(route.name or "<unnamed>"))

    duplicates = {
        f"{method} {path}": names
        for (method, path), names in sorted(seen.items())
        if len(names) != 1
    }
    assert duplicates == {}


def test_retired_compatibility_facades_are_route_free():
    assert retired_operator_guard_router.routes == []
    assert retired_backup_center_router.routes == []
    assert retired_message_center_ui_router.routes == []

    setup = read("app/api/initial_setup_wizard.py")
    acceptance = read("app/api/acceptance_center.py")
    timeline = read("app/api/workdesk_timeline.py")
    assert "operator_guard_router" not in setup
    assert "superadmin_ui_guards_router" not in acceptance
    assert "WORKDESK_HTML =" not in timeline
    assert "inject_workdesk_integrity" not in timeline


def test_backup_center_has_one_hardened_runtime_owner():
    assert _only("GET", "/backup-center/status").endpoint is backup_status_override
    assert _only("GET", "/backup-center/ui").endpoint is backup_ui_override
    assert (
        _only("POST", "/backup-center/verify/{archive_name}").endpoint
        is verify_backup_override
    )


def test_message_center_ui_is_owned_by_product_with_role_safe_renderer():
    assert _only("GET", "/message-center/ui").endpoint is role_safe_message_center_ui
    for method, path in (
        ("GET", "/message-center/status"),
        ("GET", "/message-center/cases/{case_id}/messages"),
        ("POST", "/message-center/cases/{case_id}/reply"),
        ("POST", "/message-center/{message_id}/read"),
    ):
        _only(method, path)


def test_legacy_admin_paths_have_hardened_single_owners():
    assert _only("GET", "/admin/queue").endpoint is safe_legacy_admin_queue
    assert _only("GET", "/admin/lawyers").endpoint is safe_legacy_lawyer_list
    assert _only("POST", "/admin/lawyers").endpoint is retire_legacy_lawyer_creation
    assert (
        _only("POST", "/admin/scheduler/run-once").endpoint
        is safe_manual_scheduler_run_once
    )


def test_lawyer_m2_mutations_and_ui_have_product_owners():
    assert (
        _only("POST", "/lawyer/consultations/{consultation_id}/complete").endpoint
        is guarded_complete_consultation
    )
    assert (
        _only(
            "POST",
            "/lawyer/consultations/{consultation_id}/client-no-show",
        ).endpoint
        is guided_client_no_show
    )
    _only("GET", "/lawyer/ui")
    _only("GET", "/lawyer/workspace/ui")
    _only("GET", "/lawyer/consultation-desk/ui")


def test_fake_payment_surface_is_owned_only_by_local_test_guard():
    assert _only("POST", "/webhooks/payments/fake").endpoint is guarded_fake_payment_webhook
    assert (
        _only("GET", "/webhooks/payments/fake-pay/{payment_id}").endpoint
        is guarded_fake_payment_page
    )
    assert (
        _only("POST", "/webhooks/payments/fake-pay/{payment_id}/success").endpoint
        is guarded_fake_payment_success
    )
    assert _only("POST", "/webhooks/payments/yookassa").endpoint is yookassa_payment_webhook
    assert _only("GET", "/webhooks/payments/payment-result").endpoint is payment_result


def test_workdesk_and_technical_recovery_have_non_overlapping_ui_owners():
    assert _only("GET", "/admin/workdesk/ui").endpoint is workdesk_runtime_ui
    assert _only("GET", "/admin/technical-cases/ui").endpoint is retired_technical_cases_ui
    _only("GET", "/admin/technical-cases")
    _only("GET", "/admin/technical-cases/{case_id}/context")
    _only("POST", "/admin/technical-cases/{case_id}/recover")
    _only("GET", "/admin/workdesk/cases/{case_id}/timeline")


def test_document_payment_sla_refund_and_outcome_products_keep_single_owners():
    for method, path in (
        ("GET", "/document-access/ui"),
        ("GET", "/document-access/review/ui"),
        ("GET", "/admin/payment-reviews"),
        ("POST", "/admin/payment-reviews/{payment_id}/resolve"),
        ("GET", "/admin/payment-reviews/ui"),
        ("GET", "/admin/refunds"),
        ("POST", "/admin/refunds/{payment_id}/resolve"),
        ("GET", "/admin/refunds/ui"),
        ("GET", "/admin/sla"),
        ("GET", "/admin/sla/ui"),
        ("GET", "/admin/consultation-outcomes"),
        ("GET", "/admin/consultation-outcomes/ui"),
        ("GET", "/admin/workdesk/integrity"),
    ):
        _only(method, path)


def test_superadmin_sensitive_ui_paths_are_unique():
    for path in (
        "/access/ui",
        "/audit-center/ui",
        "/security-events/ui",
        "/retention/ui",
        "/recovery-center/ui",
    ):
        _only("GET", path)
