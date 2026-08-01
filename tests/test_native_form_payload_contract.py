from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from starlette.routing import compile_path

from app.main import app


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
SUPPORTED_FORM_METHODS = {"GET", "POST"}
DEFAULT_FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
NON_DATA_INPUT_TYPES = {"button", "reset", "submit", "image"}
DYNAMIC_MARKERS = ("{dynamic}", "__ID__")
MIN_NATIVE_FORMS = 2


@dataclass(frozen=True)
class Control:
    tag: str
    attrs: dict[str, str]
    line: int


@dataclass
class NativeForm:
    source: str
    attrs: dict[str, str]
    line: int
    controls: list[Control] = field(default_factory=list)


@dataclass(frozen=True)
class OperationContract:
    route_path: str
    method: str
    operation: dict


class NativeFormParser(HTMLParser):
    def __init__(self, source: str) -> None:
        super().__init__(convert_charrefs=True)
        self.source = source
        self.forms: list[NativeForm] = []
        self._stack: list[NativeForm] = []

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.lower(): value or "" for name, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = self._attrs(attrs)
        line, _ = self.getpos()

        if tag == "form":
            form = NativeForm(source=self.source, attrs=values, line=line)
            self.forms.append(form)
            self._stack.append(form)
            return

        if tag in {"input", "select", "textarea", "button"} and self._stack:
            self._stack[-1].controls.append(
                Control(tag=tag, attrs=values, line=line)
            )

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._stack:
            self._stack.pop()


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


def _native_forms() -> list[NativeForm]:
    forms: list[NativeForm] = []
    for source, text in _html_documents():
        parser = NativeFormParser(source)
        parser.feed(text)
        forms.extend(
            form
            for form in parser.forms
            if form.attrs.get("action", "").strip()
        )
    return forms


