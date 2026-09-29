from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.bot.screens.history import (
    HISTORY_PAGE_SIZE,
    _format_timeline,
    _history_target,
)
from app.domain.cases.case_activity import present_case_activity


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def audit(
    action: str,
    *,
    new_value: dict | None = None,
    comment: str | None = None,
    actor_type: str = "system",
    event_id: int = 10,
):
    return SimpleNamespace(
        id=event_id,
        action=action,
        new_value=new_value,
        comment=comment,
        actor_type=actor_type,
        created_at=datetime(2026, 8, 6, 12, 30, tzinfo=timezone.utc),
    )


def test_unknown_and_internal_events_are_not_exposed_to_client():
    assert present_case_activity(audit("TOKEN_REVOKED"), audience="client") is None
    assert present_case_activity(audit("SLA_OVERDUE"), audience="client") is None


def test_staff_sees_operational_sla_without_raw_payload():
    item = present_case_activity(
        audit(
            "SLA_OVERDUE",
            new_value={"secret_hash": "do-not-render", "level": 2},
            comment="Связаться с клиентом и установить новый срок",
            actor_type="system",
        ),
        audience="staff",
    )

    assert item is not None
    assert item.title == "Зафиксирована просрочка SLA"
    assert item.category == "sla"
    assert item.detail == "Связаться с клиентом и установить новый срок"
    assert "secret_hash" not in str(item.as_dict())
    assert "do-not-render" not in str(item.as_dict())


def test_status_event_uses_human_label_not_internal_state_code():
    item = present_case_activity(
        audit(
            "CASE_STATUS_CHANGED",
            new_value={"status": "M1_LAWYER_REVIEW"},
            actor_type="lawyer",
        ),
        audience="client",
    )

    assert item is not None
    assert item.title == "Этап дела обновлён"
    assert item.detail is not None
    assert "M1_LAWYER_REVIEW" not in item.detail
    assert "Текущий этап:" in item.detail
    assert item.actor_label == "Юрист"


def test_upload_explains_that_file_is_not_yet_submitted():
    item = present_case_activity(
        audit(
            "DOCUMENT_UPLOADED",
            new_value={
                "document_id": 77,
                "type": "DDU",
                "version": 3,
                "sha256": "hidden",
                "encryption_key_id": "hidden",
            },
            actor_type="client",
        ),
        audience="client",
    )

    assert item is not None
    assert item.title == "Документ загружен"
    assert item.detail == "ДДУ, версия 3. Файл ещё не передан юристу."
    assert "77" not in item.detail
    assert "hidden" not in item.detail


def test_document_decision_keeps_staff_comment_out_of_client_history():
    item = present_case_activity(
        audit(
            "DOCUMENT_REVIEW_DECISION",
            new_value={
                "document_id": 77,
                "status": "NEEDS_REUPLOAD",
                "version": 3,
            },
            comment="Добавьте читаемую страницу с подписями " * 20,
            actor_type="lawyer",
        ),
        audience="client",
    )

    assert item is not None
    assert item.title == "Юрист проверил документ"
    assert item.detail is not None
    assert "нужна новая версия" in item.detail
    assert "Комментарий:" not in item.detail
    assert "Добавьте читаемую страницу" not in item.detail
    assert "document_id" not in item.detail

    staff_item = present_case_activity(
        audit(
            "DOCUMENT_REVIEW_DECISION",
            new_value={
                "document_id": 77,
                "status": "NEEDS_REUPLOAD",
                "version": 3,
            },
            comment="Добавьте читаемую страницу с подписями " * 20,
            actor_type="lawyer",
        ),
        audience="staff",
    )
    assert staff_item is not None
    assert staff_item.detail is not None
    assert "Комментарий:" in staff_item.detail


def test_telegram_timeline_has_bound_target_and_readable_business_time_output():
    assert HISTORY_PAGE_SIZE == 7
    assert _history_target("case_history_before:42") == (None, 42, True)
    assert _history_target("case_history_open:v2:7") == (7, None, False)
    assert _history_target("case_history_before:v2:7:42") == (7, 42, False)
    with pytest.raises(ValueError):
        _history_target("case_history_before:0")
    with pytest.raises(ValueError):
        _history_target("case_history_before:broken")

    text = _format_timeline(
        {
            "items": [
                {
                    "occurred_at": "2026-08-06T12:30:00+00:00",
                    "category": "documents",
                    "title": "Документы переданы юристу",
                    "detail": "Передано новых файлов: 2.",
                    "actor_label": "Клиент",
                }
            ]
        },
        case_number="DLC-2026-000042",
    )

    assert "🕘 История дела" in text
    assert "Обращение № DLC-2026-000042" in text
    assert "📄 06.08.2026 · 15:30 МСК" in text
    assert "Документы переданы юристу" in text
    assert "Передано новых файлов: 2." in text
    assert "DOCUMENTS_SENT_TO_REVIEW" not in text


