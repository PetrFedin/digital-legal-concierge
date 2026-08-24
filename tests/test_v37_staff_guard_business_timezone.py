from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_staff_guard_injects_one_configured_business_timezone_formatter():
    source = read("app/api/staff_ui_guards.py")

    assert "settings.business_timezone" in source
    assert "settings.business_timezone_label" in source
    assert "_RAW_BROWSER_DT_RENDERERS" in source
    assert "new Date(v).toLocaleString('ru-RU')" in source
    assert "new Intl.DateTimeFormat('ru-RU',{dateStyle:'short',timeStyle:'short'})" in source
    assert "timeZone:businessTimeZone" in source
    assert "expected exactly one browser-local dt renderer" in source


def test_document_review_and_sla_use_guard_timezone_patch_but_payment_keeps_own_owner():
    source = read("app/api/staff_ui_guards.py")

    sla = source.split("async def protected_sla_ui", 1)[1].split(
        "async def protected_document_review_ui", 1
    )[0]
    document = source.split("async def protected_document_review_ui", 1)[1].split(
        "@router.get", 1
    )[0]
    payment = source.split("async def protected_payment_review_ui", 1)[1].split(
        "async def protected_sla_ui", 1
    )[0]

    assert "_inject_business_timezone_ui(SLA_CENTER_HTML)" in sla
    assert "_inject_sla_guided_copy(html)" in sla
    assert "_inject_business_timezone_ui(REVIEW_HTML)" in document
    assert "_inject_business_timezone_ui(PAYMENT_REVIEW_CENTER_HTML)" not in payment


def test_sla_card_uses_now_then_main_next_step_before_secondary_actions():
    source = read("app/api/staff_ui_guards.py")

    assert '<div class="rule"><b>Сейчас</b></div>' in source
    assert '"<b>Главный следующий шаг</b>"' in source
    assert "guided-card markers not found" in source


def test_staff_review_product_routes_keep_guarded_canonical_ui_owners():
    document_product = read("app/api/document_access_product.py")
    sla_product = read("app/api/sla_product.py")

    assert "protected_document_review_ui" in document_product
    assert "protected_sla_ui" in sla_product
