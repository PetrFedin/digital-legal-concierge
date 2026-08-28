from __future__ import annotations

import pytest

from app.domain.cases.case_transition_policy import (
    CaseTransitionError,
    transition_allowed,
    validate_initial_status,
    validate_transition,
)
from app.domain.statuses.case_statuses import CaseStatus
from app.models import Case


def test_legacy_m2_status_remains_readable_and_can_only_move_forward() -> None:
    source, destination = validate_transition(
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
        force=False,
        actor_type="system",
        comment="legacy intake upgrade",
    )

    assert source == CaseStatus.M2_CONSULTATION_ROUTE
    assert destination == CaseStatus.M2_DESCRIPTION_PENDING
    assert transition_allowed(
        CaseStatus.M2_CONSULTATION_ROUTE,
        CaseStatus.M2_DESCRIPTION_PENDING,
    )


def test_new_case_cannot_start_in_legacy_m2_status() -> None:
    with pytest.raises(CaseTransitionError, match="исторических данных"):
        validate_initial_status(CaseStatus.M2_CONSULTATION_ROUTE)

    assert (
        validate_initial_status(CaseStatus.M2_DESCRIPTION_PENDING)
        == CaseStatus.M2_DESCRIPTION_PENDING
    )


@pytest.mark.parametrize("force", [False, True])
def test_transition_cannot_reenter_legacy_m2_status_even_when_forced(force: bool) -> None:
    with pytest.raises(CaseTransitionError, match="compatibility-only"):
        validate_transition(
            CaseStatus.M2_DESCRIPTION_PENDING,
            CaseStatus.M2_CONSULTATION_ROUTE,
            force=force,
            actor_type="admin" if force else "system",
            comment="administrative correction",
        )

    assert not transition_allowed(
        CaseStatus.M2_DESCRIPTION_PENDING,
        CaseStatus.M2_CONSULTATION_ROUTE,
    )


def test_orm_backstop_rejects_new_legacy_m2_status() -> None:
    with pytest.raises(ValueError, match="только для чтения"):
        Case(
            case_number="DLC-TEST-LEGACY",
            client_id=1,
            route="M2",
            status=CaseStatus.M2_CONSULTATION_ROUTE,
        )


def test_orm_backstop_rejects_direct_reentry_into_legacy_m2_status() -> None:
    case = Case(
        case_number="DLC-TEST-CURRENT",
        client_id=1,
        route="M2",
        status=CaseStatus.M2_DESCRIPTION_PENDING,
    )

    with pytest.raises(ValueError, match="повторный вход"):
        case.status = CaseStatus.M2_CONSULTATION_ROUTE
