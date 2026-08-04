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
    assert '@router.get("/admin/case-workspace/{case_id}")' in source
    assert 'QUEUE_NAMES = {"unassigned", "documents", "consultations", "overdue"}' in source
    assert 'Case.assigned_lawyer_id.is_(None)' in source
    assert 'Document.status.in_(DOCUMENT_REVIEW_STATUSES)' in source
    assert 'Consultation.status == "BOOKED"' in source
    assert 'Case.sla_status.in_(["FIRST_RESPONSE_OVERDUE", "ACTION_OVERDUE"])' in source
    assert "status_label" in source
    assert "lawyer_name" in source
    assert "sla_label" in source
    assert "lawyer_comment" in source
    assert "Рекомендуемое действие" in source
    assert "Не удалось загрузить раздел" in source
    assert "Повторить" in source
    assert "Очередь пуста" in source
    assert "Открыть дело" in source
    assert "Автоназначить" in source


def test_document_review_queue_matches_domain_status():
    service = read("app/domain/documents/document_service.py")
    dashboard = read("app/admin/admin_dashboard.py")
    workspace = read("app/api/web_admin.py")

    assert "document.status = DocumentStatus.ON_REVIEW" in service
    assert '"ON_REVIEW"' in dashboard
    assert '"ON_REVIEW"' in workspace
    assert '"ON_REVIEW": "На проверке у юриста"' in workspace


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


def test_telegram_lawyer_contact_matches_no_payment_mode_and_has_no_implicit_dead_end():
    source = read("app/bot/screens/messages.py")

    assert "payments_disabled()" in source
    assert "записаться на консультацию без онлайн-оплаты" in source
    assert "платную консультацию" not in source
    assert "оплатите встречу" not in source
    assert "После отправки вопроса будет создано новое обращение" in source
    assert '("✉️ Задать вопрос", "message_create")' in source
    assert '("🧮 Рассчитать неустойку", "calc_start")' in source


def test_telegram_message_failures_rollback_and_offer_recovery():
    source = read("app/bot/screens/messages.py")

    assert source.count("await db.rollback()") >= 2
    assert "Не удалось загрузить переписку" in source
    assert '("🔄 Повторить", "message_history")' in source
    assert "Не удалось отправить вопрос" in source
    assert '("🔄 Начать отправку заново", "message_create")' in source
    assert "Текст не был зарегистрирован" in source


def test_telegram_stale_buttons_return_to_current_state_without_demo_language():
    source = read("app/bot/screens/common.py")

    assert 'c.data == "noop"' in source
    assert "Эта кнопка больше не актуальна" in source
    assert "_home_text(db, callback)" in source
    assert "DEV подтверждения" not in source
    assert "В тестовом режиме" not in source
    assert "PILOT_NEXT_ACTIONS" in source
    assert "Онлайн-оплата сейчас отключена" in source


def test_telegram_document_list_uses_client_statuses_not_security_codes():
    source = read("app/bot/screens/documents.py")

    assert "def _client_document_status" in source
    assert '"UPLOADED": "Безопасно загружен"' in source
    assert '"PENDING_REVIEW": "Проверяет юрист"' in source
    assert '"APPROVED": "Принят юристом"' in source
    assert '"REJECTED": "Нужно заменить файл"' in source
    assert "Что исправить:" in source
    assert "безопасность:" not in source
    assert "хранение:" not in source


def test_telegram_document_empty_and_failure_states_have_safe_exits():
    source = read("app/bot/screens/documents.py")

    assert "if not case:" in source
    assert "_new_case_buttons()" in source
    assert "Пока документов нет" in source
    assert "Продолжить без документов" in source
    assert '("🔄 Повторить передачу", "doc_finish_upload")' in source
    assert "Загруженные файлы сохранены" in source
    assert "Документ не сохранён" in source
    assert '("🏠 Главная", "nav_home")' in source


def test_consultation_related_case_is_verified_for_current_client():
    service = read("app/domain/consultations/consultation_service.py")
    screen = read("app/bot/screens/consultations.py")

    assert "def _validate_related_case" in service
    assert "Case.id == related_case_id" in service
    assert "Case.client_id == client_id" in service
    assert "принадлежит другому клиенту" in service
    assert "verified_related_case_id" in service
    assert "Case.id == case_id" in screen
    assert "Case.client_id == user.id" in screen
    assert "Выбранное дело больше недоступно" in screen


def test_consultation_screen_uses_human_status_and_recovers_stale_buttons():
    source = read("app/bot/screens/consultations.py")

    assert "CONSULTATION_STATUS_LABELS" in source
    assert 'ConsultationStatus.BOOKED: "Консультация подтверждена"' in source
    assert "consultation_status_label(consultation.status)" in source
    assert 'f"Статус: {consultation.status}' not in source
    assert "booking_recovery_buttons()" in source
    assert "Эта кнопка выбора времени больше не актуальна" in source
    assert "Эта кнопка переноса больше не актуальна" in source
    assert "Текущая запись сохранена" in source
    assert '("🔄 Повторить отмену", "consult_cancel_confirm")' in source
    assert '("📁 Моё дело", "my_case_open")' in source
