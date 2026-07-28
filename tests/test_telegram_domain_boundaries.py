from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest


BOT_ROOT = Path("app/bot")
CLIENT_PRESENTATION_FILES = (
    Path("app/bot/consultation_booking_renderer.py"),
    Path("app/bot/screens/consultation_selection.py"),
    Path("app/bot/screens/my_case.py"),
)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bslot\.status\s*=",
        r"\bconsultation\.status\s*=",
        r"\bpayment\.status\s*=",
        r"\bcase\.assigned_lawyer_id\s*=",
    ],
)
def test_telegram_layer_does_not_mutate_domain_state_directly(pattern):
    violations = []
    expression = re.compile(pattern)
    for path in BOT_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if expression.search(source):
            violations.append(str(path))

    assert violations == []


def test_client_slot_presentation_does_not_reference_internal_workload_fields():
    forbidden = {
        "workload_limit",
        "current_workload",
        "available_capacity",
        "utilization_percent",
    }
    violations = {}
    for path in CLIENT_PRESENTATION_FILES:
        source = path.read_text(encoding="utf-8")
        found = sorted(value for value in forbidden if value in source)
        if found:
            violations[str(path)] = found

    assert violations == {}


def test_client_slot_renderers_do_not_read_lawyer_contact_fields():
    forbidden_attributes = {"phone", "email"}
    violations = []
    for path in CLIENT_PRESENTATION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr in forbidden_attributes
                and isinstance(node.value, ast.Name)
                and node.value.id in {"lawyer", "consultation"}
            ):
                violations.append(f"{path}:{node.lineno}:{node.attr}")

    assert violations == []


def test_telegram_analytics_does_not_store_full_callback_payload():
    source = Path("app/bot/bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    string_values = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }

    assert "callback_data" not in string_values
    assert "callback_action" in string_values
    assert "callback_length" in string_values
