from __future__ import annotations

import mimetypes
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiogram import Bot

from app.config import settings


@dataclass
class StoredFile:
    original_name: str
    storage_path: str
    mime_type: str | None
    file_size: int | None


SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def safe_filename(name: str) -> str:
    base = Path(name or "file").name.strip() or "file"
    stem = Path(base).stem[:80] or "file"
    suffix = Path(base).suffix[:12]
    return SAFE_NAME_RE.sub("_", stem) + suffix


class LocalStorageService:
    """Production-friendly local storage layer.

    For MVP this stores files in STORAGE_DIR. The class is intentionally isolated so
    S3/MinIO can replace only this file later without touching Telegram screens.
    """

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.storage_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    async def save_telegram_file(
        self,
        *,
        bot: Bot,
        telegram_file_id: str,
        case_id: int,
        original_name: str,
        mime_type: str | None = None,
        file_size: int | None = None,
    ) -> StoredFile:
        case_dir = self.base_dir / "cases" / str(case_id)
        case_dir.mkdir(parents=True, exist_ok=True)

        name = safe_filename(original_name)
        target = case_dir / f"{uuid.uuid4().hex}_{name}"

        tg_file = await bot.get_file(telegram_file_id)
        await bot.download_file(tg_file.file_path, destination=target)

        guessed_type = mime_type or mimetypes.guess_type(name)[0]
        size = file_size or target.stat().st_size
        return StoredFile(
            original_name=original_name,
            storage_path=str(target),
            mime_type=guessed_type,
            file_size=size,
        )
