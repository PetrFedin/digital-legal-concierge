from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
API_DIR = ROOT / "app" / "api"
WRITE_METHOD_PATTERN = re.compile(
    r"\bmethod\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]",
    re.IGNORECASE,
)
GUARDED_SOURCES = {
    "app/api/access_management.py",
    "app/api/consultation_slots.py",
}
MIN_WRITE_BUTTON_ACTIONS = 9


@dataclass(frozen=True)
class JavascriptFunction:
    source: str
    name: str
    parameters: str
    body: str


@dataclass(frozen=True)
class ClickHandler:
    source: str
    function_name: str
    body: str


class ScriptParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self._depth = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "script":
            self._depth += 1
            if self._depth == 1:
                self._parts = []

    def handle_data(self, data: str) -> None:
        if self._depth:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or not self._depth:
            return
        self._depth -= 1
        if self._depth == 0:
            script = "".join(self._parts).strip()
            if script:
                self.scripts.append(script)
            self._parts = []


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


def _guarded_html_documents() -> list[tuple[str, str]]:
    return [
        (source, html)
        for source, html in _html_documents()
        if source in GUARDED_SOURCES
    ]


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


def _balanced_block(text: str, open_brace: int) -> tuple[str, int] | None:
    if open_brace >= len(text) or text[open_brace] != "{":
        return None
    depth = 1
    index = open_brace + 1
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
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[open_brace + 1 : index], index + 1
        index += 1
    return None


def _javascript_functions(source: str, html: str) -> dict[str, JavascriptFunction]:
    parser = ScriptParser()
    parser.feed(html)
    functions: dict[str, JavascriptFunction] = {}
    pattern = re.compile(
        r"(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*"
        r"\((?P<params>[^)]*)\)\s*\{"
    )
    for script in parser.scripts:
        for match in pattern.finditer(script):
            open_brace = match.end() - 1
            block = _balanced_block(script, open_brace)
            if block is None:
                continue
            body, _ = block
            functions[match.group("name")] = JavascriptFunction(
                source=source,
                name=match.group("name"),
                parameters=match.group("params"),
                body=body,
            )
    return functions


def _click_handlers(source: str, html: str) -> list[ClickHandler]:
    normalized = html.replace('\\"', '"').replace("\\'", "'")
    handlers: list[ClickHandler] = []
    for match in re.finditer(
        r"\bonclick\s*=\s*(?P<q>['\"])(?P<body>.*?)(?P=q)",
        normalized,
        re.IGNORECASE | re.DOTALL,
    ):
        body = match.group("body").strip()
        function_match = re.search(r"([A-Za-z_$][\w$]*)\s*\(", body)
        if function_match:
            handlers.append(
                ClickHandler(
                    source=source,
                    function_name=function_match.group(1),
                    body=body,
                )
            )
    return handlers


def _try_and_catch_blocks(body: str) -> tuple[str, str] | None:
    try_match = re.search(r"\btry\s*\{", body)
    if not try_match:
        return None
    try_open = body.find("{", try_match.start())
    try_block = _balanced_block(body, try_open)
    if try_block is None:
        return None
    success, try_end = try_block
    catch_match = re.search(r"\bcatch\s*\([^)]*\)\s*\{", body[try_end:])
    if not catch_match:
        return None
    catch_start = try_end + catch_match.start()
    catch_open = body.find("{", catch_start)
    catch_block = _balanced_block(body, catch_open)
    if catch_block is None:
        return None
    failure, _ = catch_block
    return success, failure


def _has_user_feedback(body: str) -> bool:
    return bool(
        re.search(
            r"\b(?:feedback|alert)\s*\(|\.textContent\s*=|\.innerHTML\s*=|"
            r"(?:window\.)?location(?:\.href)?\s*=|location\.reload\s*\(",
            body,
        )
    )


