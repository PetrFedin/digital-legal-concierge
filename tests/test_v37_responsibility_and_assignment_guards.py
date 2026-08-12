from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import app.domain.documents.document_review_service as review_module
from app.api.admin_queue_guard import safe_legacy_admin_queue
from app.domain.cases.assignment_service import CaseAssignmentService
from app.domain.documents.document_review_service import (
    DocumentReviewError,
    DocumentReviewService,
)
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_safe_legacy_admin_queue_precedes_generic_admin_queue():
    assert _first_route("/admin/queue").endpoint is safe_legacy_admin_queue


def test_manual_assignment_rejects_intake_and_m2_but_allows_actionable_m1():
    for status in (
        "NEW",
        "CALCULATOR_STARTED",
        "CALCULATED",
        "CLIENT_DECISION",
        "M1_DOCUMENTS_PENDING",
        "M2_DESCRIPTION_PENDING",
        "M2_SLOT_PENDING",
        "M2_CONSULTATION_BOOKED",
    ):
        with pytest.raises(ValueError, match="рабочего M1"):
            CaseAssignmentService._ensure_case_can_be_assigned(
                SimpleNamespace(status=status)
            )

    CaseAssignmentService._ensure_case_can_be_assigned(
        SimpleNamespace(status="M1_LAWYER_REVIEW")
    )
    CaseAssignmentService._ensure_case_can_be_assigned(
        SimpleNamespace(status="M1_ENFORCEMENT")
    )


def test_document_review_uses_effective_responsibility_for_m2(monkeypatch):
    calls = []

    async def allowed(db, *, case, lawyer_id):
        calls.append((db, case, lawyer_id))
        return True

    monkeypatch.setattr(review_module, "lawyer_can_access_case", allowed)
    db = object()
    service = DocumentReviewService(db)  # type: ignore[arg-type]
    actor = SimpleNamespace(role="lawyer", lawyer_id=77)
    case = SimpleNamespace(status="M2_CONSULTATION_BOOKED")

    asyncio.run(service.ensure_actor_can_review(actor, case))
    assert calls == [(db, case, 77)]


def test_document_review_denies_wrong_responsible_lawyer(monkeypatch):
    async def denied(db, *, case, lawyer_id):
        return False

    monkeypatch.setattr(review_module, "lawyer_can_access_case", denied)
    service = DocumentReviewService(object())  # type: ignore[arg-type]
    actor = SimpleNamespace(role="lawyer", lawyer_id=91)
    case = SimpleNamespace(status="M2_CONSULTATION_BOOKED")

    with pytest.raises(DocumentReviewError, match="не отвечает"):
        asyncio.run(service.ensure_actor_can_review(actor, case))


def test_closed_case_document_decisions_are_read_only_even_for_admin():
    service = DocumentReviewService(object())  # type: ignore[arg-type]
    actor = SimpleNamespace(role="admin", lawyer_id=None)
    case = SimpleNamespace(status="M2_CLOSED")

    with pytest.raises(DocumentReviewError, match="только для просмотра"):
        asyncio.run(service.ensure_actor_can_review(actor, case))
