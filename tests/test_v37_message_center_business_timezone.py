from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_message_center_overrides_raw_iso_with_configured_business_timezone():
    source = read("app/api/message_center_role_ui_impl.py")

    assert "settings.business_timezone" in source
    assert "settings.business_timezone_label" in source
    assert "Intl.DateTimeFormat('ru-RU'" in source
    assert "timeZone:businessTimeZone" in source
    assert "formatBusinessTime(m.created_at)" in source
    assert "Время: ${businessTimeLabel||businessTimeZone}" in source


def test_timezone_patch_is_applied_to_the_role_safe_canonical_ui():
    source = read("app/api/message_center_role_ui_impl.py")
    product = read("app/api/message_center_product.py")

    renderer = source.split("def role_safe_message_center_html", 1)[1].split(
        "@router.get", 1
    )[0]
    assert "_inject_message_center_patch(MESSAGE_CENTER_HTML)" in renderer
    assert "_inject_business_timezone_ui(html)" in renderer
    assert "role_safe_message_center_ui" in product