def test_write_buttons_are_single_flight_and_report_success_and_failure():
    write_actions: list[tuple[ClickHandler, JavascriptFunction]] = []

    for source, html in _guarded_html_documents():
        functions = _javascript_functions(source, html)
        for handler in _click_handlers(source, html):
            function = functions.get(handler.function_name)
            if function is None or not WRITE_METHOD_PATTERN.search(function.body):
                continue
            write_actions.append((handler, function))

    duplicate_risk: list[str] = []
    missing_result_handling: list[str] = []
    missing_success_feedback: list[str] = []
    missing_failure_feedback: list[str] = []

    for handler, function in write_actions:
        description = f"{function.source}: {function.name} via onclick={handler.body!r}"
        if "this" not in handler.body:
            duplicate_risk.append(f"{description}: button does not pass itself")
        if "withButton(" not in function.body:
            duplicate_risk.append(f"{description}: write is not protected by withButton")

        blocks = _try_and_catch_blocks(function.body)
        if blocks is None:
            missing_result_handling.append(
                f"{description}: write action has no try/catch boundary"
            )
            continue
        success, failure = blocks
        if not _has_user_feedback(success):
            missing_success_feedback.append(
                f"{description}: success is not surfaced to the user"
            )
        if not _has_user_feedback(failure):
            missing_failure_feedback.append(
                f"{description}: backend failure is not surfaced to the user"
            )

    assert len(_guarded_html_documents()) == len(GUARDED_SOURCES), (
        "one or more guarded write interfaces disappeared from the HTML audit"
    )
    assert len(write_actions) >= MIN_WRITE_BUTTON_ACTIONS, (
        "write-button feedback audit is unexpectedly shallow: "
        f"{len(write_actions)} actions, expected at least {MIN_WRITE_BUTTON_ACTIONS}"
    )
    assert duplicate_risk == [], "write buttons allow duplicate submissions:\n" + "\n".join(
        duplicate_risk
    )
    assert missing_result_handling == [], "write actions do not isolate failures:\n" + "\n".join(
        missing_result_handling
    )
    assert missing_success_feedback == [], "successful writes are silent:\n" + "\n".join(
        missing_success_feedback
    )
    assert missing_failure_feedback == [], "failed writes are silent:\n" + "\n".join(
        missing_failure_feedback
    )


def test_write_helpers_reject_non_success_responses_and_restore_buttons():
    helper_failures: list[str] = []
    status_regions = 0

    for source, html in _guarded_html_documents():
        functions = _javascript_functions(source, html)
        clicked_write_names = {
            handler.function_name
            for handler in _click_handlers(source, html)
            if handler.function_name in functions
            and WRITE_METHOD_PATTERN.search(functions[handler.function_name].body)
        }
        if not clicked_write_names:
            continue

        api = functions.get("api")
        if api is None or not re.search(
            r"if\s*\(\s*!\s*[A-Za-z_$][\w$]*\.ok\s*\)\s*(?:\{\s*)?throw\b",
            api.body,
        ):
            helper_failures.append(
                f"{source}: api helper does not throw on non-success HTTP responses"
            )

        single_flight = functions.get("withButton")
        if single_flight is None:
            helper_failures.append(f"{source}: withButton helper is missing")
        else:
            compact = re.sub(r"\s+", "", single_flight.body)
            required_fragments = (
                "button.disabled=true",
                "finally",
                "button.disabled=false",
            )
            missing = [fragment for fragment in required_fragments if fragment not in compact]
            if missing:
                helper_failures.append(
                    f"{source}: withButton is incomplete; missing {missing}"
                )

        feedback = functions.get("feedback")
        if feedback is None or ".textContent=" not in re.sub(r"\s+", "", feedback.body):
            helper_failures.append(
                f"{source}: feedback helper does not visibly update the page"
            )

        normalized = html.replace('\\"', '"').replace("\\'", "'")
        status_regions += len(
            re.findall(
                r"role\s*=\s*['\"]status['\"][^>]*aria-live\s*=\s*['\"]polite['\"]",
                normalized,
                re.IGNORECASE,
            )
        )

    assert helper_failures == [], "unsafe write helpers:\n" + "\n".join(helper_failures)
    assert status_regions >= 4, (
        "write feedback is not exposed through enough accessible status regions: "
        f"{status_regions}"
    )
