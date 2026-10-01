from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import unicodedata
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath

from app.security.document_encryption import encrypt_file


@dataclass(frozen=True)
class AllowedUploadType:
    code: str
    mime_type: str
    extensions: frozenset[str]
    claimed_mime_types: frozenset[str]


PDF = AllowedUploadType(
    code="pdf",
    mime_type="application/pdf",
    extensions=frozenset({".pdf"}),
    claimed_mime_types=frozenset({"application/pdf"}),
)
JPEG = AllowedUploadType(
    code="jpeg",
    mime_type="image/jpeg",
    extensions=frozenset({".jpg", ".jpeg"}),
    claimed_mime_types=frozenset({"image/jpeg", "image/jpg"}),
)
PNG = AllowedUploadType(
    code="png",
    mime_type="image/png",
    extensions=frozenset({".png"}),
    claimed_mime_types=frozenset({"image/png"}),
)
DOCX = AllowedUploadType(
    code="docx",
    mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    extensions=frozenset({".docx"}),
    claimed_mime_types=frozenset(
        {
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/zip",
        }
    ),
)
ALLOWED_TYPES = (PDF, JPEG, PNG, DOCX)
ALLOWED_BY_EXTENSION = {
    extension: upload_type
    for upload_type in ALLOWED_TYPES
    for extension in upload_type.extensions
}
GENERIC_MIME_TYPES = {"", "application/octet-stream", "binary/octet-stream"}
MAX_FILENAME_LENGTH = 180
MAX_IMAGE_PIXELS = 40_000_000
MAX_DOCX_ENTRIES = 2_000
MAX_DOCX_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
MAX_DOCX_COMPRESSION_RATIO = 200
WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]+")
SPACE_RE = re.compile(r"\s+")
UNSAFE_PDF_MARKERS = (
    b"/JavaScript",
    b"/JS",
    b"/Launch",
    b"/EmbeddedFile",
    b"/RichMedia",
)
UNSAFE_ARCHIVE_SUFFIXES = (
    ".exe",
    ".dll",
    ".com",
    ".scr",
    ".bat",
    ".cmd",
    ".ps1",
    ".vbs",
    ".js",
    ".jar",
    ".msi",
)


class UploadSecurityError(ValueError):
    def __init__(
        self,
        code: str,
        user_message: str,
        *,
        technical_message: str | None = None,
        sha256: str | None = None,
        quarantine_path: str | None = None,
    ):
        super().__init__(technical_message or user_message)
        self.code = code
        self.user_message = user_message
        self.technical_message = technical_message or user_message
        self.sha256 = sha256
        self.quarantine_path = quarantine_path


@dataclass(frozen=True)
class UploadPreflight:
    safe_name: str
    extension: str
    expected_type: AllowedUploadType


@dataclass(frozen=True)
class UploadInspection:
    safe_name: str
    extension: str
    detected_type: str
    mime_type: str
    size_bytes: int
    sha256: str
    scanned_at: datetime


def _normalized_claimed_mime(value: str | None) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def safe_filename(name: str | None) -> str:
    value = unicodedata.normalize("NFKC", str(name or "file")).replace("\\", "/")
    value = PurePosixPath(value).name
    value = CONTROL_RE.sub("", value).strip().strip(".")
    value = SPACE_RE.sub(" ", value)
    if not value:
        value = "file"

    original_suffix = Path(value).suffix.lower()
    stem = value[: -len(original_suffix)] if original_suffix else value
    cleaned_stem = "".join(
        character
        if character.isalnum() or character in {" ", "_", "-", ".", "(", ")"}
        else "_"
        for character in stem
    ).strip(" ._")
    cleaned_stem = SPACE_RE.sub(" ", cleaned_stem) or "file"
    if cleaned_stem.upper() in WINDOWS_RESERVED_NAMES:
        cleaned_stem = f"file_{cleaned_stem}"

    suffix = "".join(
        character for character in original_suffix if character.isalnum() or character == "."
    )[:12]
    available = max(1, MAX_FILENAME_LENGTH - len(suffix))
    return f"{cleaned_stem[:available]}{suffix}"


