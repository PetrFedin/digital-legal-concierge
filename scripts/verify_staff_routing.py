"""Exact import-time ASGI staff route authorization verification.

Run in a fresh Python interpreter (same as a web server startup) so that a
pytest worker's patched/mutated app globals cannot give false route findings.
No database or network operations. A missing route, ambiguous path+method,
missing live session dependency or bearer query param fails closed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from importlib.metadata import version
import json

from fastapi import FastAPI
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

    # Diagnostic-only metadata, never credentials or case data.
    probe = FastAPI()
    probe.include_router(admin.router)
    return {
        "ok": not defects,
        "fastapi_version": version("fastapi"),
        "starlette_version": version("starlette"),
        "pydantic_version": version("pydantic"),
        "admin_router_declarations": len(admin.router.routes),
        "admin_probe_routes": len([
            item for item in probe.routes if isinstance(item, APIRoute)
        ]),
        "asgi_app_routes": len(app.routes),
        "asgi_router_routes": len(app.router.routes),
        "asgi_router_class": type(app.router).__name__,
        "asgi_route_classes": dict(Counter(
            type(item).__module__ + "." + type(item).__name__
            for item in app.routes
        )),
        "admin_declaration_classes": dict(Counter(
            type(item).__module__ + "." + type(item).__name__
            for item in admin.router.routes
        )),
        "admin_first_route_fields": {
            "path": getattr(admin.router.routes[0], "path", None),
            "methods": sorted(getattr(admin.router.routes[0], "methods", []) or []),
            "has_dependant": hasattr(admin.router.routes[0], "dependant"),
            "has_endpoint": hasattr(admin.router.routes[0], "endpoint"),
            "name": getattr(admin.router.routes[0], "name", None),
        },
        "asgi_paths_sample": [
            str(getattr(item, "path", "?")) for item in app.routes[:22]
        ],
        "asgi_http_paths_sample": [
            path for path, _method in sorted(mounted)[:10]
        ],
        "tested_staff_endpoints": len(checked),
        "all_http_endpoints": len(mounted),
        "expected_router_families": len(GATED_ROUTERS),
        "failures": defects[:30],
    }


if __name__ == "__main__":
    report = audit_staff_routes()
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 1)
