from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.consultations.client_no_show_resolution_service import (
    ClientNoShowResolutionError,
    ClientNoShowResolutionService,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.domain.statuses.consultation_statuses import ConsultationStatus


def _service(original, case):
    service = ClientNoShowResolutionService(object())  # type: ignore[arg-type]
    service._lock_consultation = AsyncMock(return_value=original)
    service._lock_case = AsyncMock(return_value=case)
    return service


def _rebook_original():
    return SimpleNamespace(
        id=520,
        case_id=620,
        status=ConsultationStatus.RESCHEDULED,
        decision="client_no_show_rebook",
    )


def _case(*, status=CaseStatus.M2_SLOT_PENDING):
    return SimpleNamespace(
        id=620,
        status=status,
        route="M2",
    )


def _event(*, actor_id: int, comment: str, new_value: dict):
    return SimpleNamespace(
        actor_id=actor_id,
        comment=comment,
        new_value=new_value,
    )


def test_exact_client_no_show_rebook_retry_returns_only_recorded_replacement():
    original = _rebook_original()
    case = _case()
    replacement = SimpleNamespace(id=521)
    service = _service(original, case)
    service._latest_resolution_event = AsyncMock(
        return_value=_event(
            actor_id=91,
            comment="Клиент согласовал новую платную запись",
            new_value={
                "previous_consultation_id": 520,
                "consultation_id": 521,
            },
        )
    )
    service.consultations.get_current_for_case = AsyncMock(return_value=replacement)

    previous, current, returned_case = asyncio.run(
        service.prepare_new_paid_booking(
            consultation_id=520,
            admin_id=91,
            comment="Клиент согласовал новую платную запись",
        )
    )

    assert previous is original
    assert current is replacement
    assert returned_case is case


def test_client_no_show_rebook_retry_from_other_admin_conflicts():
    original = _rebook_original()
    case = _case()
    service = _service(original, case)
    service._latest_resolution_event = AsyncMock(
        return_value=_event(
            actor_id=91,
            comment="Клиент согласовал новую платную запись",
            new_value={
                "previous_consultation_id": 520,
                "consultation_id": 521,
            },
        )
    )

    with pytest.raises(ClientNoShowResolutionError, match="другим администратором"):
        asyncio.run(
            service.prepare_new_paid_booking(
                consultation_id=520,
                admin_id=92,
                comment="Клиент согласовал новую платную запись",
            )
        )


def test_client_no_show_rebook_retry_rejects_later_unrelated_current_consultation():
    original = _rebook_original()
    case = _case()
    service = _service(original, case)
    service._latest_resolution_event = AsyncMock(
        return_value=_event(
            actor_id=91,
            comment="Клиент согласовал новую платную запись",
            new_value={
                "previous_consultation_id": 520,
                "consultation_id": 521,
            },
        )
    )
    service.consultations.get_current_for_case = AsyncMock(
        return_value=SimpleNamespace(id=599)
    )

    with pytest.raises(ClientNoShowResolutionError, match="не совпадает"):
        asyncio.run(
            service.prepare_new_paid_booking(
                consultation_id=520,
                admin_id=91,
                comment="Клиент согласовал новую платную запись",
            )
        )


def _closed_original():
    return SimpleNamespace(
        id=530,
        case_id=630,
        status=ConsultationStatus.CLIENT_NO_SHOW,
        decision="client_no_show_closed",
    )


def _closed_case():
    return SimpleNamespace(
        id=630,
        status=CaseStatus.M2_CLOSED,
    )


def test_exact_client_no_show_close_retry_requires_same_admin_and_comment():
    original = _closed_original()
    case = _closed_case()
    service = _service(original, case)
    service._latest_resolution_event = AsyncMock(
        return_value=_event(
            actor_id=93,
            comment="Клиент подтвердил завершение обращения",
            new_value={"consultation_id": 530},
        )
    )

    result_consultation, result_case = asyncio.run(
        service.close_case(
            consultation_id=530,
            admin_id=93,
            comment="Клиент подтвердил завершение обращения",
        )
    )

    assert result_consultation is original
    assert result_case is case


def test_client_no_show_close_retry_from_other_admin_conflicts():
    original = _closed_original()
    case = _closed_case()
    service = _service(original, case)
    service._latest_resolution_event = AsyncMock(
        return_value=_event(
            actor_id=93,
            comment="Клиент подтвердил завершение обращения",
            new_value={"consultation_id": 530},
        )
    )

    with pytest.raises(ClientNoShowResolutionError, match="другим администратором"):
        asyncio.run(
            service.close_case(
                consultation_id=530,
                admin_id=94,
                comment="Клиент подтвердил завершение обращения",
            )
        )


def test_client_no_show_resolution_evidence_never_ages_out_by_event_count():
    source = inspect.getsource(ClientNoShowResolutionService._latest_resolution_event)
    assert "AuditLog.action == action" in source
    assert "AuditLog.entity_id == int(case_id)" in source
    assert ".limit(" not in source
