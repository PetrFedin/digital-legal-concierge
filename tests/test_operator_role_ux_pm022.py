from pathlib import Path


SOURCE = Path("app/api/operator.py").read_text(encoding="utf-8")


def test_superadmin_label_does_not_duplicate_inherited_admin_role() -> None:
    assert (
        "if(roles.includes('superadmin'))labels.push('Суперадминистратор');"
        "else if(roles.includes('admin'))labels.push('Администратор');"
        in SOURCE
    )


def test_admin_daily_work_exposes_consultation_schedule() -> None:
    assert (
        "link('/consultation-slots/ui','Расписание',"
        "'Свободные слоты, резервы и время юристов')"
        in SOURCE
    )


def test_superadmin_has_one_coherent_leadership_workspace() -> None:
    start = SOURCE.index("if(isSuperAdmin){")
    end = SOURCE.index("systemLinks.innerHTML=systemGroups;", start)
    leadership = SOURCE[start:end]

    assert "group('Руководительский контроль'" in leadership
    for href in (
        "/access/ui",
        "/audit-center/ui",
        "/backup-center/ui",
        "/retention/ui",
    ):
        assert f"link('{href}'" in leadership


def test_admin_control_links_are_not_duplicated_between_system_groups() -> None:
    start = SOURCE.index("let systemGroups=group(")
    end = SOURCE.index("if(isSuperAdmin){", start)
    admin_system = SOURCE[start:end]

    assert admin_system.count("link('/admin/sla/ui'") == 1
    assert admin_system.count("link('/admin/notification-delivery/ui'") == 1
