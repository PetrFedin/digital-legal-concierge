from __future__ import annotations

import ast
import inspect
import re
import textwrap
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, get_origin, get_type_hints

from fastapi.params import Depends as DependsMarker
from fastapi.routing import iter_route_contexts
from pydantic import BaseModel
from starlette.routing import compile_path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DYNAMIC_TOKEN = "1"


@dataclass(frozen=True)
class JavascriptFragment:
    source: str
    kind: str
    text: str


@dataclass(frozen=True)
class PayloadCall:
    source: str
    kind: str
    method: str
    path: str
    expression: str
    keys: frozenset[str]
    has_spread: bool


@dataclass(frozen=True)
class BodyContract:
    allowed: frozenset[str]
    required: frozenset[str]
    allow_unknown: bool
    origin: str


@dataclass(frozen=True)
class RouteContract:
    path: str
    method: str
    pattern: re.Pattern[str]
    endpoint: Any
    body: BodyContract | None


class JavascriptContextParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.fragments: list[JavascriptFragment] = []
        self._script_depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name.lower(): value or "" for name, value in attrs}
        if tag.lower() == "script":
            self._script_depth += 1
            if self._script_depth == 1:
                self._parts = []
        for name, value in values.items():
            if name.startswith("on") and value.strip():
                self.fragments.append(
                    JavascriptFragment(
                        source=self.source,
                        kind=f"inline-{name}",
                        text=value,
                    )
                )

    def handle_data(self, data: str) -> None:
        if self._script_depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._script_depth:
            return
        self._script_depth -= 1
        if self._script_depth == 0:
            script = "".join(self._parts).strip()
            if script:
                self.fragments.append(
                    JavascriptFragment(
                        source=self.source,
                        kind="script",
                        text=script,
                    )
                )
            self._parts = []


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


def _skip_string(text: str, start: int, quote: str) -> int:
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
    end = text.find("\n", start + 2)
    return len(text) if end < 0 else end


def _skip_block_comment(text: str, start: int) -> int:
    end = text.find("*/", start + 2)
    return len(text) if end < 0 else end + 2


def _skip_template_expression(text: str, start: int) -> int:
    depth = 1
    index = start
    while index < len(text) and depth:
        char = text[index]
        if char in {"'", '"'}:
            index = _skip_string(text, index, char)
        elif char == "`":
            index = _skip_template(text, index)
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


def _skip_template(text: str, start: int) -> int:
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
    masked = list(text)
    index = 0
    while index < len(text):
        end = index
        char = text[index]
        if char in {"'", '"'}:
            end = _skip_string(text, index, char)
        elif char == "`":
            end = _skip_template(text, index)
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


def _balanced(text: str, start: int, opener: str, closer: str) -> tuple[str, int] | None:
    if start >= len(text) or text[start] != opener:
        return None
    depth = 1
    index = start + 1
    while index < len(text):
        char = text[index]
        if char in {"'", '"'}:
            index = _skip_string(text, index, char)
            continue
        if char == "`":
            index = _skip_template(text, index)
            continue
        if text.startswith("//", index):
            index = _skip_line_comment(text, index)
            continue
        if text.startswith("/*", index):
            index = _skip_block_comment(text, index)
            continue
        if char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return text[start + 1 : index], index + 1
        index += 1
    return None


def _split_top_level(text: str, delimiter: str = ",") -> list[str]:
    parts: list[str] = []
    start = 0
    index = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    while index < len(text):
        char = text[index]
        if char in {"'", '"'}:
            index = _skip_string(text, index, char)
            continue
        if char == "`":
            index = _skip_template(text, index)
            continue
        if text.startswith("//", index):
            index = _skip_line_comment(text, index)
            continue
        if text.startswith("/*", index):
            index = _skip_block_comment(text, index)
            continue
        if char in depths:
            depths[char] += 1
        elif char in closing and depths[closing[char]]:
            depths[closing[char]] -= 1
        elif char == delimiter and not any(depths.values()):
            parts.append(text[start:index].strip())
            start = index + 1
        index += 1
    parts.append(text[start:].strip())
    return parts


def _replace_template_expressions(content: str) -> str | None:
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
        if end <= marker + 2:
            return None
        output.append(DYNAMIC_TOKEN)
        dynamic = True
        index = end
    return "".join(output) if dynamic else content


def _quoted_value(expression: str) -> str | None:
    expression = expression.strip()
    if len(expression) < 2 or expression[0] not in {"'", '"'}:
        return None
    end = _skip_string(expression, 0, expression[0])
    if end != len(expression):
        return None
    return expression[1:-1]


def _sample_path(expression: str) -> str | None:
    expression = expression.strip()
    quoted = _quoted_value(expression)
    if quoted is not None:
        return quoted if quoted.startswith("/") and not quoted.startswith("//") else None

    if expression.startswith("`") and _skip_template(expression, 0) == len(expression):
        normalized = _replace_template_expressions(expression[1:-1])
        if normalized and normalized.startswith("/") and not normalized.startswith("//"):
            return normalized
        return None

    parts = _split_top_level(expression, "+")
    if len(parts) < 2:
        return None
    output: list[str] = []
    for part in parts:
        literal = _quoted_value(part)
        if literal is None:
            output.append(DYNAMIC_TOKEN)
        else:
            output.append(literal)
    candidate = "".join(output)
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    if any(char.isspace() or char in {'<', '>', '"', "'", "`"} for char in candidate):
        return None
    return candidate


