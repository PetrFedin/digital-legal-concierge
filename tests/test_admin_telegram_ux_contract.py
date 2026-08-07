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
    workflow = read("app/domain/documents/document_workflow.py")
    dashboard = read("app/admin/admin_dashboard.py")
    workspace = read("app/api/web_admin.py")

    assert "document.status = DocumentStatus.ON_REVIEW" in service
    assert 'ACTIONABLE_REVIEW_STATUSES = frozenset({DocumentStatus.ON_REVIEW.value})' in workflow
    assert "ACTIONABLE_REVIEW_STATUSES" in dashboard
    assert "Document.status.in_(tuple(ACTIONABLE_REVIEW_STATUSES))" in dashboard
    assert "DOCUMENT_REVIEW_STATUSES = ACTIONABLE_REVIEW_STATUSES" in workspace
    assert "Document.status.in_(DOCUMENT_REVIEW_STATUSES)" in workspace
    assert '"ON_REVIEW": "На проверке у юриста"' in workspace
    assert '"UPLOADED": "Ожидает передачи юристу"' in workspace


def test_telegram_case_actions_are_current_state_driven():
    screen = read("app/bot/screens/my_case.py")
    presenter = read("app/bot/client_case_view.py")

    assert 'f"next_action:v2:{view.case_id}:{view.action_key}"' in screen
    assert "requested_case_id != case.id" in screen
    assert "requested_action_key != view.action_key" in screen
    assert "requested_status != view.case_status" in screen
    assert "Данные дела или документов уже изменились" in screen
    assert "Показан актуальный следующий шаг" in screen
    assert "Сейчас действие от вас не требуется" in screen
    assert "Compatibility with messages" in screen
    assert "CLIENT_ACTIONS" in presenter
    assert "def client_action_for" in presenter
    assert "documents.uploaded_count" in presenter
    assert '"doc_finish_upload"' in presenter


def test_telegram_case_screen_does_not_show_payment_controls_when_disabled():
    screen = read("app/bot/screens/my_case.py")
    presenter = read("app/bot/client_case_view.py")

    assert "if not payments_disabled():" in screen
    assert '("💳 Оплаты", "payments_open")' in screen
    assert presenter.count("if not payments_disabled():") >= 2
    assert "payments_summary = None" in presenter
    assert "select(Payment.id)" in presenter
    assert "В режиме без онлайн-оплаты" not in screen
    assert "В режиме без онлайн-оплаты" not in presenter


def test_telegram_lawyer_contact_matches_no_payment_mode_and_has_no_implicit_dead_end():
    source = read("app/bot/screens/messages.py")

    assert "payments_disabled()" in source
    assert "записаться на консультацию без онлайн-оплаты" in source
    assert "платную консультацию" not in source
    assert "оплатите встречу" not in source
    assert "После подтверждения вопроса будет создано новое обращение" in source
    assert '("✉️ Задать вопрос", "message_create")' in source
    assert '("🧮 Рассчитать неустойку", "calc_start")' in source


def test_telegram_message_failures_rollback_preserve_draft_and_offer_recovery():
    source = read("app/bot/screens/messages.py")

    assert source.count("await db.rollback()") >= 2
    assert "Не удалось загрузить переписку" in source
    assert '("🔄 Повторить", f"message_history:{requested_page}")' in source
    assert 'c.data == "message_history"' in source
    assert "Не удалось зарегистрировать вопрос" in source
    assert '("🔄 Повторить отправку", "message_submit")' in source
    assert "черновик сохранён" in source
    assert '("✏️ Изменить текст", "message_edit_text")' in source


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
    assert '"PENDING_REVIEW": "Статус уточняется"' in source
    assert '"ON_REVIEW": "Проверяет юрист"' in source
    assert '_DOCUMENT_REVIEW_STATUSES = {"ON_REVIEW"}' in source
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
    intake_screen = read("app/bot/screens/consultation_intake.py")

    assert "def _validate_related_case" in service
    assert "Case.id == related_case_id" in service
    assert "Case.client_id == client_id" in service
    assert "принадлежит другому клиенту" in service
    assert "verified_related_case_id" in service
    assert "Case.id == case_id" in intake_screen
    assert "Case.client_id == user.id" in intake_screen
    assert "Выбранное дело больше недоступно" in intake_screen


def test_consultation_screens_use_human_status_and_recover_stale_buttons():
    intake_source = read("app/bot/screens/consultation_intake.py")
    change_source = read("app/bot/screens/consultations.py")

    assert "CONSULTATION_STATUS_LABELS" in intake_source
    assert 'ConsultationStatus.BOOKED: "Консультация подтверждена"' in intake_source
    assert "_consultation_status_label(consultation.status)" in intake_source
    assert 'f"Статус: {consultation.status}' not in intake_source
    assert "Эта кнопка выбора времени больше не актуальна" in intake_source
    assert "booking_recovery_buttons()" in change_source
    assert "Эта кнопка переноса больше не актуальна" in change_source
    assert "Текущая запись сохранена" in change_source
    assert '("🔄 Повторить отмену", "consult_cancel_confirm")' in change_source
    assert '("📁 Моё дело", "my_case_open")' in change_source
