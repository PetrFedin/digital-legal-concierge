from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi.routing import iter_route_contexts
from starlette.routing import compile_path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
DYNAMIC_MARKERS = ("${", "{", "}", "__ID__")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
AUTH_STATUSES = {401, 403}


@dataclass(frozen=True)
class GetInteraction:
    source: str
    kind: str
    target: str


@dataclass(frozen=True)
class GetRoute:
    path: str
    pattern: object


@dataclass
class ParsedHtml:
    interactions: list[tuple[str, str, str]] = field(default_factory=list)


class GetInteractionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParsedHtml()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()

        if tag in {"a", "area"} and values.get("href"):
            self.result.interactions.append(("link", "GET", values["href"]))
        elif tag == "form" and values.get("action"):
            self.result.interactions.append(
                ("form", values.get("method", "GET").upper(), values["action"])
            )
        elif tag in {"button", "input"} and values.get("formaction"):
            self.result.interactions.append(
                (
                    "form-control",
                    values.get("formmethod", "GET").upper(),
                    values["formaction"],
                )
            )


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{dynamic}")
        return "".join(parts)
    return None


def _html_documents() -> list[tuple[str, str]]:
    documents: list[tuple[str, str]] = []
    seen: set[str] = set()
    for path in sorted(API_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(
                parents.get(node), ast.JoinedStr
            ):
                continue
            text = _literal_text(node)
            if not text:
                continue
            lowered = text.lower()
            if "<!doctype html" not in lowered and "<html" not in lowered:
                continue
            digest = sha256(text.encode("utf-8")).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            documents.append((str(path.relative_to(ROOT)), text))
    return documents


def _javascript_get_interactions(text: str) -> list[tuple[str, str, str]]:
    interactions: list[tuple[str, str, str]] = []

    call_pattern = re.compile(
        r"(?P<callee>fetch|api)\s*\(\s*(?P<q>['\"])(?P<path>/[^'\"]*)(?P=q)(?P<tail>[^)]{0,320})\)",
        re.IGNORECASE,
    )
    method_pattern = re.compile(
        r"method\s*:\s*['\"](?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]",
        re.IGNORECASE,
    )
    for match in call_pattern.finditer(text):
        tail = match.group("tail")
        if tail.lstrip().startswith("+"):
            continue
        method_match = method_pattern.search(tail)
        method = method_match.group("method").upper() if method_match else "GET"
        interactions.append(
            (f"javascript-{match.group('callee').lower()}", method, match.group("path"))
        )

    navigation_patterns = [
        re.compile(
            r"(?:window\.)?location(?:\.href)?\s*=\s*(?P<q>['\"])(?P<path>/[^'\"]*)(?P=q)"
        ),
        re.compile(
            r"location\.assign\(\s*(?P<q>['\"])(?P<path>/[^'\"]*)(?P=q)"
        ),
        re.compile(
            r"window\.open\(\s*(?P<q>['\"])(?P<path>/[^'\"]*)(?P=q)"
        ),
    ]
    for pattern in navigation_patterns:
        for match in pattern.finditer(text):
            suffix = text[match.end() : match.end() + 12].lstrip()
            if not suffix.startswith("+"):
                interactions.append(
                    ("javascript-navigation", "GET", match.group("path"))
                )

    return interactions


def _static_internal_target(target: str) -> str | None:
    target = target.strip()
    if not target.startswith("/") or target.startswith("//"):
        return None
    if any(marker in target for marker in DYNAMIC_MARKERS):
        return None
    parsed = urlsplit(target)
    if not parsed.path:
        return None
    return target


def _get_interactions() -> list[GetInteraction]:
    unique: dict[tuple[str, str], GetInteraction] = {}
    for source, text in _html_documents():
        parser = GetInteractionParser()
        parser.feed(text)
        candidates = parser.result.interactions + _javascript_get_interactions(text)
        for kind, method, target in candidates:
            if method != "GET":
                continue
            normalized = _static_internal_target(target)
            if normalized is None:
                continue
            key = (kind, normalized)
            unique.setdefault(
                key,
                GetInteraction(source=source, kind=kind, target=normalized),
            )
    return sorted(unique.values(), key=lambda item: (item.target, item.kind))


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


def _registered_get_target(location: str, routes: list[GetRoute]) -> bool:
    parsed = urlsplit(location)
    if parsed.scheme or parsed.netloc:
        if parsed.scheme not in {"http", "https"} or parsed.netloc != "testserver":
            return False
    path = parsed.path or "/"
    return any(route.pattern.fullmatch(path) for route in routes)


def _response_error_text(response: httpx.Response) -> str | None:
    body = response.text.strip()
    lowered = body.lower()
    if "internal server error" in lowered or "traceback (most recent call last)" in lowered:
        return "response exposes an internal server failure"

    content_type = response.headers.get("content-type", "").lower()
    if "application/json" in content_type:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return "declares application/json but body is invalid JSON"
        if isinstance(payload, dict):
            detail = str(payload.get("detail", "")).lower()
            if "internal server error" in detail or "traceback" in detail:
                return "JSON detail exposes an internal server failure"
    elif response.status_code != 204 and not response.content.strip():
        return "successful response is empty"
    return None


@pytest.mark.asyncio
async def test_static_get_interactions_execute_without_application_failures():
    interactions = _get_interactions()
    routes = _get_routes()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)

    request_failures: list[str] = []
    invalid_statuses: list[str] = []
    invalid_redirects: list[str] = []
    invalid_responses: list[str] = []

    for interaction in interactions:
        try:
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                follow_redirects=False,
                timeout=10.0,
            ) as client:
                response = await client.get(
                    interaction.target,
                    headers={
                        "accept": "application/json,text/html;q=0.9,*/*;q=0.8",
                    },
                )
        except Exception as exc:  # pragma: no cover - diagnostic guard
            request_failures.append(
                f"{interaction.source}: {interaction.kind} GET {interaction.target}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue

        status = response.status_code
        description = (
            f"{interaction.source}: {interaction.kind} GET {interaction.target}"
        )

        if 200 <= status < 300:
            error = _response_error_text(response)
            if error:
                invalid_responses.append(f"{description}: {error}")
            continue

        if status in REDIRECT_STATUSES:
            location = response.headers.get("location", "").strip()
            if not location:
                invalid_redirects.append(f"{description}: {status} without Location")
            elif not _registered_get_target(location, routes):
                invalid_redirects.append(
                    f"{description}: {status} points to unregistered or external target {location!r}"
                )
            continue

        if status in AUTH_STATUSES:
            if not response.content.strip():
                invalid_responses.append(
                    f"{description}: authorization failure {status} is empty"
                )
            continue

        invalid_statuses.append(
            f"{description}: unexpected {status}; body={response.text[:240]!r}"
        )

    assert len(interactions) >= 25, (
        "runtime GET interaction audit is unexpectedly shallow: "
        f"{len(interactions)} interactions"
    )
    assert request_failures == [], "GET interactions raised exceptions:\n" + "\n".join(
        request_failures
    )
    assert invalid_statuses == [], "GET interactions returned invalid statuses:\n" + "\n".join(
        invalid_statuses
    )
    assert invalid_redirects == [], "GET interaction redirects are broken:\n" + "\n".join(
        invalid_redirects
    )
    assert invalid_responses == [], "GET interaction responses are invalid:\n" + "\n".join(
        invalid_responses
    )
