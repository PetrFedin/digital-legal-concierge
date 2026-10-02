from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"


def read(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_customer_delivery_has_one_documentation_authority_chain():
    manifest = yaml.safe_load(read("AUTHORITY_MANIFEST.yml"))

    assert manifest["status"] == "authoritative"
    assert manifest["authoritative"]["product"] == [
        "docs/CANONICAL_PRODUCT_SPEC_2026-10-02.md",
        "docs/PRODUCT_SCOPE_CURRENT.md",
    ]
    assert "docs/TECHNICAL_ARCHITECTURE_CURRENT.md" in manifest["authoritative"]["architecture"]
    assert "docs/SYSTEM_CONTRACT_CURRENT.md" in manifest["authoritative"]["architecture"]
    assert "docs/ACCEPTANCE_CURRENT.md" in manifest["authoritative"]["acceptance"]
    assert "docs/RUNBOOK_CURRENT.md" in manifest["authoritative"]["operations"]
    assert "docs/PROCESS_MAP_CURRENT.md" in manifest["authoritative"]["implementation_inventory"]


def test_start_here_is_container_first_and_does_not_restore_legacy_python_quickstart():
    start = read("START_HERE.md")

    assert "docker compose up -d --build" in start
    assert "CANONICAL_PRODUCT_SPEC_2026-10-02.md" in start
    assert "TECHNICAL_ARCHITECTURE_CURRENT.md" in start
    assert "AUTHORITY_MANIFEST.yml" in start
    assert "python -m venv" not in start
    assert "pip install -e ." not in start


def test_canonical_product_spec_keeps_two_route_boundary_and_telegram_cabinet():
    product = read("CANONICAL_PRODUCT_SPEC_2026-10-02.md")

    assert "M1 — standard recovery" in product
    assert "M2 — paid personal consultation" in product
    assert "Telegram is the client cabinet" in product
    assert "not a third route" in product
    assert "Case.status" in product


def test_technical_architecture_names_real_runtime_authorities():
    architecture = read("TECHNICAL_ARCHITECTURE_CURRENT.md")

    for required in (
        "PostgreSQL 17 production authority",
        "Redis 7.4 durable Telegram FSM/coordination",
        "ClamAV sidecar",
        "Case.status",
        "PaymentEvent",
        "PaymentWebhookEvent",
        "ConsultationSlot",
        "Alembic",
    ):
        assert required in architecture


def test_business_source_specs_are_retained_as_frozen_inputs():
    manifest = yaml.safe_load(read("AUTHORITY_MANIFEST.yml"))
    for source in manifest["business_sources"]:
        assert (ROOT / source).is_file(), source
