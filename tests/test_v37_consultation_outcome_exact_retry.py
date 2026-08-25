from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.domain.consultations.outcome_service import (
    ConsultationOutcomeError,
    ConsultationOutcomeService,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus


def _service(consultation):
    service = ConsultationOutcomeService(object())  # type: ignore[arg-type]
    service._lock_consultation = AsyncMock(return_value=consultation)
    return service


def test_exact_completed_consultation_retry_is_idempotent():
    consultation = SimpleNamespace(
        id=501,
        lawyer_id=71,
        status=ConsultationStatus.DONE,
        lawyer_result="Подробный итог консультации уже сохранён клиенту.",
        decision="close",
    )
    service = _service(consultation)

    result = asyncio.run(
        service.complete(
            consultation_id=501,
            lawyer_id=71,
            result="Подробный итог консультации уже сохранён клиенту.",
            decision="close",
        )
    )

    assert result is consultation


@pytest.mark.parametrize(
    ("result", "decision"),
    (
        ("Другой итог консультации из старой вкладки юриста.", "close"),
        ("Подробный итог консультации уже сохранён клиенту.", "to_m1"),
    ),
)
def test_completed_consultation_stale_different_result_or_decision_conflicts(result, decision):
    consultation = SimpleNamespace(
        id=502,
        lawyer_id=71,
        status=ConsultationStatus.DONE,
        lawyer_result="Подробный итог консультации уже сохранён клиенту.",
        decision="close",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="уже завершена другим итогом"):
        asyncio.run(
            service.complete(
                consultation_id=502,
                lawyer_id=71,
                result=result,
                decision=decision,
            )
        )


def test_completed_consultation_retry_from_foreign_lawyer_is_denied_first():
    consultation = SimpleNamespace(
        id=503,
        lawyer_id=71,
        status=ConsultationStatus.DONE,
        lawyer_result="Подробный итог консультации уже сохранён клиенту.",
        decision="close",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="только назначенному юристу"):
        asyncio.run(
            service.complete(
                consultation_id=503,
                lawyer_id=72,
                result="Подробный итог консультации уже сохранён клиенту.",
                decision="close",
            )
        )


def test_exact_client_no_show_retry_is_idempotent():
    consultation = SimpleNamespace(
        id=504,
        lawyer_id=71,
        status=ConsultationStatus.CLIENT_NO_SHOW,
        lawyer_result="Клиент не подключился, связь проверена.",
        decision="client_no_show",
    )
    service = _service(consultation)

    result = asyncio.run(
        service.mark_client_no_show(
            consultation_id=504,
            lawyer_id=71,
            comment="Клиент не подключился, связь проверена.",
        )
    )

    assert result is consultation


def test_client_no_show_retry_with_changed_comment_conflicts():
    consultation = SimpleNamespace(
        id=505,
        lawyer_id=71,
        status=ConsultationStatus.CLIENT_NO_SHOW,
        lawyer_result="Клиент не подключился, связь проверена.",
        decision="client_no_show",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="другим комментарием"):
        asyncio.run(
            service.mark_client_no_show(
                consultation_id=505,
                lawyer_id=71,
                comment="Старая вкладка отправляет иное описание неявки.",
            )
        )


def test_lawyer_no_show_retry_with_changed_comment_conflicts():
    consultation = SimpleNamespace(
        id=506,
        lawyer_id=71,
        status=ConsultationStatus.LAWYER_NO_SHOW,
        lawyer_result="Юрист не подключился, факт подтверждён администратором.",
        decision="lawyer_no_show",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="другим комментарием"):
        asyncio.run(
            service.mark_lawyer_no_show(
                consultation_id=506,
                admin_id=91,
                comment="Старая вкладка отправляет другое описание неявки юриста.",
            )
        )
