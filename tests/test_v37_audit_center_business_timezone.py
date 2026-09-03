from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_audit_center_exposes_and_uses_configured_business_timezone():
    source = read("app/api/audit_center.py")

    assert '"business_timezone": settings.business_timezone' in source
    assert '"business_timezone_label": settings.business_timezone_label' in source
    assert "session.business_timezone||'UTC'" in source
    assert "timeZone:businessTimeZone" in source
    assert "dt(a.created_at)" in source


def test_audit_center_uses_canonical_now_main_step_secondary_hierarchy():
    source = read("app/api/audit_center.py")

    html = source.split('AUDIT_CENTER_HTML = r"""', 1)[1]
    assert '>Сейчас<' in html
    assert html.count("Главный следующий шаг") >= 3
    assert '>Вторичные действия<' in html
    assert 'href="/admin/workdesk/ui"' in html