def test_case_activity_service_is_exact_case_scoped_and_cursor_paginated():
    source = read("app/domain/cases/case_activity.py")

    assert 'AuditLog.entity_type == "case"' in source
    assert "AuditLog.entity_id == int(case_id)" in source
    assert "AuditLog.id < int(before_id)" in source
    assert ".limit(bounded_limit + 1)" in source
    assert 'audience: ActivityAudience' in source
    assert "CLIENT_VISIBLE_ACTIONS" in source
    assert "STAFF_ONLY_ACTIONS" in source
    assert "old_value" not in source.split("def present_case_activity", 1)[1]


def test_telegram_history_has_local_recovery_and_no_raw_audit_fallback():
    source = read("app/bot/screens/history.py")

    assert 'c.data == "case_history_open"' in source
    assert "HISTORY_CALLBACK_PREFIX" in source
    assert "Более ранние события" in source
    assert "К последним событиям" in source
    assert "Не удалось загрузить историю. Данные дела сохранены" in source
    assert '("🔄 Повторить", retry_callback)' in source
    assert 'bound_case_callback("message_create", case_id)' in source
    assert '("📁 Моё дело", "my_case_open")' in source
    assert '("🏠 Главная", "nav_home")' in source
    assert "select(AuditLog)" not in source
    assert "CLIENT_ACTION_TITLES" not in source
    assert "Событие по делу" not in source


def test_staff_endpoint_and_case_card_use_same_shared_timeline():
    endpoint = read("app/api/case_timeline.py")
    operator = read("app/api/operator.py")
    ui = read("app/api/workdesk_ui.py")

    assert '@router.get("/admin/workdesk/cases/{case_id}/timeline")' in endpoint
    assert "await db.get(Case, int(case_id))" in endpoint
    assert 'audience="staff"' in endpoint
    assert "before_id=before_id" in endpoint
    assert "limit=limit" in endpoint
    assert "router.include_router(case_timeline_router)" in operator

    assert "/admin/workdesk/cases/'+id+'/timeline?limit=6" in ui
    assert "Promise.allSettled" in ui
    assert "timelineResult.status==='fulfilled'" in ui
    assert "История временно не загружена" in ui
    assert "Карточка дела доступна" in ui
    assert "loadMoreTimeline" in ui
    assert "Показать более ранние" in ui
    assert "if(selected!==id)return" in ui
    assert "Повторить историю" in ui


def test_self_filing_history_exposes_customer_milestones_but_keeps_internal_failures_staff_only():
    selected = present_case_activity(
        audit("SELF_FILING_SERVICE_MODE_SELECTED", actor_type="client"),
        audience="client",
    )
    prepared = present_case_activity(
        audit("SELF_FILING_PACKAGE_READY", actor_type="lawyer"),
        audience="client",
    )
    internal_failure_client = present_case_activity(
        audit(
            "SELF_FILING_EMAIL_DELIVERY_FAILED",
            comment="SMTP diagnostic must stay staff-only",
        ),
        audience="client",
    )
    internal_failure_staff = present_case_activity(
        audit(
            "SELF_FILING_EMAIL_DELIVERY_FAILED",
            comment="SMTP diagnostic must stay staff-only",
        ),
        audience="staff",
    )

    assert selected is not None
    assert selected.title == "Выбран пакет для самостоятельной подачи"
    assert prepared is not None
    assert prepared.title == "Пакет документов готов"
    assert internal_failure_client is None
    assert internal_failure_staff is not None
    assert internal_failure_staff.title == "Email-доставка пакета требует внимания"


def test_lawyer_history_keeps_staff_milestone_but_hides_admin_operational_comment():
    item = present_case_activity(
        audit(
            "SELF_FILING_PAYMENT_REVIEW_RESOLVED",
            comment="Бухгалтерская сверка: внутренний референс 12345",
            actor_type="admin",
        ),
        audience="lawyer",
    )

    assert item is not None
    assert item.title == "Финансовая сверка пакета завершена"
    assert item.detail is None
    assert "внутренний референс" not in str(item.as_dict())


def test_admin_history_still_keeps_bounded_operational_comment():
    item = present_case_activity(
        audit(
            "SELF_FILING_PAYMENT_REVIEW_RESOLVED",
            comment="Бухгалтерская сверка завершена",
            actor_type="admin",
        ),
        audience="staff",
    )

    assert item is not None
    assert item.detail == "Бухгалтерская сверка завершена"


def test_self_filing_email_retry_is_a_staff_milestone_with_role_scoped_detail():
    staff = present_case_activity(
        audit(
            "SELF_FILING_EMAIL_RETRY_REQUESTED",
            comment="Администратор запустил повторную email-доставку готового пакета",
            actor_type="admin_user",
        ),
        audience="staff",
    )
    lawyer = present_case_activity(
        audit(
            "SELF_FILING_EMAIL_RETRY_REQUESTED",
            comment="Внутренний операционный комментарий",
            actor_type="admin_user",
        ),
        audience="lawyer",
    )
    client = present_case_activity(
        audit("SELF_FILING_EMAIL_RETRY_REQUESTED", actor_type="admin_user"),
        audience="client",
    )

    assert staff is not None
    assert staff.title == "Администратор запустил повторную email-доставку"
    assert staff.detail is not None
    assert lawyer is not None
    assert lawyer.detail is None
    assert client is None
