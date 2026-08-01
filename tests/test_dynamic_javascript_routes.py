from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

from fastapi.routing import iter_route_contexts
from starlette.routing import compile_path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
DYNAMIC_TOKEN = "{dynamic}"
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"}


@dataclass(frozen=True)
class DynamicInteraction:
    source: str
    kind: str
    method: str
    expression: str
    path_template: str
    sample_path: str


@dataclass(frozen=True)
class RegisteredRoute:
    path: str
    methods: frozenset[str]
    pattern: re.Pattern[str]


@dataclass(frozen=True)
class JavascriptFragment:
    kind: str
    text: str


class JavascriptContextParser(HTMLParser):
    """Extract executable scripts and inline handlers from an HTML document."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.fragments: list[JavascriptFragment] = []
        self._script_depth = 0
        self._script_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "script":
            self._script_depth += 1
            if self._script_depth == 1:
                self._script_parts = []
        for name, value in values.items():
            if name.startswith("on") and value.strip():
                self.fragments.append(
                    JavascriptFragment(kind=f"inline-{name}", text=value)
                )

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._script_depth:
            return
        self._script_depth -= 1
        if self._script_depth == 0:
            script = "".join(self._script_parts).strip()
            if script:
                self.fragments.append(JavascriptFragment(kind="script", text=script))
            self._script_parts = []


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append(DYNAMIC_TOKEN)
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


def _skip_simple_string(text: str, start: int, quote: str) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == quote:
            return index + 1
        else:
            index += 1
    return len(text)


def _skip_line_comment(text: str, start: int) -> int:
    newline = text.find("\n", start + 2)
    return len(text) if newline < 0 else newline


def _skip_block_comment(text: str, start: int) -> int:
    end = text.find("*/", start + 2)
    return len(text) if end < 0 else end + 2


def _skip_template_expression(text: str, start: int) -> int:
    depth = 1
    index = start
    while index < len(text) and depth:
        char = text[index]
        if char in {"'", '"'}:
            index = _skip_simple_string(text, index, char)
        elif char == "`":
            index = _skip_template_literal(text, index)
        elif text.startswith("//", index):
            index = _skip_line_comment(text, index)
        elif text.startswith("/*", index):
            index = _skip_block_comment(text, index)
        elif char == "{":
            depth += 1
            index += 1
        elif char == "}":
            depth -= 1
            index += 1
        else:
            index += 1
    return index


def _skip_template_literal(text: str, start: int) -> int:
    index = start + 1
    while index < len(text):
        if text[index] == "\\":
            index += 2
        elif text[index] == "`":
            return index + 1
        elif text.startswith("${", index):
            index = _skip_template_expression(text, index + 2)
        else:
            index += 1
    return len(text)


def _mask_non_executable_javascript(text: str) -> str:
    """Mask strings, templates and comments while preserving source offsets."""
    masked = list(text)
    index = 0
    while index < len(text):
        end = index
        char = text[index]
        if char in {"'", '"'}:
            end = _skip_simple_string(text, index, char)
        elif char == "`":
            end = _skip_template_literal(text, index)
        elif text.startswith("//", index):
            end = _skip_line_comment(text, index)
        elif text.startswith("/*", index):
            end = _skip_block_comment(text, index)

        if end > index:
            for position in range(index, end):
                if masked[position] not in {"\n", "\r"}:
                    masked[position] = " "
            index = end
        else:
            index += 1
    return "".join(masked)


def _read_quoted(expression: str, start: int) -> tuple[str, int] | None:
    quote = expression[start]
    if quote not in {"'", '"'}:
        return None
    chars: list[str] = []
    escaped = False
    index = start + 1
    while index < len(expression):
        char = expression[index]
        if escaped:
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == quote:
            return "".join(chars), index + 1
        else:
            chars.append(char)
        index += 1
    return None


def _replace_template_expressions(content: str) -> tuple[str, bool]:
    output: list[str] = []
    index = 0
    dynamic = False
    while index < len(content):
        marker = content.find("${", index)
        if marker < 0:
            output.append(content[index:])
            break
        output.append(content[index:marker])
        end = _skip_template_expression(content, marker + 2)
        if end <= marker + 2 or end > len(content):
            return content, False
        output.append(DYNAMIC_TOKEN)
        dynamic = True
        index = end
    return "".join(output), dynamic


def _consume_dynamic_term(expression: str, start: int) -> int:
    index = start
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    escaped = False
    while index < len(expression):
        char = expression[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in pairs and depths[pairs[char]]:
            depths[pairs[char]] -= 1
        elif char == "+" and not any(depths.values()):
            break
        index += 1
    return index


def _valid_path_template(path_template: str) -> bool:
    if not path_template.startswith("/") or path_template.startswith("//"):
        return False
    if DYNAMIC_TOKEN not in path_template:
        return False
    return not any(
        char.isspace() or char in {'<', '>', '"', "'", "`", "\\"}
        for char in path_template
    )


def _normalize_dynamic_expression(expression: str) -> str | None:
    expression = expression.strip()
    if not expression:
        return None

    if expression.startswith("`"):
        if not expression.endswith("`"):
            return None
        normalized, dynamic = _replace_template_expressions(expression[1:-1])
        return normalized if dynamic and _valid_path_template(normalized) else None

    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(expression):
        while index < len(expression) and (
            expression[index].isspace() or expression[index] == "+"
        ):
            index += 1
        if index >= len(expression):
            break
        if expression[index] in {"'", '"'}:
            quoted = _read_quoted(expression, index)
            if quoted is None:
                return None
            value, index = quoted
            tokens.append(("literal", value))
            continue
        end = _consume_dynamic_term(expression, index)
        term = expression[index:end].strip()
        if term:
            tokens.append(("dynamic", term))
        index = end

    if not tokens or tokens[0][0] != "literal":
        return None
    normalized: list[str] = []
    for kind, value in tokens:
        if kind == "literal":
            normalized.append(value)
        elif not normalized or normalized[-1] != DYNAMIC_TOKEN:
            normalized.append(DYNAMIC_TOKEN)
    result = "".join(normalized)
    return result if _valid_path_template(result) else None


def _sample_path(path_template: str) -> str | None:
    candidate = path_template.replace(DYNAMIC_TOKEN, "1")
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    return urlsplit(candidate).path or "/"


def _balanced_call(text: str, open_parenthesis: int) -> tuple[str, int] | None:
    depth = 1
    index = open_parenthesis + 1
    quote: str | None = None
    escaped = False
    while index < len(text):
        char = text[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[open_parenthesis + 1 : index], index + 1
        index += 1
    return None


def _first_argument(call_body: str) -> str:
    index = 0
    depths = {"(": 0, "[": 0, "{": 0}
    pairs = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    escaped = False
    while index < len(call_body):
        char = call_body[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"', "`"}:
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in pairs and depths[pairs[char]]:
            depths[pairs[char]] -= 1
        elif char == "," and not any(depths.values()):
            return call_body[:index].strip()
        index += 1
    return call_body.strip()


def _method_from_call(call_body: str, default: str = "GET") -> str:
    match = re.search(
        r"\bmethod\s*:\s*['\"](?P<method>GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)['\"]",
        call_body,
        re.IGNORECASE,
    )
    return match.group("method").upper() if match else default


def _call_interactions(
    source: str, fragment: JavascriptFragment
) -> list[DynamicInteraction]:
    interactions: list[DynamicInteraction] = []
    text = fragment.text
    masked = _mask_non_executable_javascript(text)
    pattern = re.compile(
        r"(?<![\w$])(?P<callee>fetch|api|location\.assign|window\.open)\s*\(",
        re.IGNORECASE,
    )
    for match in pattern.finditer(masked):
        open_parenthesis = masked.find("(", match.start())
        call = _balanced_call(text, open_parenthesis)
        if call is None:
            continue
        call_body, _ = call
        expression = _first_argument(call_body)
        path_template = _normalize_dynamic_expression(expression)
        if path_template is None:
            continue
        sample_path = _sample_path(path_template)
        if sample_path is None:
            continue
        callee = match.group("callee").lower()
        method = (
            _method_from_call(call_body) if callee in {"fetch", "api"} else "GET"
        )
        interactions.append(
            DynamicInteraction(
                source=source,
                kind=f"{fragment.kind}-{callee}",
                method=method,
                expression=expression,
                path_template=path_template,
                sample_path=sample_path,
            )
        )
    return interactions


def _assignment_interactions(
    source: str, fragment: JavascriptFragment
) -> list[DynamicInteraction]:
    interactions: list[DynamicInteraction] = []
    text = fragment.text
    masked = _mask_non_executable_javascript(text)
    pattern = re.compile(
        r"(?:window\.)?location(?:\.href)?\s*=\s*",
        re.IGNORECASE,
    )
    for match in pattern.finditer(masked):
        cursor = match.end()
        quote: str | None = None
        escaped = False
        depths = {"(": 0, "[": 0, "{": 0}
        pairs = {")": "(", "]": "[", "}": "{"}
        while cursor < len(text):
            char = text[cursor]
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                cursor += 1
                continue
            if char in {"'", '"', "`"}:
                quote = char
            elif char in depths:
                depths[char] += 1
            elif char in pairs and depths[pairs[char]]:
                depths[pairs[char]] -= 1
            elif char in {";", "\n"} and not any(depths.values()):
                break
            cursor += 1
        expression = text[match.end() : cursor].strip()
        path_template = _normalize_dynamic_expression(expression)
        if path_template is None:
            continue
        sample_path = _sample_path(path_template)
        if sample_path is None:
            continue
        interactions.append(
            DynamicInteraction(
                source=source,
                kind=f"{fragment.kind}-location",
                method="GET",
                expression=expression,
                path_template=path_template,
                sample_path=sample_path,
            )
        )
    return interactions


def _tag_attributes(attribute_text: str) -> dict[str, str]:
    normalized = attribute_text.replace('\\"', '"').replace("\\'", "'")
    return {
        match.group("name").lower(): match.group("value")
        for match in re.finditer(
            r"(?P<name>[A-Za-z_:][\w:.-]*)\s*=\s*(?P<q>['\"])(?P<value>.*?)(?P=q)",
            normalized,
            re.DOTALL,
        )
    }


def _markup_fragments(markup: str) -> tuple[list[JavascriptFragment], list[dict[str, str]]]:
    handlers: list[JavascriptFragment] = []
    tags: list[dict[str, str]] = []
    for match in re.finditer(
        r"<(?P<tag>[A-Za-z][\w:-]*)\b(?P<attrs>[^<>]*)>",
        markup,
        re.DOTALL,
    ):
        attrs = _tag_attributes(match.group("attrs"))
        attrs["__tag__"] = match.group("tag").lower()
        tags.append(attrs)
        for name, value in attrs.items():
            if name.startswith("on") and value.strip():
                handlers.append(
                    JavascriptFragment(kind=f"template-{name}", text=value)
                )
    return handlers, tags


def _markup_attribute_interactions(
    source: str, markup: str, *, kind_prefix: str
) -> list[DynamicInteraction]:
    interactions: list[DynamicInteraction] = []
    _, tags = _markup_fragments(markup)
    for attrs in tags:
        tag = attrs.get("__tag__", "")
        for attribute in ("href", "src", "action", "formaction"):
            expression = attrs.get(attribute, "").strip()
            if not expression.startswith("/"):
                continue
            if "${" in expression:
                path_template, dynamic = _replace_template_expressions(expression)
            else:
                path_template = expression
                dynamic = DYNAMIC_TOKEN in path_template
            if not dynamic or not _valid_path_template(path_template):
                continue
            sample_path = _sample_path(path_template)
            if sample_path is None:
                continue
            if attribute == "action" and tag == "form":
                method = attrs.get("method", "GET").upper()
            elif attribute == "formaction":
                method = attrs.get("formmethod", "GET").upper()
            else:
                method = "GET"
            interactions.append(
                DynamicInteraction(
                    source=source,
                    kind=f"{kind_prefix}-{attribute}",
                    method=method,
                    expression=expression,
                    path_template=path_template,
                    sample_path=sample_path,
                )
            )
    return interactions


def _dynamic_interactions() -> list[DynamicInteraction]:
    unique: dict[tuple[str, str, str, str], DynamicInteraction] = {}
    for source, text in _html_documents():
        parser = JavascriptContextParser()
        parser.feed(text)
        candidates = _markup_attribute_interactions(
            source, text, kind_prefix="html"
        )
        queue = list(parser.fragments)
        seen_fragments: set[tuple[str, str]] = set()
        while queue:
            fragment = queue.pop()
            fragment_key = (fragment.kind, fragment.text)
            if fragment_key in seen_fragments:
                continue
            seen_fragments.add(fragment_key)
            candidates.extend(_call_interactions(source, fragment))
            candidates.extend(_assignment_interactions(source, fragment))
            candidates.extend(
                _markup_attribute_interactions(
                    source,
                    fragment.text,
                    kind_prefix=f"{fragment.kind}-template",
                )
            )
            nested_handlers, _ = _markup_fragments(fragment.text)
            queue.extend(nested_handlers)

        for interaction in candidates:
            key = (
                interaction.source,
                interaction.kind,
                interaction.method,
                interaction.path_template,
            )
            unique[key] = interaction
    return list(unique.values())


def test_dynamic_javascript_urls_match_parameterized_fastapi_routes():
    routes = _registered_routes()
    interactions = _dynamic_interactions()
    missing: list[str] = []
    ambiguous: list[str] = []
    invalid_methods: list[str] = []

    for interaction in interactions:
        if interaction.method not in HTTP_METHODS:
            invalid_methods.append(
                f"{interaction.source}: {interaction.kind} uses {interaction.method}"
            )
            continue
        matches = [
            route
            for route in routes
            if interaction.method in route.methods
            and route.pattern.fullmatch(interaction.sample_path)
        ]
        description = (
            f"{interaction.source}: {interaction.kind} {interaction.method} "
            f"{interaction.path_template} (from {interaction.expression!r})"
        )
        if not matches:
            missing.append(description)
        elif len(matches) > 1:
            ambiguous.append(
                f"{description} matches {[route.path for route in matches]}"
            )

    assert len(interactions) >= 10, (
        "dynamic JavaScript route audit is unexpectedly shallow: "
        f"{len(interactions)} interactions"
    )
    assert invalid_methods == [], "invalid dynamic HTTP methods:\n" + "\n".join(
        invalid_methods
    )
    assert missing == [], "dynamic interactions without handlers:\n" + "\n".join(
        missing
    )
    assert ambiguous == [], "ambiguous dynamic route matches:\n" + "\n".join(
        ambiguous
    )
