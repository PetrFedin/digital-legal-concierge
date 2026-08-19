from __future__ import annotations

import hashlib

CONSENT_TYPE = "personal_data_m1"
CONSENT_VERSION = "2026-08-19.1"
# Keep the callback token short enough for Telegram's callback_data limit. It
# identifies exactly CONSENT_VERSION; changing the legal text requires a new
# token and version, never reusing the old pair.
CONSENT_CALLBACK_TOKEN = "pd1"

CONSENT_TEXT = (
    "Согласие на обработку персональных данных\n\n"
    "Вы выбрали ведение дела. Чтобы передать документы юридической команде, "
    "подтвердите согласие отдельно. Подтверждение относится только к указанному "
    "обращению и не применяется к будущим делам.\n\n"
    "Если вы не готовы подтверждать согласие, можно вернуться к нейтральному "
    "расчёту или выбрать консультацию."
)

CONSENT_TEXT_SHA256 = hashlib.sha256(CONSENT_TEXT.encode("utf-8")).hexdigest()


def resolve_consent_contract(callback_token: str):
    """Resolve a callback-bound legal text version without silent upgrading."""
    if str(callback_token or "") != CONSENT_CALLBACK_TOKEN:
        return None
    return {
        "consent_type": CONSENT_TYPE,
        "consent_version": CONSENT_VERSION,
        "callback_token": CONSENT_CALLBACK_TOKEN,
        "text": CONSENT_TEXT,
        "text_sha256": CONSENT_TEXT_SHA256,
    }


__all__ = [
    "CONSENT_CALLBACK_TOKEN",
    "CONSENT_TEXT",
    "CONSENT_TEXT_SHA256",
    "CONSENT_TYPE",
    "CONSENT_VERSION",
    "resolve_consent_contract",
]
