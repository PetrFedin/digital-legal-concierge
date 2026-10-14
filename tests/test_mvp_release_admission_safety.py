"""A configured test installation can NEVER claim customer or production acceptance.

The six release surfaces historically conflated local files and a fake payment
adapter with legal/provider/client sign-off. These are executable regressions:
no forged live evidence, external providers, or production resources required.
"""
from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import os
import subprocess

import httpx
import pytest

from app.api import (
    acceptance_center,
    final_handover_center,
    final_qa_center,
    go_live_center,
    production_center,
    release_manager,
)
from app.domain.release_admission import runtime_release_admission
from scripts import deploy_admission
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
            "/final-qa/status": "qa_accepted",
            "/release-manager/status": "ok",
        }
        for path, ready_field in endpoints.items():
            response = await client.get(path)
            assert response.status_code == 200, path
            data = response.json()
            assert data[ready_field] is False, path
            assert data["release_admission"]["status"] == "HOLD", path
            assert data["release_admission"]["release_authorized"] is False, path

    assert (await final_handover_center.final_handover_status())["status"] == "HANDOVER_HOLD"
    assert (await final_qa_center.final_qa_status())["status"] == "QA_EVIDENCE_PENDING"
    assert (await release_manager.status())["workflow_available"] is True
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


@pytest.mark.asyncio
async def test_unsaved_final_qa_checkboxes_are_not_customer_acceptance():
    result = await final_qa_center.final_qa_status()
    assert result["status"] == "QA_EVIDENCE_PENDING"
    assert result["qa_accepted"] is False
    assert result["checks"] == sum(len(section["items"]) for section in result["qa_sections"])
    assert result["release_admission"]["release_authorized"] is False

    html = await final_qa_center.final_qa_ui()
    assert "Статус: HOLD" in html
    assert "не сохраняют результаты проверки" in html
    assert "OPERATOR_QA_READY" not in html


@pytest.mark.asyncio
async def test_release_manager_plan_presence_is_not_a_release_decision():
    response = await release_manager.status()
    assert response["status"] == "HOLD"
    assert response["ok"] is False
    assert response["workflow_available"] is True
    assert response["release_admission"]["customer_accepted"] is False
    html = await release_manager.ui()
    assert "статус HOLD" in html.body.decode("utf-8")


@pytest.mark.parametrize(
    "environment,target",
    [
        ("local", "local"),
        ("test", "local"),
        ("development", "local"),
        ("staging", "staging"),
        ("test", "staging"),
    ],
)
def test_deploy_gate_allows_nonproduction_only_for_live_hold(environment, target):
    report = deploy_admission.evaluate_deployment_admission(
        environment, runtime_release_admission(), target=target
    )
    assert report["ok"] is True
    assert report["status"] == "NONPRODUCTION_ONLY"
    assert "production acceptance" not in report["message"].lower()


@pytest.mark.parametrize(
    "admission",
    [
        {"status": "HOLD", "release_authorized": False, "customer_accepted": False},
        {"status": "ACCEPTED", "release_authorized": True, "customer_accepted": False},
        {"status": "ACCEPTED", "release_authorized": "true", "customer_accepted": True},
        {"status": "HOLD", "release_authorized": True, "customer_accepted": True},
        {"status": "ACCEPTED", "release_authorized": False, "customer_accepted": True},
    ],
)
def test_deploy_gate_denies_production_without_all_three_authorities(admission):
    report = deploy_admission.evaluate_deployment_admission(
        "production", admission, target="production"
    )
    assert report["ok"] is False
    assert report["status"] == "PRODUCTION_HOLD"


@pytest.mark.parametrize("environment", ["", "unknown", "prod", "live", "qa-production"])
def test_deploy_gate_denies_unknown_environments(environment):
    report = deploy_admission.evaluate_deployment_admission(
        environment, runtime_release_admission(), target="local"
    )
    assert report["ok"] is False
    assert report["status"] == "ENVIRONMENT_TARGET_MISMATCH"


@pytest.mark.parametrize("target", ["", "unknown", "live", "qa-production"])
def test_deploy_gate_denies_unknown_targets(target):
    report = deploy_admission.evaluate_deployment_admission(
        "local", runtime_release_admission(), target=target
    )
    assert report["ok"] is False
    assert report["status"] == "UNKNOWN_TARGET"


