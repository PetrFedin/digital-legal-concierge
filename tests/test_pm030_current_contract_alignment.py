from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_product_scope_names_preview_as_canonical_non_persistent_entry() -> None:
    source = read("docs/PRODUCT_SCOPE_CURRENT.md")

    assert "canonical visible entry is deliberately **non-persistent**" in source
    assert "`preview_calc_start`" in source
    assert "`preview_calc_save:v2:<preview_id>`" in source
    assert "create **no** `Case`, `CalculationIntake`, `Calculation`" in source
    assert "Historical raw `calc_start` remains a compatibility handler" in source
    assert "Starting a new calculation is a new legal inquiry and can create a new Case" not in source


def test_system_contract_separates_preview_materialization_and_durable_recovery() -> None:
    source = read("docs/SYSTEM_CONTRACT_CURRENT.md")

    assert "non-persistent calculator preview" in source
    assert "`preview_calc_start` is the canonical global" in source
    assert "`preview_calc_save:v2:<preview_id>` is the only canonical durable materialization boundary" in source
    assert "`calc_recover:v2:<case_id>` resumes an unfinished calculation" in source
    assert "raw `calc_start` is legacy compatibility" in source
    assert "`calc_start` is the global **new calculation / new Case** action" not in source


def test_process_map_p01_starts_with_preview_and_explicit_save() -> None:
    source = read("docs/PROCESS_MAP_CURRENT.md")
    start = source.index("## P-01")
    end = source.index("## P-02", start)
    p01 = source[start:end]

    assert "preview_calc_start" in p01
    assert "preview_calc_save:v2:<preview_id>" in p01
    assert "Before the client chooses **Save calculation and continue**" in p01
    assert "Durable Case path starts **after explicit preview save**" in p01
    assert "Current runtime source emits zero raw `calc_start`" in p01
    assert "Calculate → source operation key → CaseCreationRequest → new Case" not in p01
    assert "`calc_start` means a new matter" not in p01
