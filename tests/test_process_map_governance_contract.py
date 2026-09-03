from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
AGENT_CONTRACT = ROOT / "AGENTS.md"
PROCESS_MAP = ROOT / "docs" / "PROCESS_MAP_CURRENT.md"


def test_ci_requires_process_map_presence_and_freshness() -> None:
    source = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "test -s docs/PROCESS_MAP_CURRENT.md" in source
    assert "git diff --name-only \"$BASE_SHA\" \"$HEAD_SHA\"" in source
    assert "latest_map_commit" in source
    assert "latest_governed_commit" in source
    assert "git rev-list -1 \"$BASE_SHA..$HEAD_SHA\" -- docs/PROCESS_MAP_CURRENT.md" in source
    assert "':(exclude)docs/PROCESS_MAP_CURRENT.md'" in source
    assert "git merge-base --is-ancestor \"$latest_governed_commit\" \"$latest_map_commit\"" in source
    assert "process map is stale" in source.lower()


def test_agent_contract_explains_last_governed_commit_rule() -> None:
    source = AGENT_CONTRACT.read_text(encoding="utf-8")

    assert "last governed commit" in source
    assert "no later governed repository commit exists" in source
    assert "docs/PROCESS_MAP_CURRENT.md" in source


def test_process_map_is_present_and_identifies_itself_as_living_inventory() -> None:
    source = PROCESS_MAP.read_text(encoding="utf-8")

    assert "# PROCESS MAP — CURRENT" in source
    assert "living authoritative implementation inventory" in source
    assert "# Known inconsistency and debt register" in source
    assert "# Change log" in source
