"""A configured test installation can NEVER claim customer or production acceptance.

The six release surfaces historically conflated local files and a fake payment
adapter with legal/provider/client sign-off. These are executable regressions:
no forged live evidence, external providers, or production resources required.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from app.api import (
    acceptance_center,
    final_handover_center,
    go_live_center,
    production_center,
)
from app.domain.release_admission import runtime_release_admission
from app.main import app


def test_runtime_admission_is_fail_closed_and_traces_real_requirements():
    result = runtime_release_admission()
    assert result["status"] == "HOLD"
    assert result["release_authorized"] is False
    assert result["customer_accepted"] is False
    assert result["scope"] == ["M1", "M2"]
    assert result["automated_source_checks_are_not_live_uat"] is True
    codes = {item["code"] for item in result["required_external_evidence"]}
    assert {
        "source_review", "legal_b007", "legal_calculator",
        "privacy_retention", "archive_delivery", "staging",
        "telegram", "payments", "external_uat", "operator",
    } == codes
    assert {item["status"] for item in result["required_external_evidence"]} == {
        "NOT_VERIFIED_HERE"
    }


@pytest.mark.asyncio
async def test_all_existing_release_status_endpoints_fail_closed_even_if_configured():
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=True),
        base_url="http://testserver",
    ) as client:
        endpoints = {
            "/go-live/status": "ready",
            "/production-center/status": "ready",
            "/acceptance-center/status": "ok",
            "/final-handover/status": "handover_accepted",
        }
        for path, ready_field in endpoints.items():
            response = await client.get(path)
            assert response.status_code == 200, path
            data = response.json()
            assert data[ready_field] is False, path
            assert data["release_admission"]["status"] == "HOLD", path
            assert data["release_admission"]["release_authorized"] is False, path

    assert (await final_handover_center.final_handover_status())["status"] == "HANDOVER_HOLD"
    assert (await acceptance_center.status())["source_checks_available"] is True


@pytest.mark.asyncio
async def test_green_legacy_file_checks_do_not_produce_a_launch_approval(monkeypatch):
    # Old endpoint used to equate all-files-present with READY.
    monkeypatch.setattr(
        go_live_center,
        "GO_LIVE_ITEMS",
        [("only_local", "local file", lambda: True)],
    )
    result = go_live_center.build_go_live_status()
    assert result["configuration_ready"] is True
    assert result["score"] == 100.0
    assert result["score_scope"] == "LOCAL_CONFIGURATION_ONLY"
    assert result["ready"] is False

    html = await go_live_center.go_live_ui()
    assert "ВЫПУСК ЗАБЛОКИРОВАН" in html
    assert "Локальной конфигурации" not in html  # no invented approval terminology
    assert "Готовность локальной конфигурации: 100.0%" in html
    assert "ГОТОВО К ЗАПУСКУ" not in html


def _full_production_settings(storage_dir, *, payment_provider):
    return SimpleNamespace(
        storage_dir=str(storage_dir),
        bot_token="synthetic:valid",
        database_url="postgresql+asyncpg://fake.invalid/no_connection",
        admin_username="admin",
        admin_password="synthetic-password",
        admin_api_token="synthetic-admin-token",
        payment_provider=payment_provider,
        yookassa_shop_id="SYNTHETIC-ONLY",
        yookassa_secret_key="synthetic-never-used",
    )


@pytest.mark.asyncio
async def test_production_page_does_not_accept_fake_payment_adapter(monkeypatch, tmp_path):
    monkeypatch.setattr(production_center, "_check_file", lambda path: True)
    monkeypatch.setattr(
        production_center, "settings",
        _full_production_settings(tmp_path, payment_provider="fake"),
    )
    result = production_center.build_production_status()
    assert result["checks"]["payment_configured"] is False
    assert result["configuration_ready"] is False
    assert result["ready"] is False
    assert result["score_scope"] == "LOCAL_CONFIGURATION_ONLY"


@pytest.mark.asyncio
async def test_payment_credentials_are_not_proof_of_real_provider_uat(monkeypatch, tmp_path):
    monkeypatch.setattr(production_center, "_check_file", lambda path: True)
    monkeypatch.setattr(
        production_center, "settings",
        _full_production_settings(tmp_path, payment_provider="yookassa"),
    )
    result = production_center.build_production_status()
    assert all(result["checks"].values())
    assert result["score"] == 100.0
    assert result["configuration_ready"] is True
    assert result["ready"] is False
    assert result["release_admission"]["release_authorized"] is False

    html = await production_center.production_ui()
    assert "ВЫПУСК ЗАБЛОКИРОВАН" in html
    assert "Локальная конфигурация: 100.0%" in html
    assert "Готово к запуску" not in html
    assert "synthetic-never-used" not in html


@pytest.mark.asyncio
async def test_handover_and_acceptance_never_call_source_present_a_signed_uat():
    handover = await final_handover_center.final_handover_status()
    assert handover["status"] == "HANDOVER_HOLD"
    assert handover["handover_accepted"] is False
    assert all(block["status"] != "готово" for block in handover["blocks"])

    html = await final_handover_center.final_handover_ui()
    assert "выпуск заблокирован" in html
    assert "БЕЗ LIVE-ПРИЁМКИ" in html
    assert "HANDOVER_READY" not in html

    acceptance = await acceptance_center.status()
    assert acceptance["ok"] is False
    assert acceptance["status"] == "HOLD"
    assert "SOURCE PREFLIGHT ONLY" in acceptance["expected_result"]
    assert "READY FOR PRODUCTION" not in acceptance["expected_result"]
    ui = await acceptance_center.ui()
    assert "Статус приёмки: HOLD" in ui.body.decode("utf-8")