@pytest.mark.parametrize("environment,target", [
    ("local", "production"),
    ("test", "production"),
    ("production", "local"),
    ("production", "staging"),
    ("local", "staging"),
    ("staging", "local"),
])
def test_compose_target_cannot_be_disguised_with_app_env(environment, target):
    report = deploy_admission.evaluate_deployment_admission(
        environment, runtime_release_admission(), target=target
    )
    assert report["ok"] is False


def test_production_approval_requires_explicit_accepted_authority():
    # This is a *contract* example, not a forged signed release receipt.
    report = deploy_admission.evaluate_deployment_admission(
        "production",
        {
            "status": "ACCEPTED",
            "release_authorized": True,
            "customer_accepted": True,
        },
        target="production",
    )
    assert report["ok"] is True
    assert report["status"] == "PRODUCTION_APPROVED"
    # The actual runtime authority remains HOLD and cannot issue that receipt.
    assert deploy_admission.evaluate_deployment_admission(
        "production", runtime_release_admission(), target="production"
    )["ok"] is False


def test_deploy_cli_never_claims_release_for_configured_production(monkeypatch, capsys):
    monkeypatch.setattr(
        deploy_admission, "settings", SimpleNamespace(app_env="production")
    )
    monkeypatch.setenv("DLC_DEPLOY_TARGET", "production")
    assert deploy_admission.main() == 2
    output = capsys.readouterr().out
    assert '"ok": false' in output
    assert "PRODUCTION_HOLD" in output
    assert "secret" not in output.lower()


def test_deploy_script_requires_exact_sha_and_gate_before_mutating_app():
    script = (Path(__file__).resolve().parents[1] / "deploy.sh").read_text(
        encoding="utf-8"
    )
    assert '[ -n "${DEPLOY_EXACT_SHA:-}" ] || fail' in script
    assert "DEPLOY_EXACT_SHA обязателен" in script
    assert 'compose_name="${compose_file##*/}"' in script
    assert '[ -n "$deploy_target" ] || deploy_target="production"' in script
    assert "dlc-staging-*" in script
    assert '"$GIT_COMMIT_SHA" = "$DEPLOY_EXACT_SHA"' in script
    check = 'dc run --rm --no-deps -e DLC_DEPLOY_TARGET="$deploy_target" --entrypoint python app scripts/deploy_admission.py'
    assert check in script
    assert script.index(check) < script.index("backup_cli create")
    assert script.index(check) < script.index("dc up -d --remove-orphans")
    assert script.index("dc build --pull app") < script.index(check)
    result = subprocess.run(
        ["bash", "-n", str(Path(__file__).resolve().parents[1] / "deploy.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "compose_file,explicit_sha,expected_sha",
    [
        ("docker-compose.yml", "", "a" * 40),
        ("docker-compose.yml", "b" * 40, "b" * 40),
        ("docker-compose.timeweb.yml", "", ""),
        ("docker-compose.production.yml", "c" * 40, "c" * 40),
    ],
)
def test_run_sh_only_autopins_local_compose(
    tmp_path, compose_file, explicit_sha, expected_sha,
):
    root = Path(__file__).resolve().parents[1]
    (tmp_path / "run.sh").write_text(
        (root / "run.sh").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "deploy.sh").write_text(
        "#!/usr/bin/env bash\n"
        "printf 'sha=%s;compose=%s\\n' \"$DEPLOY_EXACT_SHA\" \"$COMPOSE_FILE\"\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    fake_git = fake_bin / "git"
    fake_git.write_text("#!/bin/sh\necho " + "a" * 40 + "\n", encoding="utf-8")
    fake_git.chmod(0o755)

    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]
    environment["COMPOSE_FILE"] = compose_file
    if explicit_sha:
        environment["DEPLOY_EXACT_SHA"] = explicit_sha
    else:
        environment.pop("DEPLOY_EXACT_SHA", None)
    response = subprocess.run(
        ["bash", str(tmp_path / "run.sh")],
        env=environment, capture_output=True, text=True, check=False,
    )
    assert response.returncode == 0, response.stderr
    assert f"sha={expected_sha};compose={compose_file}" in response.stdout

    menu = (root / "bot-control.sh").read_text(encoding="utf-8")
    assert '3|deploy|start) COMPOSE_FILE="$compose_file" bash ./run.sh ;;' in menu
