"""Exact import-time ASGI staff route authorization verification.

Run in a fresh Python interpreter (same as a web server startup) so that a
pytest worker's patched/mutated app globals cannot give false route findings.
No database or network operations. A missing route, ambiguous path+method,
missing live session dependency or bearer query param fails closed.
"""
from __future__ import annotations

from collections import defaultdict
import json

from fastapi.routing import APIRoute

from app.api import (
    access_management, admin, backup_center, consultation_outcomes, exports,
    payment_review_center, security_event_center, workdesk,
)
from app.main import app
from app.security.current_admin import current_admin_gateway, current_superadmin_gateway

GATED_ROUTERS = (
    (admin.router, current_admin_gateway),
    (exports.router, current_admin_gateway),
    (workdesk.router, current_admin_gateway),
    (payment_review_center.router, current_admin_gateway),
    (consultation_outcomes.router, current_admin_gateway),
    (access_management.router, current_superadmin_gateway),
    (security_event_center.router, current_superadmin_gateway),
    (backup_center.router, current_superadmin_gateway),
)
FORBIDDEN_QUERY_CREDENTIALS = frozenset({
    "token", "api_key", "admin_token", "access_token",
})


def audit_staff_routes() -> dict[str, object]:
    mounted: dict[tuple[str, str], list[APIRoute]] = defaultdict(list)
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods or set():
            mounted[(route.path, method)].append(route)

    checked: set[tuple[str, str]] = set()
    defects: list[str] = []
    for subrouter, gateway in GATED_ROUTERS:
        definitions = [r for r in subrouter.routes if isinstance(r, APIRoute)]
        if not definitions:
            defects.append(f"Empty staff router: {subrouter.prefix}")
        for expected in definitions:
            for method in expected.methods or set():
                key = (expected.path, method)
                registered = mounted.get(key, [])
                if len(registered) != 1:
                    defects.append(
                        f"{method} {expected.path}: expected one mounted route, "
                        f"found {len(registered)}"
                    )
                    continue
                actual = registered[0]
                checked.add(key)
                if gateway not in {dep.call for dep in actual.dependant.dependencies}:
                    defects.append(
                        f"{method} {expected.path}: missing live "
                        f"{gateway.__name__} authorization"
                    )
                if subrouter is exports.router:
                    params = {p.name for p in actual.dependant.query_params}
                    bad = params & FORBIDDEN_QUERY_CREDENTIALS
                    if bad:
                        defects.append(
                            f"{method} {expected.path}: bearer URL query {sorted(bad)}"
                        )

    if len(checked) < 20:
        defects.append(f"Unexpected verified staff route count: {len(checked)}")
    return {
        "ok": not defects,
        "tested_staff_endpoints": len(checked),
        "all_http_endpoints": len(mounted),
        "expected_router_families": len(GATED_ROUTERS),
        "failures": defects[:30],
    }


if __name__ == "__main__":
    report = audit_staff_routes()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 1)
