from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.routing import iter_route_contexts
from starlette.routing import compile_path

from app.main import app


UI_ENTRYPOINTS = {
    "/",
    "/handover",
    "/launch-assistant",
    "/login",
    "/mfa/manage",
    "/security-check",
}
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
AUTH_STATUSES = {401, 403}


@dataclass(frozen=True)
class GetRoute:
    path: str
    pattern: object


def _get_routes() -> list[GetRoute]:
    routes: list[GetRoute] = []
    for context in iter_route_contexts(app.routes):
        if not context.path or context.endpoint is None:
            continue
        if "GET" not in frozenset(context.methods or ()):
            continue
        pattern, _, _ = compile_path(context.path)
        routes.append(GetRoute(path=context.path, pattern=pattern))
    return routes


def _static_ui_paths() -> list[str]:
    paths = {
        route.path
        for route in _get_routes()
        if "{" not in route.path
        and (
            route.path in UI_ENTRYPOINTS
            or route.path.endswith("/ui")
            or route.path.endswith("-ui")
        )
    }
    return sorted(paths)


def _registered_get_target(location: str, routes: list[GetRoute]) -> bool:
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc != "testserver":
            return False
    path = parsed.path or "/"
    return any(route.pattern.fullmatch(path) for route in routes)


@pytest.mark.asyncio
async def test_every_static_ui_entrypoint_renders_without_routing_or_server_errors():
    paths = _static_ui_paths()
    routes = _get_routes()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    request_failures: list[str] = []
    invalid_statuses: list[str] = []
    invalid_redirects: list[str] = []
    invalid_html: list[str] = []
    empty_auth_responses: list[str] = []

    for path in paths:
        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
                timeout=10.0,
            ) as client:
                response = await client.get(
                    path,
                    headers={"accept": "text/html,application/xhtml+xml"},
                )
        except Exception as exc:  # pragma: no cover - diagnostic guard
            request_failures.append(f"GET {path}: {type(exc).__name__}: {exc}")
            continue

        status = response.status_code
        if status == 200:
            content_type = response.headers.get("content-type", "").lower()
            body = response.text.strip()
            if "text/html" not in content_type:
                invalid_html.append(
                    f"GET {path}: expected text/html, got {content_type or '<missing>'}"
                )
            elif len(body) < 40:
                invalid_html.append(
                    f"GET {path}: HTML body is unexpectedly short ({len(body)} chars)"
                )
            elif "internal server error" in body.lower() or "traceback (most recent call last)" in body.lower():
                invalid_html.append(f"GET {path}: response exposes a server failure")
            continue

        if status in REDIRECT_STATUSES:
            location = response.headers.get("location", "").strip()
            if not location:
                invalid_redirects.append(f"GET {path}: {status} without Location")
            elif not _registered_get_target(location, routes):
                invalid_redirects.append(
                    f"GET {path}: {status} points to unregistered or external target {location!r}"
                )
            continue

        if status in AUTH_STATUSES:
            if not response.content.strip():
                empty_auth_responses.append(
                    f"GET {path}: {status} has no explanation for the user"
                )
            continue

        invalid_statuses.append(
            f"GET {path}: unexpected {status}; body={response.text[:240]!r}"
        )

    assert len(paths) >= 25, (
        "runtime UI smoke audit is unexpectedly shallow: "
        f"{len(paths)} static UI entrypoints"
    )
    assert request_failures == [], "UI requests raised exceptions:\n" + "\n".join(
        request_failures
    )
    assert invalid_statuses == [], "UI entrypoints returned invalid statuses:\n" + "\n".join(
        invalid_statuses
    )
    assert invalid_redirects == [], "UI redirects are broken:\n" + "\n".join(
        invalid_redirects
    )
    assert invalid_html == [], "UI pages did not render valid HTML:\n" + "\n".join(
        invalid_html
    )
    assert empty_auth_responses == [], "authorization failures are empty:\n" + "\n".join(
        empty_auth_responses
    )
