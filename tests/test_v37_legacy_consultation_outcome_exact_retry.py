from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.domain.consultations.legacy_outcome_resolution_service import (
    LegacyConsultationOutcomeResolutionError,
    LegacyConsultationOutcomeResolutionService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


class _One:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _Many:
    def __init__(self, values):
        self.values = list(values)

    def scalars(self):
        return self

    def all(self):
        return list(self.values)


class _Db:
    def __init__(self, *results):
        self.results = list(results)

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected DB execute")
        return self.results.pop(0)

    async def flush(self):
        return None


def _consultation(*, decision: str = "close"):
    return SimpleNamespace(
        id=540,
        case_id=640,
        status=ConsultationStatus.DONE.value,
        decision=decision,
    )


def _case():
    return SimpleNamespace(id=640)


def _event(*, actor_id: int, decision: str, comment: str):
    return SimpleNamespace(
        actor_id=actor_id,
        comment=comment,
        new_value={
            "consultation_id": 540,
            "consultation_decision": decision,
        },
    )


def test_exact_legacy_outcome_retry_requires_same_admin_decision_and_comment():
    consultation = _consultation(decision="close")
    case = _case()
    db = _Db(
        _One(consultation),
        _Many([_event(actor_id=91, decision="close", comment="Архивный итог проверен администратором")]),
        _One(case),
    )
    service = LegacyConsultationOutcomeResolutionService(db)  # type: ignore[arg-type]

    result_consultation, result_case = asyncio.run(
        service.resolve(
            consultation_id=540,
            admin_id=91,
            decision="close",
            comment="Архивный итог проверен администратором",
        )
    )

    assert result_consultation is consultation
    assert result_case is case


def test_legacy_outcome_retry_from_other_admin_is_conflict():
    consultation = _consultation(decision="close")
    db = _Db(
        _One(consultation),
        _Many([_event(actor_id=91, decision="close", comment="Архивный итог проверен администратором")]),
    )
    service = LegacyConsultationOutcomeResolutionService(db)  # type: ignore[arg-type]

    with pytest.raises(LegacyConsultationOutcomeResolutionError, match="другим администратором"):
        asyncio.run(
            service.resolve(
                consultation_id=540,
                admin_id=92,
                decision="close",
                comment="Архивный итог проверен администратором",
            )
        )


def test_legacy_outcome_retry_with_changed_comment_is_conflict():
    consultation = _consultation(decision="close")
    db = _Db(
        _One(consultation),
        _Many([_event(actor_id=91, decision="close", comment="Архивный итог проверен администратором")]),
    )
    service = LegacyConsultationOutcomeResolutionService(db)  # type: ignore[arg-type]

    with pytest.raises(LegacyConsultationOutcomeResolutionError, match="другим решением или с другим основанием"):
        asyncio.run(
            service.resolve(
                consultation_id=540,
                admin_id=91,
                decision="close",
                comment="Иное основание из старой вкладки администратора",
            )
        )


def test_opposite_legacy_decision_from_stale_tab_conflicts_before_retry_lookup():
    consultation = _consultation(decision="close")
    db = _Db(_One(consultation))
    service = LegacyConsultationOutcomeResolutionService(db)  # type: ignore[arg-type]

    with pytest.raises(LegacyConsultationOutcomeResolutionError, match="другое поддерживаемое"):
        asyncio.run(
            service.resolve(
                consultation_id=540,
                admin_id=91,
                decision="to_m1",
                comment="Старая вкладка пытается выбрать противоположный итог",
            )
        )

    assert db.results == []


def test_legacy_resolution_audit_evidence_does_not_age_out_by_event_count():
    source = inspect.getsource(
        LegacyConsultationOutcomeResolutionService._latest_resolution_event
    )
    assert "AuditLog.action == self.RESOLUTION_ACTION" in source
    assert "AuditLog.entity_id == int(case_id)" in source
    assert ".limit(" not in source
