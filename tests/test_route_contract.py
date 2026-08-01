from __future__ import annotations

import inspect
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.routing import iter_route_contexts

from app.main import app


IGNORED_FRAMEWORK_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}


@dataclass(frozen=True)
class RouteRecord:
    path: str
    methods: frozenset[str]
    name: str | None
    endpoint: Any


def _application_routes() -> list[RouteRecord]:
    records: list[RouteRecord] = []
    for route_context in iter_route_contexts(app.routes):
        path = route_context.path
        endpoint = route_context.endpoint
        if not path or endpoint is None or path in IGNORED_FRAMEWORK_PATHS:
            continue
        records.append(
            RouteRecord(
                path=path,
                methods=frozenset(route_context.methods or ()),
                name=route_context.name,
                endpoint=endpoint,
            )
        )
    return records


def _route_by_path(path: str) -> RouteRecord:
    matches = [route for route in _application_routes() if route.path == path]
    assert matches, f"route is not registered: {path}"
    assert len(matches) == 1, f"route is registered more than once: {path}"
    return matches[0]


@pytest.mark.asyncio
async def test_every_launch_check_internal_link_resolves_to_one_registered_route():
    launch_route = _route_by_path("/launch-check")
    result = launch_route.endpoint()
    if inspect.isawaitable(result):
        result = await result

    assert isinstance(result, dict)
    links = {
        key: value
        for key, value in result.items()
        if isinstance(value, str) and value.startswith("/")
    }
    assert links, "launch-check must publish at least one internal route"

    missing: dict[str, str] = {}
    duplicated: dict[str, str] = {}
    registered_paths = Counter(route.path for route in _application_routes())
    for key, path in links.items():
        count = registered_paths[path]
        if count == 0:
            missing[key] = path
        elif count > 1:
            duplicated[key] = path

    assert missing == {}, f"launch-check contains missing routes: {missing}"
    assert duplicated == {}, f"launch-check contains ambiguous routes: {duplicated}"


def test_no_duplicate_http_method_and_path_handlers_are_registered():
    registrations: Counter[tuple[str, str]] = Counter()
    for route in _application_routes():
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            registrations[(method, route.path)] += 1

    duplicates = {
        f"{method} {path}": count
        for (method, path), count in registrations.items()
        if count > 1
    }
    assert duplicates == {}, f"duplicate route handlers shadow each other: {duplicates}"


def test_every_named_application_route_has_a_unique_name():
    routes_by_name: defaultdict[str, list[str]] = defaultdict(list)
    for route in _application_routes():
        if not route.name or route.name in {
            "openapi",
            "swagger_ui_html",
            "swagger_ui_redirect",
            "redoc_html",
        }:
            continue
        endpoint_name = (
            f"{getattr(route.endpoint, '__module__', '?')}."
            f"{getattr(route.endpoint, '__qualname__', repr(route.endpoint))}"
        )
        methods = ",".join(sorted(route.methods)) or "ANY"
        routes_by_name[route.name].append(
            f"{methods} {route.path} -> {endpoint_name}"
        )

    duplicates = {
        name: registrations
        for name, registrations in routes_by_name.items()
        if len(registrations) > 1
    }
    assert duplicates == {}, f"duplicate route names make reverse routing ambiguous: {duplicates}"


@pytest.mark.asyncio
async def test_root_redirect_target_is_registered():
    root_route = _route_by_path("/")
    response = root_route.endpoint()
    if inspect.isawaitable(response):
        response = await response

    location = response.headers.get("location")
    assert location and location.startswith("/"), "root must redirect to an internal route"
    _route_by_path(location)
