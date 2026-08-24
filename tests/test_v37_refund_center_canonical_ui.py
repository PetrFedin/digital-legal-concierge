from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_refund_center_uses_business_timezone_and_explicit_admin_context():
    source = read("app/api/refund_center.py")

    html = source.split('REFUND_CENTER_HTML = r"""', 1)[1]
    assert "roles.includes('admin')||roles.includes('superadmin')" in html
    assert "businessTimeZone=s.business_timezone||businessTimeZone" in html
    assert "timeZone:businessTimeZone" in html
    assert "formatDate(x.requested_at)" in html
    assert "Роль: администратор / суперадминистратор" in html


def test_refund_center_uses_now_main_step_secondary_actions_before_queue():
    source = read("app/api/refund_center.py")

    html = source.split('REFUND_CENTER_HTML = r"""', 1)[1]
    assert '>Сейчас<' in html
    assert html.count("Главный следующий шаг") >= 5
    assert '>Вторичные действия<' in html
    assert html.index('>Сейчас<') < html.index('>Рабочая очередь<')


def test_declined_retry_patch_updates_same_summary_and_never_claims_money_moved():
    source = read("app/api/refund_resolution_guard_impl.py")

    patch = source.split('_REFUND_RETRY_UI_PATCH = r"""', 1)[1].split(
        'def _refund_ui_html', 1
    )[0]
    assert "updateRefundSummary(visible)" in patch
    assert "Это не отправляет деньги и не меняет этап дела" in patch
    assert "Сначала устраните причину отказа" in patch
    assert "Главный следующий шаг" in patch


def test_refund_product_keeps_server_authenticated_single_ui_owner():
    product = read("app/api/refund_product.py")
    guard = read("app/api/refund_resolution_guard_impl.py")

    assert "refund_ui_guard" in product
    ui = guard.split("async def refund_ui_guard", 1)[1]
    assert "await _admin(request, db, x_admin_token)" in ui
    assert 'RedirectResponse(url="/login"' in ui
