from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_security_status_exposes_business_timezone_and_generation_time():
    source = read("app/api/security_event_center.py")

    assert '"business_timezone": settings.business_timezone' in source
    assert '"business_timezone_label": settings.business_timezone_label' in source
    assert '"generated_at": now.isoformat()' in source


def test_security_ui_formats_event_and_freshness_times_in_business_timezone():
    source = read("app/api/security_event_center.py")

    html = source.split('SECURITY_EVENT_HTML = r"""', 1)[1]
    assert "businessTimeZone=s.business_timezone||'UTC'" in html
    assert "businessTimeLabel=s.business_timezone_label||businessTimeZone" in html
    assert "timeZone:businessTimeZone" in html
    assert "dt(d.generated_at)" in html
    assert "dt(e.created_at)" in html


def test_security_ui_follows_context_now_main_step_secondary_recovery_pattern():
    source = read("app/api/security_event_center.py")

    html = source.split('SECURITY_EVENT_HTML = r"""', 1)[1]
    assert "Роль: суперадминистратор" in html
    assert '>Сейчас<' in html
    assert html.count("Главный следующий шаг") >= 5
    assert '>Вторичные действия<' in html
    assert "не выполняйте изменения доступа, MFA или ключей вслепую" in html
    assert 'href="/admin/workdesk/ui"' in html
