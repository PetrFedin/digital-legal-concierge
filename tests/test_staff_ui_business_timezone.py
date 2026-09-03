from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_browser_session_exposes_only_non_secret_business_time_metadata():
    auth = read("app/api/auth.py")

    session = auth.split("async def auth_session", 1)[1].split(
        '@router.post("/logout")', 1
    )[0]
    assert '"api_token": BROWSER_SESSION_SENTINEL' in session
    assert '"session_transport": "httponly_cookie"' in session
    assert '"business_timezone": settings.business_timezone' in session
    assert '"business_timezone_label": settings.business_timezone_label' in session


def test_workdesk_uses_configured_business_timezone_for_every_shared_datetime():
    ui = read("app/api/workdesk_ui.py")

    assert "businessTimeZone='Europe/Moscow'" in ui
    assert "businessTimeLabel='МСК'" in ui
    assert "timeZone:businessTimeZone" in ui
    assert "businessTimeZone=s.business_timezone||businessTimeZone" in ui
    assert "businessTimeLabel=s.business_timezone_label??businessTimeLabel" in ui
    # The shared dt() helper is used by overview, queues, SLA and timeline.
    assert "dt(x.updated_at)" in ui
    assert "dt(x.created_at)" in ui
    assert "dt(d.case.sla_due_at)" in ui
    assert "dt(x.occurred_at)" in ui


def test_payment_review_and_refund_surfaces_do_not_use_browser_local_time():
    review = read("app/api/payment_review_center.py")
    refunds = read("app/api/refund_center.py")

    for source in (review, refunds):
        assert "timeZone:businessTimeZone" in source
        assert "businessTimeZone=s.business_timezone||businessTimeZone" in source
        assert "businessTimeLabel=s.business_timezone_label??businessTimeLabel" in source

    assert "formatDate(x.scheduled_at)" in review
    assert "formatDate(s.starts_at)" in review
    assert "formatDate(x.requested_at)" in refunds


def test_financial_surfaces_return_to_canonical_exact_workdesk_case():
    review = read("app/api/payment_review_center.py")
    refund_product = read("app/api/refund_product.py")
    refund_ui = read("app/api/refund_center.py")
    runtime = read("app/api/workdesk_runtime_ui.py")

    assert 'f"/admin/workdesk/ui?case_id={case.id}"' in review
    assert '`<a class="button" href="/admin/workdesk/ui?case_id=${terminalCaseId}">Вернуться в дело</a>`' in review
    assert 'f"/admin/workdesk/ui?case_id={int(case.id)}"' in refund_product
    assert '`<a class="button" href="/admin/workdesk/ui?case_id=${terminalCaseId}">Вернуться в дело</a>`' in refund_ui

    # The target is not just a decorative query parameter: the canonical
    # Workdesk runtime validates a positive integer and opens that exact Case.
    assert "const requestedCaseId=Number(" in runtime
    assert "Number.isInteger(requestedCaseId)&&requestedCaseId>0" in runtime
    assert "void openCase(requestedCaseId)" in runtime

    assert "/admin/cases/${terminalCaseId}/ui" not in review
    assert "/admin/cases/${terminalCaseId}/ui" not in refund_ui
