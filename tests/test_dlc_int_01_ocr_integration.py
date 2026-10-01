from __future__ import annotations

import io
import os
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageDraw, ImageFont

from app.config import settings
from app.domain.documents.document_derivative_service import OCRmyPDFExecutor


pytestmark = pytest.mark.skipif(
    os.getenv("OCR_INTEGRATION") != "1",
    reason="real OCRmyPDF/Tesseract runtime required only by dedicated integration job",
)


def image_only_pdf() -> bytes:
    image = Image.new("L", (1800, 600), 255)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        96,
    )
    draw.text((100, 220), "LEGAL EVIDENCE 2026", fill=0, font=font)
    output = io.BytesIO()
    image.save(output, format="PDF", resolution=180.0)
    return output.getvalue()


def test_real_ocrmypdf_creates_searchable_pdf_from_image_only_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "storage_dir", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "document_ocr_languages", "eng")
    monkeypatch.setattr(settings, "document_ocr_timeout_seconds", 120)
    monkeypatch.setattr(settings, "document_pdf_min_text_bytes", 8)

    result = OCRmyPDFExecutor().run(image_only_pdf())

    assert result.has_usable_text is True
    assert result.text_characters >= 8
    assert result.page_count == 1
    assert result.sha256
    with pikepdf.open(io.BytesIO(result.payload), attempt_recovery=False) as pdf:
        assert len(pdf.pages) == 1
        assert pdf.is_encrypted is False
