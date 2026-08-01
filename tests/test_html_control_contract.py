from __future__ import annotations

import ast
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
FORM_METHODS = {"GET", "POST"}
FIELD_TAGS = {"input", "select", "textarea"}
NON_DATA_INPUT_TYPES = {"button", "reset", "submit", "image"}
VOID_TAGS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass(frozen=True)
class Control:
    tag: str
    attrs: dict[str, str]
    line: int
    nested_in_label: bool


@dataclass
class FormRecord:
    attrs: dict[str, str]
    line: int
    controls: list[Control] = field(default_factory=list)


@dataclass
class ParsedControls:
    ids: Counter[str] = field(default_factory=Counter)
    labels_for: list[tuple[str, int]] = field(default_factory=list)
    forms: list[FormRecord] = field(default_factory=list)
    controls: list[Control] = field(default_factory=list)


class ControlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.result = ParsedControls()
        self._form_stack: list[FormRecord] = []
        self._label_depth = 0

    @staticmethod
    def _attrs(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {name.lower(): value or "" for name, value in attrs}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = self._attrs(attrs)
        line, _ = self.getpos()

        element_id = values.get("id", "").strip()
        if element_id:
            self.result.ids[element_id] += 1

        if tag == "label":
            target = values.get("for", "").strip()
            if target:
                self.result.labels_for.append((target, line))
            self._label_depth += 1

        if tag == "form":
            form = FormRecord(attrs=values, line=line)
            self.result.forms.append(form)
            self._form_stack.append(form)

        if tag in FIELD_TAGS or tag == "button":
            control = Control(
                tag=tag,
                attrs=values,
                line=line,
                nested_in_label=self._label_depth > 0,
            )
            self.result.controls.append(control)
            if self._form_stack:
                self._form_stack[-1].controls.append(control)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "label" and self._label_depth:
            self._label_depth -= 1
        elif tag == "form" and self._form_stack:
            self._form_stack.pop()


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
            # A JoinedStr is the complete f-string. Its Constant children are
            # incomplete fragments split around substitutions and must never be
            # treated as standalone HTML documents.
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


def _parse_documents() -> list[tuple[str, str, ParsedControls]]:
    parsed: list[tuple[str, str, ParsedControls]] = []
    for source, text in _html_documents():
        parser = ControlParser()
        parser.feed(text)
        parsed.append((source, text, parser.result))
    return parsed


def _is_truthy_attribute(attrs: dict[str, str], name: str) -> bool:
    return name in attrs


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value)
    except (InvalidOperation, ValueError):
        return None


def _javascript_id_references(text: str) -> list[str]:
    references: list[str] = []
    patterns = [
        re.compile(
            r"getElementById\(\s*(?P<q>['\"])(?P<id>[^'\"]+)(?P=q)"
        ),
        re.compile(
            r"querySelector\(\s*(?P<q>['\"])#(?P<id>[A-Za-z_][\w:.-]*)(?P=q)"
        ),
    ]
    for pattern in patterns:
        for match in pattern.finditer(text):
            suffix = text[match.end() : match.end() + 12].lstrip()
            if suffix.startswith("+"):
                continue
            references.append(match.group("id"))
    return references


def _declared_static_and_template_ids(
    text: str, document: ParsedControls
) -> set[str]:
    """Include IDs declared in the page and in JavaScript HTML templates."""
    declared = set(document.ids)
    for match in re.finditer(
        r"\bid\s*=\s*(?P<q>['\"])(?P<id>[^'\"]+)(?P=q)",
        text,
        re.IGNORECASE,
    ):
        element_id = match.group("id").strip()
        if element_id and "${" not in element_id and "{dynamic}" not in element_id:
            declared.add(element_id)
    return declared


def test_html_ids_labels_and_javascript_dom_references_are_consistent():
    parsed = _parse_documents()
    duplicate_ids: list[str] = []
    missing_label_targets: list[str] = []
    missing_dom_targets: list[str] = []
    checked_references = 0

    for source, text, document in parsed:
        declared_ids = _declared_static_and_template_ids(text, document)
        duplicate_ids.extend(
            f"{source}: id={element_id!r} occurs {count} times"
            for element_id, count in document.ids.items()
            if count > 1
        )
        missing_label_targets.extend(
            f"{source}:{line}: label for={target!r} has no matching id"
            for target, line in document.labels_for
            if target not in declared_ids
        )
        for target in _javascript_id_references(text):
            checked_references += 1
            if target not in declared_ids:
                missing_dom_targets.append(
                    f"{source}: JavaScript references missing id={target!r}"
                )

    assert len(parsed) >= 10, f"HTML source discovery is unexpectedly shallow: {len(parsed)}"
    assert checked_references >= 10, (
        "DOM-reference audit is unexpectedly shallow: "
        f"{checked_references} static references"
    )
    assert duplicate_ids == [], "duplicate HTML ids:\n" + "\n".join(duplicate_ids)
    assert missing_label_targets == [], "labels with missing targets:\n" + "\n".join(missing_label_targets)
    assert missing_dom_targets == [], "JavaScript references missing DOM ids:\n" + "\n".join(missing_dom_targets)


