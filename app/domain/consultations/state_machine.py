from __future__ import annotations

from collections.abc import Iterable

from app.domain.statuses.consultation_statuses import ConsultationStatus


class InvalidConsultationTransition(ValueError):
    """Raised when a consultation status transition is not allowed."""


_ALLOWED_TRANSITIONS: dict[ConsultationStatus, frozenset[ConsultationStatus]] = {
    ConsultationStatus.DESCRIPTION_PENDING: frozenset(
        {ConsultationStatus.DOCUMENTS_OPTIONAL, ConsultationStatus.CANCELLED}
    ),
    ConsultationStatus.DOCUMENTS_OPTIONAL: frozenset(
        {ConsultationStatus.PAYMENT_PENDING, ConsultationStatus.CANCELLED}
    ),
    ConsultationStatus.SLOT_PENDING: frozenset(
        {Consultation