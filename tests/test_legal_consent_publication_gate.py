"""Require an approved publication reference for real B-007 client consent.

This validates a configured publication reference, not the external legal
review itself. Only the lawyer/customer can approve the content.
"""
from __future__ import annotations

from app.config import settings
from app.legal.consent_contract import (
    consent_document_prompt,
    consent_publication_ready,
    consent_publication_reference,
)
from scripts.production_preflight import build_report


def _approved(monkeypatch):
    monkeypatch.setattr(
        settings,
        "legal_consent_document_url",
        "https://legal.example.test/documents/consent-ddo-v1.pdf",
    )
    monkeypatch.setattr(settings, "legal_consent_version", "2026-10-11-v1")
    monkeypatch.setattr(
        settings,
        "legal_consent_document_sha256",
        "e" * 64,
    )
    monkeypatch.setattr(
        settings,
        "legal_consent_approval_reference",
        "LEGAL-APPROVAL-2026-001",
    )


def test_without_approved_document_production_preflight_blocks_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path / "backups"))
    for field in (
        "legal_consent_document_url",
        "legal_consent_version",
        "legal_consent_document_sha256",
        "legal_consent_approval_reference",
    ):
        monkeypatch.setattr(settings, field, "")
    report = build_report()
    assert report["ok"] is False
    assert report["checks"]["approved_legal_consent_document_ready"] is False
    assert "approved_legal_consent_document_ready" in report["failed"]


def test_approved_reference_is_versioned_and_shown_to_client(monkeypatch):
    _approved(monkeypatch)
    assert consent_publication_ready()
    ref = consent_publication_reference()
    assert ref["consent_version"] == "2026-10-11-v1"
    assert ref["consent_sha256"] == "e" * 64
    assert ref["approval_reference"] == "LEGAL-APPROVAL-2026-001"
    prompt = consent_document_prompt()
    assert "https://legal.example.test/documents/" in prompt
    assert "2026-10-11-v1" in prompt


def test_unapproved_or_malformed_metadata_is_rejected(monkeypatch):
    _approved(monkeypatch)
    for field, value in (
        ("legal_consent_document_url", "http://non-secure.example.test/consent"),
        ("legal_consent_document_url", "https://example.com/consent"),
        ("legal_consent_document_sha256", "not-a-sha256"),
        ("legal_consent_version", ""),
        ("legal_consent_approval_reference", ""),
    ):
        with monkeypatch.context() as context:
            context.setattr(settings, field, value)
            assert not consent_publication_ready()
            assert consent_publication_reference() == {}
