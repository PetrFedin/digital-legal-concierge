from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from app.bot.case_dashboard_renderer import render_lawyer
from app.domain.cases.client_dashboard_privacy import sanitize_case_dashboard


@dataclass(frozen=True, slots=True)
class FakeLawyerView:
    exists: bool
    name: str
    specialization: str | None
    phone: str | None
    email: str | None


@dataclass(frozen=True, slots=True)
class FakeDashboard:
    lawyer: FakeLawyerView
    summary: object


def test_sanitizer_removes_internal_contacts_without_mutating_source():
    source = FakeDashboard(
        lawyer=FakeLawyerView(
            exists=True,
            name="Анна Юристова",
            specialization="Споры по ДДУ",
            phone="+7-900-INTERNAL",
            email="internal-lawyer@example.test",
        ),
        summary=SimpleNamespace(case_number="PRIVACY-1"),
    )

    sanitized = sanitize_case_dashboard(source)

    assert sanitized is not source
    assert sanitized.lawyer is not source.lawyer
    assert sanitized.lawyer.phone is None
    assert sanitized.lawyer.email is None
    assert source.lawyer.phone == "+7-900-INTERNAL"
    assert source.lawyer.email == "internal-lawyer@example.test"


def test_lawyer_renderer_receives_only_sanitized_client_view():
    source = FakeDashboard(
        lawyer=FakeLawyerView(
            exists=True,
            name="Анна <Юристова>",
            specialization="ДДУ & недвижимость",
            phone="+7-900-INTERNAL",
            email="internal-lawyer@example.test",
        ),
        summary=SimpleNamespace(case_number="PRIVACY-2"),
    )

    text, _ = render_lawyer(sanitize_case_dashboard(source))

    assert "Анна <Юристова>" in text
    assert "ДДУ & недвижимость" in text
    assert "+7-900-INTERNAL" not in text
    assert "internal-lawyer@example.test" not in text


def test_my_case_handler_applies_privacy_to_all_dashboard_builds():
    source = open("app/bot/screens/my_case.py", encoding="utf-8").read()

    assert source.count("sanitize_case_dashboard(") >= 2
    assert "await service.build_dashboard(" in source
