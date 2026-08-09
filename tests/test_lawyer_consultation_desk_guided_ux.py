from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.lawyer_consultation_desk import _timing_payload
from app.domain.statuses.consultation_statuses import ConsultationStatus


ROOT = Path(__file__).resolve().parents[1]


def _source() -> str:
    return (ROOT / "app/api/lawyer_consultation_desk.py").read_text(encoding="utf-8")


def test_lawyer_desk_has_guided_action_hierarchy_and_search_recovery():
    source = _source()

    assert "Сейчас" in source
    assert "Главный следующий шаг" in source
    assert "Подготовка" in source
    assert 'id="filter"' in source
    assert "Ничего не найдено" in source
    assert "Очистить поиск" in source
    assert "Открыть все дела" in source
    assert "Открыть сообщения" in source
    assert "Ко всем делам" in source


def test_no_show_is_an_explicit_exception_not_a_peer_primary_action():
    source = _source()
    card_start = source.index("function card(x)")
    form_start = source.index("function openForm", card_start)
    card_source = source[card_start:form_start]

    assert "Исключение: клиент не подключился" in card_source
    assert "прошло не менее 15 минут" in card_source
    assert "Зафиксировать неявку" in card_source
    assert "Нормальное завершение встречи оформляется выше через результат" in card_source
    assert "x.can_mark_no_show?`<details class=\"exception\"" in card_source


def test_operator_ui_preserves_exact_domain_endpoints_and_no_generic_status_mutation():
    source = _source()

    assert "`/lawyer/consultations/${id}/complete`" in source
    assert "`/lawyer/consultations/${id}/client-no-show`" in source
    assert "if(!confirm(" in source
    assert "/lawyer/consultations/${id}/status" not in source
    assert "generic status" not in source.lower()


def test_lawyer_desk_links_are_case_scoped_and_local_only():
    source = _source()

    assert '"case_url": f"/lawyer/workspace/ui?case_id={case.id}"' in source
    assert '"message_url": f"/message-center/ui?case_id={case.id}"' in source
    assert '"document_review_url": f"/document-access/review/ui?case_id={case.id}"' in source
    assert "function localHref" in source
    assert "value.startsWith('/')&&!value.startsWith('//')" in source


def test_prebooking_states_do_not_invent_a_fake_operator_action():
    source = _source()
    next_step_start = source.index("function nextStep(x)")
    card_start = source.index("function card(x)", next_step_start)
    next_step = source[next_step_start:card_start]

    assert "Сейчас следующий шаг выполняет клиент" in next_step
    assert "Не меняйте статус вручную" in next_step
    assert "question_pending" in next_step
    assert "materials_pending" in next_step
    assert "slot_pending" in next_step
    assert "confirmation_pending" in next_step


def test_booked_consultation_cannot_be_marked_no_show_before_fifteen_minutes():
    scheduled_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=10)
    consultation = SimpleNamespace(
        status=ConsultationStatus.BOOKED,
        scheduled_at=scheduled_at,
    )
    slot = SimpleNamespace(
        starts_at=scheduled_at,
        ends_at=scheduled_at + timedelta(hours=1),
    )

    payload = _timing_payload(
        consultation,
        slot,
        scheduled_at + timedelta(minutes=10),
    )

    assert payload["state"] == "in_progress"
    assert payload["can_complete"] is True
    assert payload["can_mark_no_show"] is False


def test_booked_consultation_allows_no_show_only_after_fifteen_minutes():
    scheduled_at = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=20)
    consultation = SimpleNamespace(
        status=ConsultationStatus.BOOKED,
        scheduled_at=scheduled_at,
    )
    slot = SimpleNamespace(
        starts_at=scheduled_at,
        ends_at=scheduled_at + timedelta(hours=1),
    )

    payload = _timing_payload(
        consultation,
        slot,
        scheduled_at + timedelta(minutes=20),
    )

    assert payload["state"] == "in_progress"
    assert payload["can_complete"] is True
    assert payload["can_mark_no_show"] is True
