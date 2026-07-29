from __future__ import annotations

import ast
from pathlib import Path

import pytest

from scripts.manage_paid_cancellations import build_parser


@pytest.mark.parametrize(
    "arguments,command",
    [
        (["list"], "list"),
        (
            [
                "refund",
                "--case-id",
                "1",
                "--payment-id",
                "2",
                "--actor-id",
                "3",
                "--reference",
                "refund-001",
                "--comment",
                "Возврат подтверждён",
            ],
            "refund",
        ),
        (
            [
                "keep",
                "--case-id",
                "1",
                "--actor-id",
                "3",
                "--comment",
                "Запись сохраняется",
            ],
            "keep",
        ),
        (
            [
                "close",
                "--case-id",
                "1",
                "--actor-id",
                "3",
                "--comment",
                "Возврат завершён",
            ],
            "close",
        ),
    ],
)
def test_cli_parser_accepts_supported_commands(arguments, command):
    assert build_parser().parse_args(arguments).command == command


@pytest.mark.parametrize(
    "arguments",
    [
        ["refund", "--case-id", "1", "--payment-id", "2", "--actor-id", "3"],
        ["keep", "--case-id", "1", "--actor-id", "3"],
        ["close", "--case-id", "1", "--comment", "Нет actor"],
    ],
)
def test_cli_requires_financial_audit_arguments(arguments):
    with pytest.raises(SystemExit):
        build_parser().parse_args(arguments)


def test_cli_does_not_mutate_domain_statuses_directly():
    path = Path("scripts/manage_paid_cancellations.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    forbidden_attributes = {"status", "slot_id", "assigned_lawyer_id"}
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr in forbidden_attributes:
                violations.append(f"{path}:{target.lineno}:{target.attr}")

    assert violations == []


def test_close_command_uses_refund_confirmed_resolution():
    source = Path("scripts/manage_paid_cancellations.py").read_text(
        encoding="utf-8"
    )

    assert "CancellationResolutionDecision.REFUND_CONFIRMED" in source
    assert "PaymentRefundService" in source
    assert "ConsultationCancellationResolutionService" in source
