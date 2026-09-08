from __future__ import annotations

import inspect

from fastapi.routing import iter_route_contexts

from app.api.lawyer_poa import confirm_poa_received
from app.api.lawyer_workspace_rejection_ui import enhanced_lawyer_workspace_html
from app.bot import bot as bot_module
from app.bot.screens import m1_stages, poa_handoff
from app.domain.documents.document_service import DOC_TITLES
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in iter_route_contexts(create_app().routes)
        if route.path == path and method in (route.methods or set())
    )


def test_lawyer_poa_receipt_endpoint_is_mounted_before_legacy_lawyer_routes():
    assert _first_route(
        "/lawyer/cases/{case_id}/poa/received",
        "POST",
    ).endpoint is confirm_poa_received


def test_lawyer_workspace_exposes_explicit_poa_receipt_confirmation():
    html = enhanced_lawyer_workspace_html()
    assert "confirm_poa" in html
    assert "/poa/received" in html
    assert "фактическое получение" in html


def test_client_poa_callback_is_routed_before_legacy_status_mutation():
    source = inspect.getsource(bot_module.build_dispatcher)
    assert source.index("poa_handoff.router") < source.index("m1_stages.router")
    handoff_source = inspect.getsource(poa_handoff.report_poa_ready)
    assert "change_status" not in handoff_source
    assert "CLIENT_POA_READY_REPORTED" not in inspect.getsource(m1_stages.poa_done)


def test_poa_has_dedicated_document_type_and_upload_callback():
    assert DOC_TITLES["POWER_OF_ATTORNEY"] == "Доверенность"
    source = inspect.getsource(poa_handoff.upload_poa_document)
    assert 'document_type="POWER_OF_ATTORNEY"' in source
    assert "DocumentUploadStates.waiting_file" in source
