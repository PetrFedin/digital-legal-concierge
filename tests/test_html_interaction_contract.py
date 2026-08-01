from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.routing import iter_route_contexts
from starlette.routing import compile_path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}
DYNAMIC_MARKERS = ("${", "{", "}", "__ID__")


@dataclass(frozen=True)
class Interaction:
    source: str
    kind: str
    method: str
    target: str


@dataclass(frozen=True)
class RegisteredRoute:
    path: str
    methods: frozenset[str]
    pattern: re.Pattern[str]


@dataclass
class ParsedHtml:
    interactions: list[tuple[str, str, str]] = field(default_factory=list)
    handlers: list[str] = field(default_factory=list)


class InteractionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParsedHtml()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        tag = tag.lower()

        if tag in {"a", "area"} and values.get("href"):
            self.result.interactions.append(("link", "GET", values["href"]))
        elif tag == "form" and values.get("action"):
            method = values.get("method", "GET").upper()
            self.result.interactions.append(("form", method, values["action"]))
        elif tag in {"button", "input"} and values.get("formaction"):
            method = values.get("formmethod", "GET").upper()
            self.result.interactions.append(("form-control", method, values["formaction"]))
        elif tag in {"script", "img", "link", "source"}:
            target = values.get("src") or values.get("href")
            if target:
                self.result.interactions.append(("asset", "GET", target))

        for name, value in values.items():
            if name.startswith("on") and value:
                self.result.handlers.append(value)


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
        for node in ast.walk(tree):
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


def _registered_routes() -> list[RegisteredRoute]:
    routes: list[RegisteredRoute] = []
    for context in iter_route_contexts(app.routes):
        if not context.path or context.endpoint is None:
            continue
        pattern, _, _ = compile_path(context.path)
        routes.append(
            RegisteredRoute(
                path=context.path,
                methods=frozenset(context.methods or ()),
                pattern=pattern,
            )
        )
    return routes


def _static_internal_target(target: str) -> str | None:
    target = target.strip()
    if not target.startswith("/") or target.startswith("//"):
        return None
    if any(marker in target for marker in DYNAMIC_MARKERS):
        return None
    path = urlsplit(target).path
    return path or "/"


def _is_concatenated(text: str, end: int) -> bool:
    return text[end : end + 12].lstrip().startswith("+")


def _javascript_interactions(text: str) -> list[tuple[str, str, str]]:
    interactions: list[tuple[str, str, str]] = []

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
            if not _is_concatenated(text, match.end()):
                interactions.append(("javascript-navigation", "GET", match.group("path")))

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
        interactions.append((f"javascript-{match.group('callee').lower()}", method, match.group("path")))

    return interactions


def _inline_handler_sources(text: str) -> list[str]:
    handlers = [
        match.group("body")
        for match in re.finditer(
            r"on[a-z]+\s*=\s*(?P<q>['\"])(?P<body>.*?)(?P=q)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    ]
    handlers.extend(
        match.group("body")
        for match in re.finditer(
            r"on[a-z]+\s*=\s*\\\"(?P<body>.*?)\\\"",
            text,
            re.IGNORECASE | re.DOTALL,
        )
    )
    return handlers


def _defined_javascript_functions(text: str) -> set[str]:
    names = set(
        re.findall(
            r"(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(",
            text,
        )
    )
    names.update(
        re.findall(
            r"(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>",
            text,
        )
    )
    return names


def _called_handler_functions(handler: str) -> set[str]:
    ignored = {
        "alert",
        "Boolean",
        "confirm",
        "Date",
        "decodeURIComponent",
        "encodeURIComponent",
        "fetch",
        "FormData",
        "isNaN",
        "JSON",
        "Number",
        "Object",
        "parseFloat",
        "parseInt",
        "prompt",
        "setTimeout",
        "String",
        "URL",
        "URLSearchParams",
    }
    return {
        name
        for name in re.findall(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", handler)
        if name not in ignored
    }


def _all_interactions() -> tuple[list[Interaction], dict[str, list[str]]]:
    interactions: list[Interaction] = []
    missing_functions: dict[str, list[str]] = {}

    for source, text in _html_documents():
        parser = InteractionParser()
        parser.feed(text)
        raw_interactions = parser.result.interactions + _javascript_interactions(text)
        interactions.extend(
            Interaction(source=source, kind=kind, method=method, target=target)
            for kind, method, target in raw_interactions
        )

        handlers = parser.result.handlers + _inline_handler_sources(text)
        defined = _defined_javascript_functions(text)
        missing = sorted(
            {
                function_name
                for handler in handlers
                for function_name in _called_handler_functions(handler)
                if function_name not in defined
            }
        )
        if missing:
            missing_functions[source] = missing

    return interactions, missing_functions


def test_static_html_interactions_resolve_to_registered_http_handlers():
    routes = _registered_routes()
    interactions, _ = _all_interactions()
    checked = 0
    missing: list[str] = []
    invalid_methods: list[str] = []

    for interaction in interactions:
        method = interaction.method.upper()
        if method not in HTTP_METHODS:
            invalid_methods.append(
                f"{interaction.source}: {interaction.kind} uses invalid method {method}"
            )
            continue
        target = _static_internal_target(interaction.target)
        if target is None:
            continue
        checked += 1
        matches = [
            route
            for route in routes
            if method in route.methods and route.pattern.fullmatch(target)
        ]
        if not matches:
            missing.append(
                f"{interaction.source}: {interaction.kind} {method} {interaction.target}"
            )

    assert checked >= 25, f"HTML interaction audit is unexpectedly shallow: {checked} targets"
    assert invalid_methods == [], "invalid HTML form/control methods:\n" + "\n".join(invalid_methods)
    assert missing == [], "HTML interactions without matching handlers:\n" + "\n".join(missing)


def test_inline_html_event_handlers_reference_defined_javascript_functions():
    documents = _html_documents()
    _, missing_functions = _all_interactions()

    assert len(documents) >= 10, f"HTML source discovery is unexpectedly shallow: {len(documents)}"
    assert missing_functions == {}, (
        "HTML event handlers reference missing JavaScript functions: "
        f"{missing_functions}"
    )
