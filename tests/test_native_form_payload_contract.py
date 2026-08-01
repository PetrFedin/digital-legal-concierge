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
MIN_NATIVE_FORMS = 2


@dataclass(frozen=True)
class Control:
    tag: str
    attrs: dict[str, str]


@dataclass
class NativeForm:
    source: str
    line: int
    attrs: dict[str, str]
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
        if tag == "form":
            line, _ = self.getpos()
            form = NativeForm(source=self.source, line=line, attrs=values)
            self.forms.append(form)
            self._stack.append(form)
        elif tag in {"input", "select", "textarea", "button"} and self._stack:
            self._stack[-1].controls.append(Control(tag=tag, attrs=values))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._stack:
            self._stack.pop()


def _literal_text(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            value.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str)
            else "{dynamic}"
            for value in node.values
        )
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
            form for form in parser.forms if form.attrs.get("action", "").strip()
        )
    return forms


def _replace_js_templates(value: str) -> str:
    return re.sub(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", "1", value)


def _sample_action(action: str) -> str | None:
    action = _replace_js_templates(action.strip())
    action = action.replace("{dynamic}", "1").replace("__ID__", "1")
    if not action.startswith("/") or action.startswith("//"):
        return None
    if any(char in action for char in {'<', '>', '"', "'", "`"}):
        return None
    return action


def _openapi_operations(document: dict) -> list[OperationContract]:
    operations: list[OperationContract] = []
    for route_path, path_item in document.get("paths", {}).items():
        if not isinstance(path_item, dict):
            continue
        for method in ("get", "post", "put", "patch", "delete"):
            operation = path_item.get(method)
            if isinstance(operation, dict):
                operations.append(
                    OperationContract(route_path, method.upper(), operation)
                )
    return operations


def _matching_operations(
    operations: list[OperationContract], method: str, path: str
) -> list[OperationContract]:
    matches: list[OperationContract] = []
    for operation in operations:
        if operation.method != method:
            continue
        pattern, _, _ = compile_path(operation.route_path)
        if pattern.fullmatch(path):
            matches.append(operation)
    return matches


def _resolve_ref(value: dict, document: dict) -> dict:
    current = value
    seen: set[str] = set()
    while isinstance(current, dict) and "$ref" in current:
        reference = str(current["$ref"])
        if reference in seen or not reference.startswith("#/"):
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


def _schema_contract(
    schema: dict, document: dict
) -> tuple[set[str], set[str], bool]:
    resolved = _resolve_ref(schema, document)
    properties = set(str(name) for name in resolved.get("properties", {}))
    required = set(str(name) for name in resolved.get("required", []))
    allow_unknown = resolved.get("additionalProperties") is True

    all_of = resolved.get("allOf", [])
    if isinstance(all_of, list):
        for child in all_of:
            if not isinstance(child, dict):
                continue
            child_properties, child_required, child_unknown = _schema_contract(
                child, document
            )
            properties.update(child_properties)
            required.update(child_required)
            allow_unknown = allow_unknown or child_unknown

    for keyword in ("anyOf", "oneOf"):
        alternatives = resolved.get(keyword, [])
        if not isinstance(alternatives, list):
            continue
        child_contracts = [
            _schema_contract(child, document)
            for child in alternatives
            if isinstance(child, dict)
        ]
        if not child_contracts:
            continue
        for child_properties, _, child_unknown in child_contracts:
            properties.update(child_properties)
            allow_unknown = allow_unknown or child_unknown
        common_required = set.intersection(
            *(child_required for _, child_required, _ in child_contracts)
        )
        required.update(common_required)

    return properties, required, allow_unknown


def _parameters(
    operation: dict, document: dict, location: str
) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    required: set[str] = set()
    for raw_parameter in operation.get("parameters", []):
        if not isinstance(raw_parameter, dict):
            continue
        parameter = _resolve_ref(raw_parameter, document)
        if parameter.get("in") != location:
            continue
        name = str(parameter.get("name", "")).strip()
        if not name:
            continue
        names.add(name)
        if parameter.get("required") is True:
            required.add(name)
    return names, required


def _successful_control_names(form: NativeForm) -> set[str]:
    names: set[str] = set()
    for control in form.controls:
        attrs = control.attrs
        if "disabled" in attrs:
            continue
        name = attrs.get("name", "").strip()
        if not name or control.tag == "button":
            continue
        control_type = attrs.get("type", "text").lower()
        if control.tag == "input" and control_type in NON_DATA_INPUT_TYPES:
            continue
        names.add(name)
    return names


def _media_type(form: NativeForm) -> str:
    value = form.attrs.get("enctype", DEFAULT_FORM_MEDIA_TYPE).strip().lower()
    return value or DEFAULT_FORM_MEDIA_TYPE


def test_native_form_fields_match_openapi_input_contracts():
    forms = _native_forms()
    document = app.openapi()
    operations = _openapi_operations(document)

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
        description = (
            f"{form.source}:{form.line} {method} "
            f"{form.attrs.get('action', '')!r}"
        )
        if method not in SUPPORTED_FORM_METHODS:
            unsupported_methods.append(description)
            continue

        action = _sample_action(form.attrs.get("action", ""))
        if action is None:
            unresolved_actions.append(description)
            continue
        parsed = urlsplit(action)
        matches = _matching_operations(operations, method, parsed.path or "/")
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
        query_names, required_query = _parameters(operation, document, "query")

        if method == "GET":
            missing = required_query - control_names
            unknown = control_names - query_names
            if missing:
                missing_required.append(
                    f"{description}: missing query fields {sorted(missing)}"
                )
            if unknown:
                unknown_fields.append(
                    f"{description}: unknown query fields {sorted(unknown)}; "
                    f"allowed={sorted(query_names)}"
                )
            continue

        action_query_names = set(parse_qs(parsed.query, keep_blank_values=True))
        missing_query = required_query - action_query_names
        if missing_query:
            missing_required.append(
                f"{description}: action misses query fields {sorted(missing_query)}"
            )

        request_body = operation.get("requestBody")
        if not isinstance(request_body, dict):
            if control_names:
                wrong_media_types.append(
                    f"{description}: sends {sorted(control_names)} but endpoint has no body"
                )
            continue

        request_body = _resolve_ref(request_body, document)
        content = request_body.get("content", {})
        content = content if isinstance(content, dict) else {}
        media_type = _media_type(form)
        media_contract = content.get(media_type)
        if not isinstance(media_contract, dict):
            wrong_media_types.append(
                f"{description}: enctype {media_type!r} is unsupported; "
                f"accepted={sorted(content)}"
            )
            continue

        raw_schema = media_contract.get("schema", {})
        raw_schema = raw_schema if isinstance(raw_schema, dict) else {}
        allowed, required, allow_unknown = _schema_contract(raw_schema, document)
        missing = required - control_names
        unknown = control_names - allowed
        if missing:
            missing_required.append(
                f"{description}: missing form fields {sorted(missing)}"
            )
        if unknown and not allow_unknown:
            unknown_fields.append(
                f"{description}: unknown form fields {sorted(unknown)}; "
                f"allowed={sorted(allowed)}"
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
    assert unresolved_actions == [], "unresolved native form actions:\n" + "\n".join(
        unresolved_actions
    )
    assert missing_routes == [], "native forms target missing routes:\n" + "\n".join(
        missing_routes
    )
    assert ambiguous_routes == [], "native forms target ambiguous routes:\n" + "\n".join(
        ambiguous_routes
    )
    assert wrong_media_types == [], "native forms use incompatible media types:\n" + "\n".join(
        wrong_media_types
    )
    assert missing_required == [], "native forms omit required backend fields:\n" + "\n".join(
        missing_required
    )
    assert unknown_fields == [], "native forms submit unknown backend fields:\n" + "\n".join(
        unknown_fields
    )
