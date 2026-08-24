from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_payment_review_uses_business_time_and_canonical_guided_hierarchy():
    center = read("app/api/payment_review_center.py")
    guards = read("app/api/staff_ui_guards.py")

    assert "timeZone:businessTimeZone" in center
    assert "s.business_timezone||businessTimeZone" in center
    assert "_inject_payment_review_guided_copy" in guards
    assert "Сейчас · почему требуется сверка" in guards
    assert "Главный следующий шаг" in guards
    assert "Вторичные действия" in guards
    assert "paymentReviewContext" in guards
    assert "_inject_payment_review_guided_copy(PAYMENT_REVIEW_CENTER_HTML)" in guards


def test_document_review_has_context_now_main_step_and_confirmed_destructive_decisions():
    source = read("app/api/document_review.py")
    guards = read("app/api/staff_ui_guards.py")

    assert '<span class="section-label">Сейчас</span>' in source
    assert '<span>Главный следующий шаг</span>' in source
    assert "context.case_number" in source
    assert "Проверить решение" in source
    assert "Подтвердить решение" in source
    assert "expected_status:x.status" in source
    assert "expected_version:x.version" in source
    assert "expected_updated_at:x.updated_at" in source
    assert "_inject_business_timezone_ui(REVIEW_HTML)" in guards


def test_sla_runtime_ui_is_business_timezone_and_uses_main_step_language():
    guards = read("app/api/staff_ui_guards.py")

    assert "_inject_business_timezone_ui(SLA_CENTER_HTML)" in guards
    assert "_inject_sla_guided_copy(html)" in guards
    assert "<b>Сейчас</b>" in guards
    assert "<b>Главный следующий шаг</b>" in guards


def test_message_center_has_context_now_main_step_recovery_and_business_time():
    base = read("app/api/message_center.py")
    guided = read("app/api/guided_message_center.py")
    role_ui = read("app/api/message_center_role_ui_impl.py")

    assert '<div class="eyebrow">Контекст клиента</div>' in base
    assert '<div class="eyebrow">СЕЙЧАС</div>' in base
    assert "ГЛАВНЫЙ СЛЕДУЮЩИЙ ШАГ" in base
    assert "expected_last_message_id:lastMessageId" in base
    assert "В диалоге появились новые сообщения" in base
    assert "Черновик сохранён" in base
    assert "currentState.innerHTML" in guided
    assert "nextStepTitle.textContent" in guided
    assert "timeZone:businessTimeZone" in role_ui
    assert "formatBusinessTime(m.created_at)" in role_ui


def test_staff_palette_remains_consistent_on_decision_surfaces():
    document_review = read("app/api/document_review.py")
    payment_review = read("app/api/payment_review_center.py")

    for token in ("#f4f6fa", "#172033", "#667085", "#e4e7ec", "#3157d5"):
        assert token in document_review
        assert token in payment_review