def _find_top_level_colon(property_text: str) -> int | None:
    index = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    while index < len(property_text):
        char = property_text[index]
        if char in {"'", '"'}:
            index = _skip_string(property_text, index, char)
            continue
        if char == "`":
            index = _skip_template(property_text, index)
            continue
        if char in depths:
            depths[char] += 1
        elif char in closing and depths[closing[char]]:
            depths[closing[char]] -= 1
        elif char == ":" and not any(depths.values()):
            return index
        index += 1
    return None


def _object_keys(object_expression: str) -> tuple[frozenset[str], bool] | None:
    expression = object_expression.strip()
    if not expression.startswith("{"):
        return None
    balanced = _balanced(expression, 0, "{", "}")
    if balanced is None or expression[balanced[1] :].strip():
        return None
    content, _ = balanced
    keys: set[str] = set()
    has_spread = False
    for item in _split_top_level(content):
        item = item.strip()
        if not item:
            continue
        if item.startswith("..."):
            has_spread = True
            continue
        colon = _find_top_level_colon(item)
        key_expression = item if colon is None else item[:colon].strip()
        if key_expression.startswith("["):
            has_spread = True
            continue
        quoted = _quoted_value(key_expression)
        if quoted is not None:
            keys.add(quoted)
            continue
        match = re.fullmatch(r"[A-Za-z_$][\w$]*", key_expression)
        if match:
            keys.add(key_expression)
            continue
        has_spread = True
    return frozenset(keys), has_spread


def _json_body(options: str) -> tuple[frozenset[str], bool] | None:
    masked = _mask_non_executable_javascript(options)
    match = re.search(r"\bbody\s*:\s*JSON\.stringify\s*\(", masked)
    if match:
        open_parenthesis = masked.find("(", match.start())
        call = _balanced(options, open_parenthesis, "(", ")")
        if call is None:
            return None
        arguments, _ = call
        first_argument = _split_top_level(arguments)[0]
        return _object_keys(first_argument)

    empty_match = re.search(
        r"\bbody\s*:\s*(?P<q>['\"])\s*\{\s*\}\s*(?P=q)",
        options,
    )
    if empty_match:
        return frozenset(), False
    return None


def _nested_template_handlers(fragment: JavascriptFragment) -> list[JavascriptFragment]:
    handlers: list[JavascriptFragment] = []
    normalized = fragment.text.replace('\\"', '"').replace("\\'", "'")
    for match in re.finditer(
        r"\bon(?P<event>[a-z]+)\s*=\s*(?P<q>['\"])(?P<body>.*?)(?P=q)",
        normalized,
        re.IGNORECASE | re.DOTALL,
    ):
        body = match.group("body").strip()
        if body:
            handlers.append(
                JavascriptFragment(
                    source=fragment.source,
                    kind=f"template-on{match.group('event').lower()}",
                    text=body,
                )
            )
    return handlers


def _javascript_fragments() -> list[JavascriptFragment]:
    fragments: list[JavascriptFragment] = []
    seen: set[tuple[str, str, str]] = set()
    for source, text in _html_documents():
        parser = JavascriptContextParser(source)
        parser.feed(text)
        queue = list(parser.fragments)
        while queue:
            fragment = queue.pop()
            key = (fragment.source, fragment.kind, fragment.text)
            if key in seen:
                continue
            seen.add(key)
            fragments.append(fragment)
            queue.extend(_nested_template_handlers(fragment))
    return fragments


def _payload_calls() -> list[PayloadCall]:
    calls: list[PayloadCall] = []
    seen: set[tuple[str, str, str, frozenset[str]]] = set()
    pattern = re.compile(r"(?<![\w$])(?P<callee>fetch|api)\s*\(", re.IGNORECASE)

    for fragment in _javascript_fragments():
        masked = _mask_non_executable_javascript(fragment.text)
        for match in pattern.finditer(masked):
            open_parenthesis = masked.find("(", match.start())
            call = _balanced(fragment.text, open_parenthesis, "(", ")")
            if call is None:
                continue
            arguments_text, _ = call
            arguments = _split_top_level(arguments_text)
            if not arguments:
                continue
            path = _sample_path(arguments[0])
            if path is None:
                continue
            options = arguments[1] if len(arguments) > 1 else ""
            method_match = re.search(
                r"\bmethod\s*:\s*['\"](?P<method>POST|PUT|PATCH|DELETE)['\"]",
                options,
                re.IGNORECASE,
            )
            if not method_match:
                continue
            method = method_match.group("method").upper()
            payload = _json_body(options)
            if payload is None:
                continue
            keys, has_spread = payload
            call_record = PayloadCall(
                source=fragment.source,
                kind=f"{fragment.kind}-{match.group('callee').lower()}",
                method=method,
                path=path,
                expression=arguments[0].strip(),
                keys=keys,
                has_spread=has_spread,
            )
            key = (method, path, fragment.kind, keys)
            if key not in seen:
                seen.add(key)
                calls.append(call_record)
    return calls


