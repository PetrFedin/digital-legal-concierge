from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from app.api.lawyer_consultation_desk import (
    _latest_documents,
    _timing_payload,
)
from app.domain.statuses.consultation_statuses import ConsultationStatus
from app.domain.statuses.document_statuses import DocumentStatus

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def consultation(*, status: ConsultationStatus, scheduled_at: datetime | None):
    return SimpleNamespace(status=status, scheduled_at=scheduled_at)


def slot(*, starts_at: datetime, ends_at: datetime):
    return SimpleNamespace(starts_at=starts_at, ends_at=ends_at)


def document(*, id: int, document_type: str, version: int, status: DocumentStatus):
    return SimpleNamespace(
        id=id,
        document_type=document_type,
        version=version,
        status=status,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=id),
    )


def test_future_consultation_exposes_preparation_without_dead_action():
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    starts_at = now + timedelta(hours=2)

    payload = _timing_payload(
        consultation(status=ConsultationStatus.BOOKED, scheduled_at=starts_at),
        slot(starts_at=starts_at, ends_at=starts_at + timedelta(hours=1)),
        now,
    )

    assert payload["state"] == "upcoming"
    assert payload["can_complete"] is False
    assert payload["can_mark_no_show"] is False
    assert "До начала" in str(payload["detail"])


def test_live_consultation_allows_result_before_no_show_threshold():
    now = datetime(2026, 8, 6, 9, 10, tzinfo=timezone.utc)
    starts_at = now - timedelta(minutes=10)

    payload = _timing_payload(
        consultation(status=ConsultationStatus.BOOKED, scheduled_at=starts_at),
        slot(starts_at=starts_at, ends_at=starts_at + timedelta(hours=1)),
        now,
    )

    assert payload["state"] == "in_progress"
    assert payload["can_complete"] is True
    assert payload["can_mark_no_show"] is False
    assert "15 минут" in str(payload["detail"])


def test_no_show_and_outcome_actions_appear_only_when_domain_allows_them():
    starts_at = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)
    live = _timing_payload(
        consultation(status=ConsultationStatus.BOOKED, scheduled_at=starts_at),
        slot(starts_at=starts_at, ends_at=starts_at + timedelta(hours=1)),
        starts_at + timedelta(minutes=20),
    )
    ended = _timing_payload(
        consultation(status=ConsultationStatus.BOOKED, scheduled_at=starts_at),
        slot(starts_at=starts_at, ends_at=starts_at + timedelta(hours=1)),
        starts_at + timedelta(hours=2),
    )

    assert live["can_complete"] is True
    assert live["can_mark_no_show"] is True
    assert ended["state"] == "outcome_due"
    assert ended["requires_outcome"] is True
    assert ended["can_complete"] is True
    assert ended["can_mark_no_show"] is True


def test_prebooking_states_never_offer_lawyer_outcome_actions():
    now = datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc)

    for status in (
        ConsultationStatus.DESCRIPTION_PENDING,
        ConsultationStatus.DOCUMENTS_OPTIONAL,
        ConsultationStatus.SLOT_PENDING,
        ConsultationStatus.SLOT_RESERVED,
        ConsultationStatus.PAYMENT_PENDING,
    ):
        payload = _timing_payload(
            consultation(status=status, scheduled_at=None),
            None,
            now,
        )
        assert payload["can_complete"] is False
        assert payload["can_mark_no_show"] is False
        assert payload["requires_outcome"] is False


def test_consultation_materials_show_only_latest_active_version_per_type():
    documents = [
        document(
            id=1,
            document_type="OTHER",
            version=1,
            status=DocumentStatus.APPROVED,
        ),
        document(
            id=2,
            document_type="OTHER",
            version=2,
            status=DocumentStatus.ON_REVIEW,
        ),
        document(
            id=3,
            document_type="PASSPORT",
            version=1,
            status=DocumentStatus.ARCHIVED,
        ),
    ]

    latest = _latest_documents(documents)

    assert len(latest) == 1
    assert latest[0].document_type == "OTHER"
    assert latest[0].version == 2
    assert latest[0].status == DocumentStatus.ON_REVIEW


def test_consultation_desk_is_role_scoped_and_has_complete_recovery_ui():
    source = read("app/api/lawyer_consultation_desk.py")

    assert "require_lawyer_actor" in source
    assert "Consultation.lawyer_id == actor.lawyer.id" in source
    assert '@router.get("/data")' in source
    assert '@router.get("/ui"' in source
    assert "Вопрос клиента" in source
    assert "Материалы" in source
    assert "Дата и время" in source
    assert "Связь с клиентом" in source
    assert '/message-center/ui?case_id={case.id}' in source
    assert '/document-access/review/ui?case_id={case.id}' in source
    assert "Зафиксировать результат" in source
    assert "Исключение: клиент не подключился" in source
    assert ".green{background:var(--green)}" in source
    assert "Загрузка консультаций" in source
    assert "Не удалось загрузить консультации" in source
    assert "Повторить" in source
    assert "Вернуться без сохранения" in source
    assert "Результат сохранён, но список не обновился" in source
    assert "Неявка сохранена, но список не обновился" in source
    assert "aria-live=\"polite\"" in source
    assert "const pending=new Set()" in source
    assert "setInterval" in source


def test_operator_mounts_and_links_the_consultation_desk():
    source = read("app/api/operator.py")

    assert 'href="/lawyer/consultation-desk/ui"' in source
    assert '"lawyer_consultations": "/lawyer/consultation-desk/ui"' in source
    assert "router.include_router(lawyer_consultation_desk_router)" in source
