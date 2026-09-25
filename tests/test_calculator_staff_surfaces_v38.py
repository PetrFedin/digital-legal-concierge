from pathlib import Path
from types import SimpleNamespace

from app.api.workdesk_calculator_projection import _source_projection
from app.api.calculator_builder import (
    _load_review_template,
    router as calculator_builder_router,
)
from app.api.workdesk_product import router as workdesk_router
from app.api.workdesk_runtime_ui import render_workdesk_runtime_html


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _routes(router):
    return {
        (method, route.path)
        for route in router.routes
        for method in getattr(route, "methods", set())
    }


def test_calculation_rule_admin_surface_has_controlled_lifecycle_routes():
    routes = _routes(calculator_builder_router)

    assert ("GET", "/calculator-builder/status") in routes
    assert ("GET", "/calculator-builder/rules") in routes
    assert ("GET", "/calculator-builder/ui") in routes
    assert ("POST", "/calculator-builder/draft") in routes
    assert ("POST", "/calculator-builder/{revision_id}/edit") in routes
    assert ("POST", "/calculator-builder/{revision_id}/clear-section") in routes
    assert ("POST", "/calculator-builder/{revision_id}/validate") in routes
    assert ("POST", "/calculator-builder/{revision_id}/legal-review") in routes
    assert ("POST", "/calculator-builder/{revision_id}/return-draft") in routes
    assert ("POST", "/calculator-builder/{revision_id}/approve") in routes
    assert ("POST", "/calculator-builder/{revision_id}/publish") in routes
    assert ("POST", "/calculator-builder/{revision_id}/retire") in routes


def test_workdesk_mounts_case_bound_calculator_evidence_projection():
    routes = _routes(workdesk_router)

    assert (
        "GET",
        "/admin/workdesk/cases/{case_id}/calculator",
    ) in routes


def test_workdesk_case_card_renders_inputs_separately_from_latest_result():
    html = render_workdesk_runtime_html()

    assert "Исходные данные клиента" in html
    assert "Последний предварительный расчёт" in html
    assert "/admin/workdesk/cases/'+id+'/calculator" in html
    # Routine staff UI exposes a sanitized evidence projection, not the full
    # reproducibility rule snapshot. Applied/excluded segments and only their
    # linked legal sources are intentionally visible.
    assert "rule_snapshot_json" not in html
    assert "⚖️ Основания, ставки, периоды и источники" in html
    assert "Правовые и расчётные источники" in html
    assert "Открыть источник ↗" in html
    assert "applied_segments" in html
    assert "excluded_segments" in html


def test_staff_source_projection_exposes_only_referenced_saved_sources():
    calculation = SimpleNamespace(
        client_type="consumer",
        unique_object=False,
        rule_snapshot={
            "formula": {"source_refs": ["LAW"]},
            "rate_policy": {"source_refs": ["CBR"]},
            "client_types": {
                "consumer": {"source_refs": ["LAW"]},
            },
            "sources": {
                "LAW": {
                    "title": "Правовая норма",
                    "locator": "ч. 2 ст. 6",
                    "url": "https://example.test/law",
                    "checked_at": "2026-09-24",
                },
                "CBR": {
                    "title": "Ставка ЦБ",
                    "locator": "таблица ключевой ставки",
                    "url": "https://example.test/cbr",
                    "checked_at": "2026-09-24",
                },
                "UNUSED": {
                    "title": "Неиспользуемый источник",
                    "locator": "unused",
                    "url": "https://example.test/unused",
                },
            },
        },
        applied_segments=[
            {
                "base_rate_source_refs": ["CBR"],
                "cap_source_refs": [],
            }
        ],
        excluded_segments=[],
    )

    sources = _source_projection(calculation)

    assert [item["code"] for item in sources] == ["LAW", "CBR"]
    assert all(item["url"].startswith("https://") for item in sources)
    assert sources[0]["locator"] == "ч. 2 ст. 6"
    assert "UNUSED" not in {item["code"] for item in sources}


def test_client_can_reopen_calculation_legal_details_from_my_case_and_archive():
    my_case = read("app/bot/screens/my_case.py")
    calculator = read("app/bot/screens/calculator.py")

    assert "🔎 Основания и детализация расчёта" in my_case
    assert 'f"calc_details:v2:{int(view.case_id)}"' in my_case
    assert 'startswith("calc_details:v2:")' in calculator
    assert "get_case_for_user(" in calculator
    assert "format_calculation_details(calculation)" in calculator


def test_reviewed_pm016_template_is_loadable_but_remains_draft_input_only():
    template = _load_review_template()

    assert template["schema_version"] == 2
    assert template["rate_policy"]["mode"] == "due_date"
    assert template["control_examples"]
    assert template["sources"]


def test_rule_editor_uses_guided_fields_with_json_kept_as_advanced_audit_surface():
    builder = read("app/api/calculator_builder.py")
    script = read("app/api/calculator_rule_editor_script.py")

    assert 'data-rule-section=' in builder
    assert 'data-guided-editor' in builder
    assert "Расширенный JSON — для точной проверки и диагностики" in builder
    assert "CALCULATOR_RULE_EDITOR_SCRIPT" in builder
    assert "renderFormula" in script
    assert "renderRatePolicy" in script
    assert "renderControlExamples" in script
    assert "renderSources" in script
    assert "Точное основание: статья / пункт / раздел / таблица" in script