def _dict_body_contract(endpoint: Any, parameter_name: str) -> BodyContract | None:
    try:
        source = textwrap.dedent(inspect.getsource(endpoint))
        tree = ast.parse(source)
    except (OSError, TypeError, SyntaxError):
        return None

    allowed: set[str] = set()
    required: set[str] = set()
    forwards_whole_payload = False

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == parameter_name
        ):
            key_node = node.slice
            if isinstance(key_node, ast.Constant) and isinstance(key_node.value, str):
                allowed.add(key_node.value)
                required.add(key_node.value)
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == parameter_name
            and node.func.attr == "get"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            allowed.add(node.args[0].value)
        elif isinstance(node, ast.Call):
            if any(
                isinstance(argument, ast.Name) and argument.id == parameter_name
                for argument in node.args
            ):
                forwards_whole_payload = True
            if any(
                keyword.arg is None
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == parameter_name
                for keyword in node.keywords
            ):
                forwards_whole_payload = True

    if not allowed and not required:
        return None
    return BodyContract(
        allowed=frozenset(allowed),
        required=frozenset(required),
        allow_unknown=forwards_whole_payload,
        origin=f"dict parameter {parameter_name}",
    )


def _body_contract(endpoint: Any) -> BodyContract | None:
    signature = inspect.signature(endpoint)
    try:
        hints = get_type_hints(endpoint)
    except Exception:
        hints = {}

    for parameter in signature.parameters.values():
        if isinstance(parameter.default, DependsMarker):
            continue
        annotation = hints.get(parameter.name, parameter.annotation)
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            fields = annotation.model_fields
            extra = annotation.model_config.get("extra")
            return BodyContract(
                allowed=frozenset(fields),
                required=frozenset(
                    name for name, field in fields.items() if field.is_required()
                ),
                allow_unknown=extra == "allow",
                origin=f"Pydantic model {annotation.__name__}",
            )
        if annotation is dict or get_origin(annotation) is dict:
            contract = _dict_body_contract(endpoint, parameter.name)
            if contract is not None:
                return contract
    return None


def _route_contracts() -> list[RouteContract]:
    routes: list[RouteContract] = []
    for context in iter_route_contexts(app.routes):
        if not context.path or context.endpoint is None:
            continue
        methods = frozenset(context.methods or ())
        for method in methods.intersection(WRITE_METHODS):
            pattern, _, _ = compile_path(context.path)
            routes.append(
                RouteContract(
                    path=context.path,
                    method=method,
                    pattern=pattern,
                    endpoint=context.endpoint,
                    body=_body_contract(context.endpoint),
                )
            )
    return routes


def test_javascript_json_payload_keys_match_backend_body_contracts():
    calls = _payload_calls()
    routes = _route_contracts()

    missing_routes: list[str] = []
    ambiguous_routes: list[str] = []
    missing_required: list[str] = []
    unknown_fields: list[str] = []
    verified = 0

    for call in calls:
        matches = [
            route
            for route in routes
            if route.method == call.method and route.pattern.fullmatch(call.path)
        ]
        description = (
            f"{call.source}: {call.kind} {call.method} {call.path} "
            f"keys={sorted(call.keys)}"
        )
        if not matches:
            missing_routes.append(description)
            continue
        if len(matches) > 1:
            ambiguous_routes.append(
                f"{description} matches {[route.path for route in matches]}"
            )
            continue

        contract = matches[0].body
        if contract is None:
            continue
        verified += 1

        missing = contract.required - call.keys
        if missing and not call.has_spread:
            missing_required.append(
                f"{description}: missing {sorted(missing)} required by {contract.origin}"
            )

        unknown = call.keys - contract.allowed
        if unknown and not contract.allow_unknown:
            unknown_fields.append(
                f"{description}: unknown {sorted(unknown)} for {contract.origin}; "
                f"allowed={sorted(contract.allowed)}"
            )

    assert len(calls) >= 8, (
        "JavaScript JSON payload discovery is unexpectedly shallow: "
        f"{len(calls)} calls"
    )
    assert verified >= 5, (
        "backend payload verification is unexpectedly shallow: "
        f"{verified} typed or inferred contracts for {len(calls)} calls"
    )
    assert missing_routes == [], "write payloads without matching routes:\n" + "\n".join(
        missing_routes
    )
    assert ambiguous_routes == [], "write payloads match multiple routes:\n" + "\n".join(
        ambiguous_routes
    )
    assert missing_required == [], "write payloads omit required fields:\n" + "\n".join(
        missing_required
    )
    assert unknown_fields == [], "write payloads contain unknown fields:\n" + "\n".join(
        unknown_fields
    )
