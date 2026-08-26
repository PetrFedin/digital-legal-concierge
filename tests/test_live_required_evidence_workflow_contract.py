from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-required.yml"


def test_each_live_required_component_uploads_success_evidence() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    for component in (
        "postgres",
        "redis",
        "telegram",
        "browser",
        "provider-sandbox",
    ):
        assert f"--component {component}" in text
        assert f"live-required-evidence-{component}-${{{{ github.run_attempt }}}}" in text

    assert text.count("uses: actions/upload-artifact@v4") >= 6
    assert text.count("if-no-files-found: error") >= 6
    assert text.count("retention-days: 30") >= 5


def test_aggregate_gate_downloads_same_attempt_evidence_and_uploads_exact_sha_manifest() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    aggregate = text.split("  live-required:\n", 1)[1]

    assert "if: ${{ always() }}" in aggregate
    assert "uses: actions/download-artifact@v4" in aggregate
    assert "pattern: live-required-evidence-*-${{ github.run_attempt }}" in aggregate
    assert "merge-multiple: true" in aggregate
    assert "python scripts/live_required_evidence.py aggregate" in aggregate
    assert '--sha "$GITHUB_SHA"' in aggregate
    assert "--require postgres redis telegram browser provider-sandbox" in aggregate
    assert "LIVE_REQUIRED_MANIFEST.json" in aggregate
    assert (
        "live-required-release-evidence-${{ github.sha }}-attempt-${{ github.run_attempt }}"
        in aggregate
    )
    assert "retention-days: 90" in aggregate


def test_manifest_is_built_only_after_all_component_conclusions_are_success() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    aggregate = text.split("  live-required:\n", 1)[1]

    status_gate = aggregate.index("Fail unless every required live component succeeded")
    download = aggregate.index("Download component evidence")
    verify = aggregate.index("Verify one-run one-attempt one-SHA evidence and build manifest")
    upload = aggregate.index("Upload release evidence manifest")

    assert status_gate < download < verify < upload
    assert 'if [ "$result" != "success" ]; then' in aggregate
    assert "exit 1" in aggregate
