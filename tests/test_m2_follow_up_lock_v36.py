from __future__ import annotations

import pytest

from app.bot.consultation_result import prepare_follow_up_consultation


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeDB:
    def __init__(self, case_result, outcome_result):
        self.values = [_ScalarResult(case_result), _ScalarResult(outcome_result)]
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.values.pop(0)


@pytest.mark.asyncio
async def test_prepare_follow_up_locks_case_and_source_consultation():
    case = type("Case", (), {"id": 7, "client_id": 11, "status": "M2_CONSULTATION_DONE"})()
    outcome = type(
        "Consultation",
        (),
        {
            "id": 19,
            "case_id": 7,
            "status": "DONE",
            "decision": "follow_up",
            "client_description": "Подробно описанный вопрос клиента длиной более двадцати символов",
        },
    )()
    db = _FakeDB(case, outcome)

    with pytest.raises(Exception):
        await prepare_follow_up_consultation(
            db,
            case=case,
            outcome=outcome,
            client_id=11,
        )

    assert len(db.statements) >= 2
    assert all("FOR UPDATE" in " ".join(str(stmt).upper().split()) for stmt in db.statements[:2])