def preflight_upload(
    *,
    original_name: str | None,
    claimed_mime: str | None,
    declared_size: int | None,
    max_bytes: int,
) -> UploadPreflight:
    if declared_size is not None and int(declared_size) <= 0:
        raise UploadSecurityError("empty_file", "Файл пустой. Прикрепите другой документ.")
    if declared_size is not None and int(declared_size) > max_bytes:
        raise UploadSecurityError(
            "file_too_large",
            f"Файл слишком большой. Максимальный размер — {max_bytes // (1024 * 1024)} МБ.",
        )

    name = safe_filename(original_name)
    extension = Path(name).suffix.lower()
    expected_type = ALLOWED_BY_EXTENSION.get(extension)
    if expected_type is None:
        raise UploadSecurityError(
            "extension_not_allowed",
            "Допустимы только PDF, DOCX, JPG и PNG.",
        )

    mime = _normalized_claimed_mime(claimed_mime)
    if mime not in GENERIC_MIME_TYPES and mime not in expected_type.claimed_mime_types:
        raise UploadSecurityError(
            "claimed_type_mismatch",
            "Тип файла не соответствует его расширению. Сохраните документ заново и повторите загрузку.",
            technical_message=f"claimed MIME {mime!r} is not valid for {extension}",
        )
    return UploadPreflight(
        safe_name=name,
        extension=extension,
        expected_type=expected_type,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _detect_type(header: bytes) -> AllowedUploadType | None:
    if header.startswith(b"%PDF-"):
        return PDF
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if header.startswith(b"\xff\xd8\xff"):
        return JPEG
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06"):
        return DOCX
    return None


def _inspect_pdf(path: Path) -> None:
    data = path.read_bytes()
    if b"%%EOF" not in data[-65536:]:
        raise UploadSecurityError(
            "malformed_pdf",
            "PDF повреждён или сформирован некорректно.",
        )
    for marker in UNSAFE_PDF_MARKERS:
        if marker in data:
            raise UploadSecurityError(
                "active_pdf_content",
                "PDF содержит активное или вложенное содержимое и не может быть принят.",
                technical_message=f"unsafe PDF marker: {marker.decode('ascii')}",
            )
    if b"/Encrypt" in data:
        raise UploadSecurityError(
            "encrypted_pdf",
            "PDF защищён паролем. Загрузите копию без пароля.",
        )


def _inspect_png(path: Path) -> None:
    with path.open("rb") as source:
        header = source.read(24)
        if len(header) < 24 or header[12:16] != b"IHDR":
            raise UploadSecurityError("malformed_png", "PNG-файл повреждён.")
        width, height = struct.unpack(">II", header[16:24])
        if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
            raise UploadSecurityError(
                "image_dimensions_rejected",
                "Изображение имеет недопустимые размеры.",
            )
        source.seek(-12, os.SEEK_END)
        if source.read(8)[4:] != b"IEND":
            raise UploadSecurityError("malformed_png", "PNG-файл повреждён.")


def _jpeg_dimensions(path: Path) -> tuple[int, int] | None:
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    with path.open("rb") as source:
        if source.read(2) != b"\xff\xd8":
            return None
        while True:
            prefix = source.read(1)
            if not prefix:
                return None
            if prefix != b"\xff":
                continue
            marker_byte = source.read(1)
            while marker_byte == b"\xff":
                marker_byte = source.read(1)
            if not marker_byte:
                return None
            marker = marker_byte[0]
            if marker in {0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
                continue
            length_bytes = source.read(2)
            if len(length_bytes) != 2:
                return None
            segment_length = struct.unpack(">H", length_bytes)[0]
            if segment_length < 2:
                return None
            if marker in sof_markers:
                data = source.read(5)
                if len(data) != 5:
                    return None
                height, width = struct.unpack(">HH", data[1:5])
                return width, height
            source.seek(segment_length - 2, os.SEEK_CUR)


def _inspect_jpeg(path: Path) -> None:
    with path.open("rb") as source:
        source.seek(-2, os.SEEK_END)
        if source.read(2) != b"\xff\xd9":
            raise UploadSecurityError("malformed_jpeg", "JPG-файл повреждён.")
    dimensions = _jpeg_dimensions(path)
    if not dimensions:
        raise UploadSecurityError("malformed_jpeg", "JPG-файл повреждён.")
    width, height = dimensions
    if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
        raise UploadSecurityError(
            "image_dimensions_rejected",
            "Изображение имеет недопустимые размеры.",
        )


def _safe_zip_member(name: str) -> bool:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    return not path.is_absolute() and ".." not in path.parts


def _inspect_docx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if not entries or len(entries) > MAX_DOCX_ENTRIES:
                raise UploadSecurityError(
                    "docx_archive_rejected",
                    "DOCX имеет некорректную структуру.",
                )
            names = {entry.filename.replace("\\", "/") for entry in entries}
            required = {"[Content_Types].xml", "_rels/.rels", "word/document.xml"}
            if not required.issubset(names):
                raise UploadSecurityError(
                    "malformed_docx",
                    "DOCX повреждён или не является документом Word.",
                )

            total_compressed = 0
            total_uncompressed = 0
            for entry in entries:
                name = entry.filename.replace("\\", "/")
                lower_name = name.lower()
                if not _safe_zip_member(name):
                    raise UploadSecurityError(
                        "unsafe_archive_path",
                        "DOCX содержит небезопасную структуру архива.",
                    )
                if entry.flag_bits & 0x1:
                    raise UploadSecurityError(
                        "encrypted_docx_entry",
                        "DOCX содержит зашифрованные вложения и не может быть принят.",
                    )
                if lower_name.endswith(UNSAFE_ARCHIVE_SUFFIXES) or "vbaproject.bin" in lower_name:
                    raise UploadSecurityError(
                        "active_docx_content",
                        "DOCX содержит макросы или исполняемое содержимое.",
                    )
                total_compressed += max(0, int(entry.compress_size))
                total_uncompressed += max(0, int(entry.file_size))
                if total_uncompressed > MAX_DOCX_UNCOMPRESSED_BYTES:
                    raise UploadSecurityError(
                        "docx_expansion_limit",
                        "DOCX имеет подозрительно большой внутренний объём.",
                    )
                if entry.file_size > 1024 * 1024:
                    ratio = entry.file_size / max(1, entry.compress_size)
                    if ratio > MAX_DOCX_COMPRESSION_RATIO:
                        raise UploadSecurityError(
                            "docx_compression_ratio",
                            "DOCX имеет подозрительную степень сжатия.",
                        )

            if total_uncompressed / max(1, total_compressed) > MAX_DOCX_COMPRESSION_RATIO:
                raise UploadSecurityError(
                    "docx_compression_ratio",
                    "DOCX имеет подозрительную степень сжатия.",
                )

            content_types = archive.read("[Content_Types].xml")
            lowered = content_types.lower()
            if b"macroenabled" in lowered or b"vbaproject" in lowered:
                raise UploadSecurityError(
                    "active_docx_content",
                    "DOCX содержит макросы и не может быть принят.",
                )
    except UploadSecurityError:
        raise
    except (zipfile.BadZipFile, KeyError, OSError, RuntimeError) as error:
        raise UploadSecurityError(
            "malformed_docx",
            "DOCX повреждён или не является документом Word.",
            technical_message=str(error),
        ) from error


def inspect_upload(
    path: Path,
    *,
    original_name: str | None,
    claimed_mime: str | None,
    declared_size: int | None,
    max_bytes: int,
) -> UploadInspection:
    preflight = preflight_upload(
        original_name=original_name,
        claimed_mime=claimed_mime,
        declared_size=declared_size,
        max_bytes=max_bytes,
    )
    size = path.stat().st_size
    if size <= 0:
        raise UploadSecurityError("empty_file", "Файл пустой. Прикрепите другой документ.")
    if size > max_bytes:
        raise UploadSecurityError(
            "file_too_large",
            f"Файл слишком большой. Максимальный размер — {max_bytes // (1024 * 1024)} МБ.",
        )

    digest = _sha256(path)
    with path.open("rb") as source:
        detected = _detect_type(source.read(16))
    if detected is None:
        raise UploadSecurityError(
            "unknown_content_type",
            "Не удалось определить реальный тип файла.",
            sha256=digest,
        )
    if detected.code != preflight.expected_type.code:
        raise UploadSecurityError(
            "content_type_mismatch",
            "Содержимое файла не соответствует его расширению.",
            technical_message=(
                f"extension {preflight.extension} expects {preflight.expected_type.code}, "
                f"detected {detected.code}"
            ),
            sha256=digest,
        )

    try:
        if detected is PDF:
            _inspect_pdf(path)
        elif detected is PNG:
            _inspect_png(path)
        elif detected is JPEG:
            _inspect_jpeg(path)
        elif detected is DOCX:
            _inspect_docx(path)
    except UploadSecurityError as error:
        if error.sha256 is None:
            error.sha256 = digest
        raise

    return UploadInspection(
        safe_name=preflight.safe_name,
        extension=preflight.extension,
        detected_type=detected.code,
        mime_type=detected.mime_type,
        size_bytes=size,
        sha256=digest,
        scanned_at=datetime.now(timezone.utc),
    )


def quarantine_file(
    source: Path,
    *,
    quarantine_dir: Path,
    error: UploadSecurityError,
    safe_name: str,
    case_id: int,
) -> str:
    """Move rejected bytes into encrypted quarantine, never plaintext retention."""

    quarantine_dir.mkdir(parents=True, exist_ok=True)
    try:
        quarantine_dir.chmod(0o700)
    except OSError:
        pass

    identifier = uuid.uuid4().hex
    target = quarantine_dir / f"{identifier}.quarantine.dlcenc"
    metadata_path = quarantine_dir / f"{identifier}.json"

    encryption = encrypt_file(
        source,
        target,
        expected_sha256=error.sha256,
    )
    source.unlink(missing_ok=True)
    try:
        target.chmod(0o600)
    except OSError:
        pass

    metadata = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "case_id": int(case_id),
        "safe_name": safe_name,
        "reason_code": error.code,
        "technical_message": error.technical_message[:500],
        "sha256": encryption.sha256,
        "quarantine_file": target.name,
        "encryption_status": "ENCRYPTED",
        "encryption_key_id": encryption.key_id,
        "encryption_format_version": encryption.format_version,
        "encryption_envelope_id": encryption.envelope_id,
        "encrypted_data_key": encryption.encrypted_data_key,
        "encrypted_data_key_nonce": encryption.encrypted_data_key_nonce,
        "encrypted_at": encryption.encrypted_at.isoformat(),
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        metadata_path.chmod(0o600)
    except OSError:
        pass
    return str(target)


def cleanup_quarantine(base_dir: Path, *, retention_days: int) -> int:
    quarantine_dir = base_dir / "quarantine"
    if not quarantine_dir.exists():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, int(retention_days)))
    removed = 0
    for path in quarantine_dir.iterdir():
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified < cutoff and path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed
