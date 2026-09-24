from types import SimpleNamespace

from app.api.workdesk_calculator_projection import _source_projection
from app.api.calculator_builder import router as calculator_builder_router
from app.api.workdesk_product import router as workdesk_router
from app.api.workdesk_runtime_ui import render_workdesk_runtime_html


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
                    "url": "https://example.test/law",
                    "checked_at": "2026-09-24",
                },
                "CBR": {
                    "title": "Ставка ЦБ",
                    "url": "https://example.test/cbr",
                    "checked_at": "2026-09-24",
                },
                "UNUSED": {
                    "title": "Неиспользуемый источник",
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
    assert "UNUSED" not in {item["code"] for item in sources}
