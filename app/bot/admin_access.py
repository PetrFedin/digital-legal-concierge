from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TelegramAdminSlot:
    slot: int
    username: str | None = None
    telegram_id: int | None = None
    is_superadmin: bool = False


DEFAULT_ADMIN_SLOTS = (
    TelegramAdminSlot(slot=1, username="sheqel", telegram_id=6888383473, is_superadmin=True),
    TelegramAdminSlot(slot=2, username="Ev_volya"),
    TelegramAdminSlot(slot=3, username="AnnaBiboletova"),
    TelegramAdminSlot(slot=4),
    TelegramAdminSlot(slot=5),
    TelegramAdminSlot(slot=6),
    TelegramAdminSlot(slot=7),
    TelegramAdminSlot(slot=8),
)


def _extra_admin_ids() -> set[int]:
    raw = os.getenv("TELEGRAM_ADMIN_IDS", "")
    result: set[int] = set()
    for item in raw.replace(";", ",").split(","):
        value = item.strip()
        if value.isdigit():
            result.add(int(value))
    return result


def is_allowed_admin(telegram_id: int | None) -> bool:
    if not telegram_id:
        return False
    configured = {slot.telegram_id for slot in DEFAULT_ADMIN_SLOTS if slot.telegram_id}
    return int(telegram_id) in configured | _extra_admin_ids()


def admin_slot_for(telegram_id: int | None) -> TelegramAdminSlot | None:
    if not telegram_id:
        return None
    for slot in DEFAULT_ADMIN_SLOTS:
        if slot.telegram_id == int(telegram_id):
            return slot
    if int(telegram_id) in _extra_admin_ids():
        return TelegramAdminSlot(slot=0, telegram_id=int(telegram_id))
    return None
