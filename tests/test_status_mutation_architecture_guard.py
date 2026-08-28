from __future__ import annotations

import ast

import pytest

from scripts.architecture_check import ROOT, _model_status_write_lines


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "app/api/example.py",
            "from app.models import Case as LegalMatter\n"
            "def mutate(record: LegalMatter):\n"
            "    record.status = 'M1_ACCEPTED'\n",
        ),
        (
            "app/domain/example.py",
            "from ..models import Case as LegalMatter\n"
            "def mutate(record: LegalMatter):\n"
            "    setattr(record, 'status', 'M1_ACCEPTED')\n",
        ),
        (
            "app/api/example.py",
            "from sqlalchemy import update\n"
            "from app.models.case import Case as LegalMatter\n"
            "stmt = update(LegalMatter).values(status='M1_ACCEPTED')\n",
        ),
        (
            "app/api/example.py",
            "from app.models import Case as LegalMatter\n"
            "query = session.query(LegalMatter)\n"
            "query = session.query(LegalMatter).update({'status': 'M1_ACCEPTED'})\n",
        ),
        (
            "app/api/example.py",
            "cases[0].status = 'M1_ACCEPTED'\n",
        ),
    ],
)
def test_case_guard_rejects_alias_setattr_and_bulk_status_writes(
    relative_path: str,
    source: str,
) -> None:
    path = ROOT / relative_path
    tree = ast.parse(source)

    assert _model_status_write_lines(path, tree, class_name="Case")


@pytest.mark.parametrize(
    ("relative_path", "source"),
    [
        (
            "app/api/example.py",
            "from app.models import Payment as Ledger\n"
            "def mutate(record: Ledger):\n"
            "    record.status = 'PAID'\n",
        ),
        (
            "app/api/example.py",
            "from app.models.payment import Payment as Ledger\n"
            "def mutate(record: Ledger):\n"
            "    setattr(record, 'status', 'PAID')\n",
        ),
        (
            "app/api/example.py",
            "from sqlalchemy import update\n"
            "from app.models import Payment as Ledger\n"
            "stmt = update(Ledger).values(status='PAID')\n",
        ),
        (
            "app/api/example.py",
            "from app.models import Payment as Ledger\n"
            "session.query(Ledger).update({'status': 'PAID'})\n",
        ),
        (
            "app/api/example.py",
            "payment_record.status = 'PAID'\n",
        ),
    ],
)
def test_payment_guard_rejects_alias_setattr_and_bulk_status_writes(
    relative_path: str,
    source: str,
) -> None:
    path = ROOT / relative_path
    tree = ast.parse(source)

    assert _model_status_write_lines(path, tree, class_name="Payment")


def test_guard_does_not_ban_other_status_models() -> None:
    path = ROOT / "app/api/example.py"
    tree = ast.parse(
        "from app.models.document import Document\n"
        "def review(document: Document):\n"
        "    document.status = 'APPROVED'\n"
    )

    assert _model_status_write_lines(path, tree, class_name="Case") == []
    assert _model_status_write_lines(path, tree, class_name="Payment") == []


def test_payment_creation_status_is_not_a_transition_violation() -> None:
    path = ROOT / "app/api/example.py"
    tree = ast.parse(
        "from app.models import Payment as Ledger\n"
        "payment = Ledger(case_id=1, payment_code='X', title='X', amount=1, status='PENDING')\n"
    )

    assert _model_status_write_lines(path, tree, class_name="Payment") == []
