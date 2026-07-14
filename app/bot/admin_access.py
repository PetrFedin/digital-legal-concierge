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
    TelegramAdminSlot(slot=2, username="Ev_volya", telegram_id=167471776),
    TelegramAdminSlot(slot=3,