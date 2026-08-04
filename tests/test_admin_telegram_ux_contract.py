from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_admin_dashboard_exposes_actionable_work_queues():
    source = read("app/admin/admin_dashboard.py")

    for field in (
        '"cases"',
        '"queue"',
        '"payments"',
        '"documents"',
        '"consultations"',
        '"generated_at"',
    ):
        assert field in source
    for queue in (
        '"unassigned"',
        '"documents_review"',
        '"consultations_today"',
        '"overdue"',
    ):
        assert queue in source


def test_admin_workspace_has_real_queue_endpoints_and_recovery_states():
    source = read("app/api/web_admin.py")

    assert '@router.get("/admin/work-queues/{queue_name}")' in source
    assert 'QUEUE_NAMES = {"unassigned", "documents", "consultations", "overdue"}' in source
    assert 'Case.assigned_lawyer_id.is_(None)' in source
    assert 'Document.status.in_(DOCUMENT_REVIEW_STATUSES)' in source
    assert 'Consultation.status == "BOOKED"' in source
    assert 'Case.sla_status.in_(["FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"])' in source
    assert "Не удалось загрузить раздел" in source
    assert "Повторить" in source
    assert "Очередь пуста" in source
    assert "Открыть дело" in source
    assert "Автоназначить" in source


def test_telegram_case_actions_are_current_state_driven():
    source = read("app/bot/screens/my_case.py")

    assert 'f"next_action:{case.id}:{case.status}"' in source
    assert "requested_case_id != case.id" in source
    assert "requested_status != current_status" in source
    assert "Статус дела уже изменился" in source
    assert "Показан актуальный следующий шаг" in source
    assert "Сейчас действие от вас не требуется" in source
    assert "Совместимость со старыми сообщениями" in source
    assert "CLIENT_ACTIONS" in source
    assert "client_action_for(case)" in source


def test_telegram_case_screen_does_not_show_payment_controls_when_disabled():
    source = read("app/bot/screens/my_case.py")

    assert "if not payments_disabled():" in source
    assert '("💳 Оплаты", "payments_open")' in source
    assert "payments_count = 0" in source
    assert "В режиме без онлайн-оплаты" not in source
