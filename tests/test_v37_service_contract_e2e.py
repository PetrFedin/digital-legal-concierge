from __future__ import annotations

import inspect

from app.api.contract_center import contract_center_ui, contract_context, upload_service_contract
from app.api.contract_workspace_ui import (
    contract_aware_lawyer_workspace_ui,
    contract_aware_workspace_html,
)
from app.bot import bot as bot_module
from app.bot.screens import service_contract
from app.domain.cases.service_contract import (
    CLIENT_CONTRACT_CONFIRMED_ACTION,
    CONTRACT_PUBLISHED_ACTION,
    confirm_service_contract,
    publish_service_contract,
)
from app.main import create_app


def _first_route(path: str, method: str = "GET"):
    return next(
        route
        for route in create_app().routes
        if route.path == path and method in (route.methods or set())
    )


def test_contract_center_routes_are_mounted_and_workspace_overlay_wins():
    assert _first_route("/contracts/ui").endpoint is contract_center_ui
    assert _first_route("/contracts/cases/{case_id}").endpoint is contract_context
    assert _first_route(
        "/contracts/cases/{case_id}/document",
        "POST",
    ).endpoint is upload_service_contract
    assert _first_route("/lawyer/workspace/ui").endpoint is contract_aware_lawyer_workspace_ui


def test_lawyer_workspace_makes_contract_artifact_primary_action():
    html = contract_aware_workspace_html()
    assert "M1_CONTRACT_READY" in html
    assert "/contracts/ui?case_id=" in html
    assert "обязательный артефакт до 30 000" in html
    assert "document_id" in html
    assert "SHA-256" in html


def test_staff_contract_publication_is_versioned_and_archives_old_current_versions():
    source = inspect.getsource(publish_service_contract)
    assert CONTRACT_PUBLISHED_ACTION in source
    assert "DocumentStatus.APPROVED" in source
    assert "DocumentStatus.ARCHIVED" in source
    assert "sha256" in source
    assert "M1_CONTRACT_PUBLISHED" in source


def test_client_confirmation_is_bound_to_exact_current_document():
    source = inspect.getsource(confirm_service_contract)
    assert CLIENT_CONTRACT_CONFIRMED_ACTION in source
    assert '"document_id"' in source
    assert '"version"' in source
    assert '"sha256"' in source
    assert "current_service_contract" in source
    assert "M1_WAITING_PAYMENT_30000" in source


def test_legacy_contract_sign_callback_cannot_create_payment():
    source = inspect.getsource(service_contract.legacy_contract_confirmation)
    assert "confirm_service_contract" not in source
    assert "get_or_create_payment" not in source
    assert "contract_open" in source


def test_exact_version_handler_runs_before_legacy_m1_stage_router():
    source = inspect.getsource(bot_module.build_dispatcher)
    assert source.index("service_contract.router") < source.index("m1_stages.router")
    confirm_source = inspect.getsource(service_contract.confirm_exact_service_contract)
    assert "document_id" in confirm_source
    assert "version" in confirm_source
    assert "current_service_contract" in confirm_source


def test_contract_upload_is_closed_after_contract_stage_changes():
    source = inspect.getsource(upload_service_contract)
    assert "_upload_open" in source
    assert "После подтверждения клиентом договор нельзя незаметно заменить" in source
