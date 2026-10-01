from __future__ import annotations

import shutil
from importlib import metadata

from app.config import settings


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def document_derivative_runtime_status() -> dict[str, object]:
    """Cheap readiness contract; no document bytes and no OCR process are started."""

    pikepdf_version = _package_version("pikepdf")
    ocrmypdf_version = _package_version("ocrmypdf")
    tesseract_path = shutil.which("tesseract")
    languages = str(settings.document_ocr_languages or "").strip()
    threshold = int(settings.document_pdf_min_text_bytes)
    timeout = int(settings.document_ocr_timeout_seconds)
    config_valid = bool(
        languages
        and 1 <= threshold <= 10000
        and 30 <= timeout <= 1800
    )
    return {
        "available": bool(
            pikepdf_version
            and ocrmypdf_version
            and tesseract_path
            and config_valid
        ),
        "pikepdf_version": pikepdf_version,
        "ocrmypdf_version": ocrmypdf_version,
        "tesseract_available": bool(tesseract_path),
        "languages": languages,
        "text_threshold": threshold,
        "timeout_seconds": timeout,
        "config_valid": config_valid,
    }


__all__ = ["document_derivative_runtime_status"]
