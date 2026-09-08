from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_notification_delivery_ui_accepts_admin_and_superadmin_sessions():
    source = read("app/api/notification_delivery.py")

    html = source.split('DELIVERY_HTML = r"""', 1)[1]
    assert "roles.includes('admin')||roles.includes('superadmin')" in html
    assert "Требуется роль администратора или суперадминистратора" in html


def test_notification_delivery_uses_business_timezone_for_all_visible_timestamps():
    source = read("app/api/notification_delivery.py")

    html = source.split('DELIVERY_HTML = r"""', 1)[1]
    assert "businessTimeZone=s.business_timezone||'UTC'" in html
    assert "businessTimeLabel=s.business_timezone_label||businessTimeZone" in html
    assert "timeZone:businessTimeZone" in html
    assert "dt(x.created_at)" in html
    assert "dt(x.next_attempt_at)" in html
    assert "dt(d.generated_at)" in html


def test_notification_delivery_fresh_case_links_use_canonical_workdesk():
    source = read("app/api/notification_delivery.py")

    html = source.split('DELIVERY_HTML = r"""', 1)[1]
    assert 'href="/admin/workdesk/ui?case_id=${x.case_id}"' in html
    assert 'href="/admin/notification-delivery/case/${x.case_id}/ui"' not in html


def test_notification_delivery_uses_guided_now_main_step_secondary_hierarchy():
    source = read("app/api/notification_delivery.py")

    html = source.split('DELIVERY_HTML = r"""', 1)[1]
    assert '>Сейчас<' in html
    assert html.count("Главный следующий шаг") >= 5
    assert '>Вторичные действия<' in html
    assert 'href="/admin/workdesk/ui"' in html