def test_native_html_forms_are_submittable_and_fields_are_serializable():
    parsed = _parse_documents()
    invalid_forms: list[str] = []
    unnamed_fields: list[str] = []
    implicit_double_submits: list[str] = []
    checked_forms = 0

    for source, _, document in parsed:
        for form in document.forms:
            checked_forms += 1
            action = form.attrs.get("action", "").strip()
            onsubmit = form.attrs.get("onsubmit", "").strip()
            method = form.attrs.get("method", "GET").upper()

            if not action and not onsubmit:
                invalid_forms.append(
                    f"{source}:{form.line}: form has neither action nor onsubmit"
                )
            if method not in FORM_METHODS:
                invalid_forms.append(
                    f"{source}:{form.line}: unsupported native form method {method!r}"
                )

            has_submit = False
            for control in form.controls:
                attrs = control.attrs
                control_type = attrs.get("type", "").lower()
                disabled = _is_truthy_attribute(attrs, "disabled")

                if control.tag == "button":
                    effective_type = control_type or "submit"
                    if effective_type == "submit" and not disabled:
                        has_submit = True
                    if attrs.get("onclick") and not control_type:
                        implicit_double_submits.append(
                            f"{source}:{control.line}: onclick button inside form needs explicit type"
                        )
                    continue

                if control.tag == "input" and control_type in {"submit", "image"}:
                    if not disabled:
                        has_submit = True
                    continue

                if disabled:
                    continue
                if control.tag == "input" and control_type in NON_DATA_INPUT_TYPES:
                    continue
                if action and not attrs.get("name", "").strip():
                    unnamed_fields.append(
                        f"{source}:{control.line}: {control.tag} in native form has no name"
                    )

            if action and not has_submit:
                invalid_forms.append(
                    f"{source}:{form.line}: form action={action!r} has no enabled submit control"
                )

    assert checked_forms >= 2, f"form audit is unexpectedly shallow: {checked_forms} forms"
    assert invalid_forms == [], "invalid or unusable forms:\n" + "\n".join(invalid_forms)
    assert unnamed_fields == [], "native form fields omitted from submission:\n" + "\n".join(unnamed_fields)
    assert implicit_double_submits == [], (
        "buttons may execute onclick and implicit submit together:\n"
        + "\n".join(implicit_double_submits)
    )


def test_html_input_constraints_are_not_self_contradictory():
    parsed = _parse_documents()
    contradictions: list[str] = []
    checked_controls = 0

    for source, _, document in parsed:
        for control in document.controls:
            if control.tag not in FIELD_TAGS:
                continue
            checked_controls += 1
            attrs = control.attrs

            if _is_truthy_attribute(attrs, "required") and _is_truthy_attribute(
                attrs, "disabled"
            ):
                contradictions.append(
                    f"{source}:{control.line}: required control is disabled"
                )

            minimum = _decimal(attrs["min"]) if "min" in attrs else None
            maximum = _decimal(attrs["max"]) if "max" in attrs else None
            if minimum is not None and maximum is not None and minimum > maximum:
                contradictions.append(
                    f"{source}:{control.line}: min={minimum} exceeds max={maximum}"
                )

            min_length = _decimal(attrs["minlength"]) if "minlength" in attrs else None
            max_length = _decimal(attrs["maxlength"]) if "maxlength" in attrs else None
            if (
                min_length is not None
                and max_length is not None
                and min_length > max_length
            ):
                contradictions.append(
                    f"{source}:{control.line}: minlength={min_length} exceeds maxlength={max_length}"
                )

            step = attrs.get("step", "").strip().lower()
            if step and step != "any":
                numeric_step = _decimal(step)
                if numeric_step is None or numeric_step <= 0:
                    contradictions.append(
                        f"{source}:{control.line}: invalid step={step!r}"
                    )

    assert checked_controls >= 20, (
        "input-constraint audit is unexpectedly shallow: "
        f"{checked_controls} controls"
    )
    assert contradictions == [], "contradictory input constraints:\n" + "\n".join(contradictions)
