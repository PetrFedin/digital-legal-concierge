from __future__ import annotations

import asyncio
import inspect
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
        slot_id=701,
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
            expected_slot_id=701,
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
        slot_id=702,
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
                expected_slot_id=702,
            )
        )


def test_completed_consultation_retry_from_foreign_lawyer_is_denied_first():
    consultation = SimpleNamespace(
        id=503,
        slot_id=703,
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
                expected_slot_id=703,
            )
        )


def test_exact_client_no_show_retry_is_idempotent():
    consultation = SimpleNamespace(
        id=504,
        slot_id=704,
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
            expected_slot_id=704,
        )
    )

    assert result is consultation


def test_client_no_show_retry_with_changed_comment_conflicts():
    consultation = SimpleNamespace(
        id=505,
        slot_id=705,
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
                expected_slot_id=705,
            )
        )


def test_completed_consultation_retry_for_old_slot_conflicts_before_result_retry():
    consultation = SimpleNamespace(
        id=508,
        slot_id=708,
        lawyer_id=71,
        status=ConsultationStatus.DONE,
        lawyer_result="Подробный итог консультации уже сохранён клиенту.",
        decision="close",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="Время консультации изменилось"):
        asyncio.run(
            service.complete(
                consultation_id=508,
                lawyer_id=71,
                result="Подробный итог консультации уже сохранён клиенту.",
                decision="close",
                expected_slot_id=707,
            )
        )


def test_client_no_show_retry_for_old_slot_conflicts_before_comment_retry():
    consultation = SimpleNamespace(
        id=509,
        slot_id=709,
        lawyer_id=71,
        status=ConsultationStatus.CLIENT_NO_SHOW,
        lawyer_result="Клиент не подключился, связь проверена.",
        decision="client_no_show",
    )
    service = _service(consultation)

    with pytest.raises(ConsultationOutcomeError, match="Время консультации изменилось"):
        asyncio.run(
            service.mark_client_no_show(
                consultation_id=509,
                lawyer_id=71,
                comment="Клиент не подключился, связь проверена.",
                expected_slot_id=708,
            )
        )


def _lawyer_no_show_consultation():
    return SimpleNamespace(
        id=506,
        case_id=606,
        slot_id=706,
        lawyer_id=71,
        status=ConsultationStatus.LAWYER_NO_SHOW,
        lawyer_result="Юрист не подключился, факт подтверждён администратором.",
        decision="lawyer_no_show",
    )


def _lawyer_no_show_event(*, actor_id: int, comment: str):
    return SimpleNamespace(
        actor_id=actor_id,
        new_value={
            "consultation_id": 506,
            "comment": comment,
        },
    )


def test_exact_lawyer_no_show_retry_requires_same_admin_comment_and_slot():
    consultation = _lawyer_no_show_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock(
        return_value=_lawyer_no_show_event(
            actor_id=91,
            comment="Юрист не подключился, факт подтверждён администратором.",
        )
    )

    result = asyncio.run(
        service.mark_lawyer_no_show(
            consultation_id=506,
            admin_id=91,
            comment="Юрист не подключился, факт подтверждён администратором.",
            expected_slot_id=706,
        )
    )

    assert result is consultation
    service._latest_consultation_history_event.assert_awaited_once_with(
        case_id=606,
        consultation_id=506,
        action="CONSULTATION_LAWYER_NO_SHOW",
    )


def test_lawyer_no_show_retry_from_other_admin_conflicts_even_with_same_text():
    consultation = _lawyer_no_show_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock(
        return_value=_lawyer_no_show_event(
            actor_id=91,
            comment="Юрист не подключился, факт подтверждён администратором.",
        )
    )

    with pytest.raises(ConsultationOutcomeError, match="другим администратором"):
        asyncio.run(
            service.mark_lawyer_no_show(
                consultation_id=506,
                admin_id=92,
                comment="Юрист не подключился, факт подтверждён администратором.",
                expected_slot_id=706,
            )
        )


def test_lawyer_no_show_retry_with_changed_comment_conflicts_before_idempotency_lookup():
    consultation = _lawyer_no_show_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock()

    with pytest.raises(ConsultationOutcomeError, match="другим комментарием"):
        asyncio.run(
            service.mark_lawyer_no_show(
                consultation_id=506,
                admin_id=91,
                comment="Старая вкладка отправляет другое описание неявки юриста.",
                expected_slot_id=706,
            )
        )

    service._latest_consultation_history_event.assert_not_awaited()


def test_lawyer_no_show_retry_for_old_slot_conflicts_before_actor_retry():
    consultation = _lawyer_no_show_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock()

    with pytest.raises(ConsultationOutcomeError, match="Время консультации изменилось"):
        asyncio.run(
            service.mark_lawyer_no_show(
                consultation_id=506,
                admin_id=91,
                comment="Юрист не подключился, факт подтверждён администратором.",
                expected_slot_id=705,
            )
        )

    service._latest_consultation_history_event.assert_not_awaited()


def _rebooked_consultation():
    return SimpleNamespace(
        id=507,
        case_id=607,
        slot_id=707,
        lawyer_id=72,
        status=ConsultationStatus.BOOKED,
        lawyer_result=None,
        decision=None,
    )


def _rebook_event(*, actor_id: int, comment: str, slot_id: int):
    return SimpleNamespace(
        actor_id=actor_id,
        comment=comment,
        new_value={
            "consultation_id": 507,
            "slot_id": slot_id,
        },
    )


def test_exact_no_show_rebook_retry_is_idempotent_for_same_admin_slot_and_comment():
    consultation = _rebooked_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock(
        return_value=_rebook_event(
            actor_id=93,
            comment="Клиенту согласован бесплатный перенос",
            slot_id=707,
        )
    )

    result = asyncio.run(
        service.rebook_after_lawyer_no_show(
            consultation_id=507,
            new_slot_id=707,
            admin_id=93,
            comment="Клиенту согласован бесплатный перенос",
        )
    )

    assert result is consultation
    service._latest_consultation_history_event.assert_awaited_once_with(
        case_id=607,
        consultation_id=507,
        action="CONSULTATION_REBOOKED_AFTER_LAWYER_NO_SHOW",
    )


@pytest.mark.parametrize(
    ("admin_id", "slot_id", "comment"),
    (
        (94, 707, "Клиенту согласован бесплатный перенос"),
        (93, 708, "Клиенту согласован бесплатный перенос"),
        (93, 707, "Другой комментарий из старой вкладки"),
    ),
)
def test_no_show_rebook_retry_with_other_actor_slot_or_comment_conflicts(
    admin_id,
    slot_id,
    comment,
):
    consultation = _rebooked_consultation()
    service = _service(consultation)
    service._latest_consultation_history_event = AsyncMock(
        return_value=_rebook_event(
            actor_id=93,
            comment="Клиенту согласован бесплатный перенос",
            slot_id=707,
        )
    )

    with pytest.raises(ConsultationOutcomeError, match="уже перенесена"):
        asyncio.run(
            service.rebook_after_lawyer_no_show(
                consultation_id=507,
                new_slot_id=slot_id,
                admin_id=admin_id,
                comment=comment,
            )
        )


def test_lawyer_no_show_idempotency_evidence_has_no_arbitrary_history_limit():
    source = inspect.getsource(ConsultationOutcomeService._latest_consultation_history_event)
    assert "AuditLog.action == action" in source
    assert "AuditLog.entity_id == int(case_id)" in source
    assert ".limit(" not in source