def _replace_template_expressions(value: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(value):
        marker = value.find("${", index)
        if marker < 0:
            output.append(value[index:])
            break
        output.append(value[index:marker])
        depth = 1
        cursor = marker + 2
        quote: str | None = None
        escaped = False
        while cursor < len(value) and depth:
            char = value[cursor]
            if quote is not None:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in {"'", '"', "`"}:
                quote = char
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            cursor += 1
        if depth:
            return value
        output.append("1")
        index = cursor
    return "".join(output)


def _sample_action(action: str) -> str | None:
    action = action.strip()
    if not action.startswith("/") or action.startswith("//"):
        return None
    action = _replace_template_expressions(action)
    for marker in DYNAMIC_MARKERS:
        action = action.replace(marker, "1")
    if any(char in action for char in {'<', '>', '"', "'", "`"}):
        return None
    return action


def _openapi_operations() -> list[OperationContract]:
    schema = app.openapi()
    operations: list[OperationContract] = []
    for route_path, item in schema.get("paths", {}).items():
        if not isinstance(item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = item.get(method)
            if isinstance(operation, dict):
                operations.append(
                    OperationContract(
                        route_path=route_path,
                        method=method.upper(),
                        operation=operation,
                    )
                )
    return operations


def _match_operation(
    method: str,
    action_path: str,
    operations: list[OperationContract],
) -> list[OperationContract]:
    matches: list[OperationContract] = []
    for contract in operations:
        if contract.method != method:
            continue
        pattern, _, _ = compile_path(contract.route_path)
        if pattern.fullmatch(action_path):
            matches.append(contract)
    return matches


def _resolve_ref(schema: dict, document: dict) -> dict:
    current = schema
    seen: set[str] = set()
    while isinstance(current, dict) and "$ref" in current:
        reference = str(current["$ref"])
        if reference in seen or not reference.startswith("#/" ):
            break
        seen.add(reference)
        resolved: object = document
        for part in reference[2:].split("/"):
            if not isinstance(resolved, dict) or part not in resolved:
                return current
            resolved = resolved[part]
        if not isinstance(resolved, dict):
            return current
        current = resolved
    return current


def _merge_object_schema(schema: dict, document: dict) -> tuple[set[str], set[str], bool]:
    resolved = _resolve_ref(schema, document)
    properties: set[str] = set()
    required: set[str] = set()
    allow_unknown = resolved.get("additionalProperties") is True

    raw_properties = resolved.get("properties", {})
    if isinstance(raw_properties, dict):
        properties.update(str(name) for name in raw_properties)
    raw_required = resolved.get("required", [])
    if isinstance(raw_required, list):
        required.update(str(name) for name in raw_required)

    for keyword in ("allOf", "anyOf", "oneOf"):
        alternatives = resolved.get(keyword, [])
        if not isinstance(alternatives, list):
            continue
        alternative_properties: list[set[str]] = []
        alternative_required: list[set[str]] = []
        for alternative in alternatives:
            if not isinstance(alternative, dict):
                continue
            child_properties, child_required, child_allow_unknown = _merge_object_schema(
                alternative, document
            )
            alternative_properties.append(child_properties)
            alternative_required.append(child_required)
            allow_unknown = allow_unknown or child_allow_unknown
        if keyword == "allOf":
            for child_properties in alternative_properties:
                properties.update(child_properties)
            for child_required in alternative_required:
                required.update(child_required)
        elif alternative_properties:
            properties.update().union(*alternative_properties)
            common_required = set.intersection(*alternative_required) if alternative_required else set()
            required.update(common_required)

    return properties, required, allow_unknown


def _operation_parameters(operation: dict, document: dict, location: str) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    required: set[str] = set()
    for parameter in operation.get("parameters", []):
        if not isinstance(parameter, dict):
            continue
        resolved = _resolve_ref(parameter, document)
        if resolved.get("in") != location:
            continue
        name = str(resolved.get("name", "")).strip()
        if not name:
            continue
        names.add(name)
        if resolved.get("required") is True:
            required.add(name)
    return names, required


def _successful_control_names(form: NativeForm) -> set[str]:
    names: set[str] = set()
    for control in form.controls:
        attrs = control.attrs
        if "disabled" in attrs:
            continue
        name = attrs.get("name", "").strip()
        if not name:
            continue
        if control.tag == "button":
            continue
        if control.tag == "input" and attrs.get("type", "text").lower() in NON_DATA_INPUT_TYPES:
            continue
        names.add(name)
    return names


def _form_media_type(form: NativeForm) -> str:
    enctype = form.attrs.get("enctype", DEFAULT_FORM_MEDIA_TYPE).strip().lower()
    return enctype or DEFAULT_FORM_MEDIA_TYPE


def test_native_form_fields_match_openapi_input_contracts():
    forms = _native_forms()
    document = app.openapi()
    operations = _openapi_operations()

    unsupported_methods: list[str] = []
    unresolved_actions: list[str] = []
    missing_routes: list[str] = []
    ambiguous_routes: list[str] = []
    wrong_media_types: list[str] = []
    missing_required: list[str] = []
    unknown_fields: list[str] = []
    verified = 0

    for form in forms:
        method = form.attrs.get("method", "GET").upper()
        description = f"{form.source}:{form.line} {method} {form.attrs.get('action', '')!r}"
        if method not in SUPPORTED_FORM_METHODS:
            unsupported_methods.append(description)
            continue

        action = _sample_action(form.attrs.get("action", ""))
        if action is None:
            unresolved_actions.append(description)
            continue
        parsed_action = urlsplit(action)
        action_path = parsed_action.path or "/"
        action_query_names = set(parse_qs(parsed_action.query, keep_blank_values=True))
        matches = _match_operation(method, action_path, operations)
        if not matches:
            missing_routes.append(description)
            continue
        if len(matches) > 1:
            ambiguous_routes.append(
                f"{description} matches {[match.route_path for match in matches]}"
            )
            continue

        verified += 1
        operation = matches[0].operation
        control_names = _successful_control_names(form)
        query_names, required_query = _operation_parameters(operation, document, "query")

        if method == "GET":
            provided = control_names
            missing = required_query - provided
            unknown = provided - query_names
            if missing:
                missing_required.append(
                    f"{description}: missing required query fields {sorted(missing)}"
                )
            if unknown:
                unknown_fields.append(
                    f"{description}: unknown query fields {sorted(unknown)}; allowed={sorted(query_names)}"
                )
            continue

        missing_query = required_query - action_query_names
        if missing_query:
            missing_required.append(
                f"{description}: action misses required query fields {sorted(missing_query)}"
            )

        request_body = operation.get("requestBody")
        if not isinstance(request_body, dict):
            if control_names:
                wrong_media_types.append(
                    f"{description}: submits fields {sorted(control_names)} but endpoint has no request body"
                )
            continue
        request_body = _resolve_ref(request_body, document)
        content = request_body.get("content", {})
        if not isinstance(content, dict):
            content = {}
        media_type = _form_media_type(form)
        media_contract = content.get(media_type)
        if not isinstance(media_contract, dict):
            wrong_media_types.append(
                f"{description}: enctype {media_type!r} is unsupported; endpoint accepts {sorted(content)}"
            )
            continue

        schema = media_contract.get("schema", {})
        if not isinstance(schema, dict):
            schema = {}
        allowed, required, allow_unknown = _merge_object_schema(schema, document)
        missing = required - control_names
        unknown = control_names - allowed
        if missing:
            missing_required.append(
                f"{description}: missing required form fields {sorted(missing)}"
            )
        if unknown and not allow_unknown:
            unknown_fields.append(
                f"{description}: unknown form fields {sorted(unknown)}; allowed={sorted(allowed)}"
            )

    assert len(forms) >= MIN_NATIVE_FORMS, (
        "native form discovery is unexpectedly shallow: "
        f"{len(forms)} forms, expected at least {MIN_NATIVE_FORMS}"
    )
    assert verified >= MIN_NATIVE_FORMS, (
        "native form/backend verification is unexpectedly shallow: "
        f"{verified} matched forms"
    )
    assert unsupported_methods == [], "unsupported native form methods:\n" + "\n".join(
        unsupported_methods
    )
    assert unresolved_actions == [], "native form actions could not be normalized:\n" + "\n".join(
        unresolved_actions
    )
    assert missing_routes == [], "native forms target missing routes:\n" + "\n".join(
        missing_routes
    )
    assert ambiguous_routes == [], "native forms target ambiguous routes:\n" + "\n".join(
        ambiguous_routes
    )
    assert wrong_media_types == [], "native forms use incompatible request media types:\n" + "\n".join(
        wrong_media_types
    )
    assert missing_required == [], "native forms omit required backend fields:\n" + "\n".join(
        missing_required
    )
    assert unknown_fields == [], "native forms submit unknown backend fields:\n" + "\n".join(
        unknown_fields
    )
