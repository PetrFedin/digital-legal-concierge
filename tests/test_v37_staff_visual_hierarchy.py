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


def test_workdesk_case_card_uses_context_now_main_step_without_breaking_m2_projection():
    base = read("app/api/workdesk_ui.py")
    runtime = read("app/api/workdesk_runtime_ui.py")

    assert "timeZone:businessTimeZone" in base
    assert "business_timezone" in base
    assert "function applyGuidedCaseHierarchy()" in runtime
    assert "heading.textContent='Сейчас'" in runtime
    assert "label.className='eyebrow main-step-label'" in runtime
    assert "label.textContent='Главный следующий шаг'" in runtime
    assert "const actionText=actionBox.querySelector('div')" in runtime
    assert "actionBox.insertBefore(label,actionText)" in runtime
    assert "applyGuidedCaseHierarchy();" in runtime
    # The M2 product extension still updates the original action div. Keeping
    # the new hierarchy label as a span avoids changing this selector contract.
    assert "const primary=cv.querySelector('.action div')" in runtime


def test_lawyer_consultation_desk_already_uses_canonical_card_hierarchy_and_business_time():
    desk = read("app/api/lawyer_consultation_desk.py")

    assert "business_timezone" in desk
    assert "business_timezone_label" in desk
    assert "timeZone:businessTimeZone" in desk
    assert ".now-box" in desk
    assert ".next-box" in desk
    assert ".secondary-actions" in desk
    assert ".exception" in desk
    assert "Один главный следующий шаг по каждой встрече" in desk


def test_consultation_outcomes_product_separates_now_main_step_and_secondary_actions():
    product = read("app/api/consultation_outcomes_product.py")
    base = read("app/api/consultation_outcomes.py")

    assert "timeZone:businessTimeZone" in product
    assert "function applyGuidedHierarchy()" in product
    assert "now.textContent='Сейчас'" in product
    assert "nextHeading.textContent='Главный следующий шаг'" in product
    assert "heading.textContent='Вторичные действия'" in product
    assert "actions.querySelectorAll('a.button.secondary')" in product
    assert "links.forEach(link=>row.appendChild(link))" in product
    assert "const previousRender=render" in product
    assert "applyGuidedHierarchy();" in product
    # The business forms remain collapsed and confirm before mutation.
    assert "Ничего не изменится" in base
    assert "Подтвердить неявку" in base


def test_document_access_portal_exposes_exact_case_and_role_context_before_files():
    portal = read("app/api/document_access_portal.py")
    product = read("app/api/document_access_product.py")

    assert 'id="caseContext"' in portal
    assert 'id="caseBadge"' in portal
    assert 'id="roleBadge"' in portal
    assert "applyContext(id)" in portal
    assert "'/message-center/ui?case_id='+id" in portal
    assert "Одноразовая защищённая выдача" in portal
    assert '"/cases/{case_id}/documents"' in product
    assert '"/documents/{document_id}/grant"' in product
    assert '"/grants/{public_id}/download"' in product


def test_staff_palette_remains_consistent_on_decision_surfaces():
    document_review = read("app/api/document_review.py")
    payment_review = read("app/api/payment_review_center.py")

    for token in ("#f4f6fa", "#172033", "#667085", "#e4e7ec", "#3157d5"):
        assert token in document_review
        assert token in payment_review



def test_rejected_placeholder_brand_is_absent_from_visible_staff_surfaces():
    visible_sources = (
        "app/api/auth.py",
        "app/api/operator.py",
        "app/api/workdesk_ui.py",
        "app/api/web_admin.py",
        "app/admin/case_detail_page.py",
        "app/api/calculator_builder.py",
        "app/api/case_action_ui.py",
        "app/api/document_review.py",
        "app/api/lawyer_consultation_desk.py",
        "app/api/message_center.py",
        "app/api/notification_delivery.py",
        "app/api/search_center.py",
        "app/api/settings_ui.py",
        "app/api/diagnostic_center.py",
    )
    for path in visible_sources:
        assert "Digital Legal Concierge" not in read(path), path


def test_self_filing_staff_card_has_state_aware_role_controls_and_neutral_refresh():
    source = read("app/api/self_filing_product.py")

    assert 'id="startReviewButton"' in source
    assert 'id="requestDocsButton"' in source
    assert 'id="approveButton"' in source
    assert 'id="retryEmailButton"' in source
    assert "const canStartReview=" in source
    assert "const canReviewDecision=" in source
    assert "const canFinance=" in source
    assert "M1_SELF_FILING_PAYMENT_PENDING" not in source.split(
        "const canReviewDecision=", 1
    )[1].split(";", 1)[0]
    assert "document.querySelectorAll('button')" not in source
    assert '<button class="secondary" onclick="load()">Обновить</button>' in source
    assert 'id="lawyerHome"' in source
    assert 'id="adminHome"' in source
    assert 'id="readinessLink"' in source
    assert "lawyerNav.style.display=a.role==='lawyer'" in source
    assert "adminNav.style.display=['admin','superadmin'].includes" in source
    assert "readinessNav.style.display=['admin','superadmin'].includes" in source
    assert 'id="lawyerActionCard"' in source
    assert "lawyerActionCard').style.display=a.can_mutate?'block':'none'" in source
    assert "if(reviewPayment&&a.can_financial_reconcile)" in source
    assert "retryEmailButton.style.display=a.can_mutate?'':'none'" in source
    assert "Операционная роль: можно сверять фактическую оплату" in source


def test_admin_detailed_case_link_has_a_registered_authenticated_page_owner():
    source = read("app/api/web_admin.py")
    page = read("app/admin/case_detail_page.py")

    assert '@router.get("/admin/cases/{case_id}/ui", response_class=HTMLResponse)' in source
    assert "request.cookies.get(settings.admin_session_cookie)" in source
    assert "require_admin(token)" in source
    assert 'CASE_DETAIL_HTML.replace("__CASE_ID__", str(int(case_id)))' in source
    assert "<title>Карточка дела</title>" in page
    assert "Подтвердить поступление" in page


def test_admin_case_workspace_contains_the_specification_context_without_chat_reconstruction():
    source = read("app/api/web_admin.py")
    page = read("app/admin/case_detail_page.py")

    for token in (
        '"phone": client.phone',
        '"email": client.email',
        '"telegram_id": client.telegram_id',
        '"calculation": (',
        '"communications": {',
        '"activity": activity',
        'CaseActivityService(db).page(',
        'select(Calculation)',
    ):
        assert token in source

    for label in (
        "Клиент и дело",
        "Расчёт",
        "Документы",
        "Платежи",
        "История процесса",
        "Коммуникации",
        "Ближайшее действие",
    ):
        assert label in page

    assert "Кабинет юриста" not in page
    assert "не принимает юридическое решение по делу" in page
