"""Legal-copy publication contract for the frozen B-007 consent screen.

Metadata is supplied by the approved legal document owner. The application
checks completeness, never claims to have performed a legal review itself.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.config import settings

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def consent_publication_ready() -> bool:
    url = str(settings.legal_consent_document_url or "").strip()
    version = str(settings.legal_consent_version or "").strip()
    digest = str(settings.legal_consent_document_sha256 or "").strip().lower()
    approval = str(settings.legal_consent_approval_reference or "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    return bool(
        parsed.scheme == "https"
        and hostname
        and "." in hostname
        and not hostname.startswith("localhost")
        and hostname not in {"example.com", "www.example.com"}
        and parsed.username is None
        and parsed.password is None
        and len(version) >= 1
        and len(version) <= 80
        and _HEX_SHA256.fullmatch(digest)
        and len(approval) >= 6
        and len(approval) <= 200
    )


def consent_publication_reference() -> dict[str, str]:
    """Embed an exact published-copy reference in the consent audit event."""
    if not consent_publication_ready():
        return {}
    return {
        "consent_document_url": str(settings.legal_consent_document_url).strip(),
        "consent_version": str(settings.legal_consent_version).strip(),
        "consent_sha256": str(settings.legal_consent_document_sha256).strip().lower(),
        "approval_reference": str(settings.legal_consent_approval_reference).strip(),
    }


def consent_document_prompt() -> str:
    if not consent_publication_ready():
        return (
            "Полный утверждённый текст согласия пока не опубликован. "
            "В реальной эксплуатации приём персональных данных запрещён; "
            "в тестовой среде возможны только синтетические данные."
        )
    return (
        "Полный текст согласия, версия "
        + str(settings.legal_consent_version).strip()
        + ":\n"
        + str(settings.legal_consent_document_url).strip()
        + "\n\nОзнакомьтесь с документом перед подтверждением."
    )
