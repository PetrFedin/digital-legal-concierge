from pathlib import Path

from app.api.consultation_outcomes_product import _inject_business_timezone_ui
from app.config import settings

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_admin_consultation_outcome_ui_overrides_browser_timezone(monkeypatch):
    monkeypatch.setattr(settings, "business_timezone", "Europe/Moscow")
    monkeypatch.setattr(settings, "business_timezone_label", "МСК")

    rendered = _inject_business_timezone_ui(
        "<html><body><header><div class='header'><p>Контроль</p></div></header>"
        "<script>function dt(v){return v}</script></body></html>"
    )

    assert "timeZone:businessTimeZone" in rendered
    assert 'const businessTimeZone="Europe/Moscow"' in rendered
    assert 'const businessTimeLabel="МСК"' in rendered
    assert "Время: " in rendered


def test_canonical_outcome_product_applies_timezone_patch_after_all_ui_extensions():
    source = read("app/api/consultation_outcomes_product.py")
    handler = source.split("async def consultation_outcomes_ui(", 1)[1]

    assert "html = _inject_client_no_show_ui(OUTCOMES_HTML)" in handler
    assert "html = inject_legacy_outcome_ui(html)" in handler
    assert "HTMLResponse(_inject_business_timezone_ui(html))" in handler
    assert handler.index("inject_legacy_outcome_ui(html)") < handler.index(
        "_inject_business_timezone_ui(html)"
    )
