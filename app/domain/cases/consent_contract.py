from __future__ import annotations

import hashlib

from app.domain.cases.service_modes import M1ServiceMode


# Existing full-representation consent. Its token/text/version are immutable:
# historical Telegram buttons and persisted evidence must keep resolving to the
# exact legal text the client saw.
CONSENT_TYPE = "personal_data_m1"
CONSENT_VERSION = "2026-08-19.1"
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


SELF_FILING_CONSENT_TYPE = "personal_data_m1_self_filing"
SELF_FILING_CONSENT_VERSION = "2026-09-25.1"
SELF_FILING_CONSENT_CALLBACK_TOKEN = "pd2"
SELF_FILING_CONSENT_TEXT = (
    "Согласие на обработку персональных данных\n\n"
    "Вы выбрали подготовку пакета документов для самостоятельной подачи в суд. "
    "Юридическая команда будет обрабатывать данные и документы этого обращения "
    "для проверки комплекта, определения применимой подсудности и подготовки "
    "процессуальных документов. Представительство в суде в эту услугу не входит: "
    "готовый пакет вы подаёте и используете самостоятельно.\n\n"
    "Подтверждение относится только к указанному обращению и не применяется к "
    "будущим делам. Если вы не готовы подтверждать согласие, можно вернуться к "
    "сохранённому расчёту или выбрать консультацию."
)
SELF_FILING_CONSENT_TEXT_SHA256 = hashlib.sha256(
    SELF_FILING_CONSENT_TEXT.encode("utf-8")
).hexdigest()


_CONTRACTS = {
    CONSENT_CALLBACK_TOKEN: {
        "consent_type": CONSENT_TYPE,
        "consent_version": CONSENT_VERSION,
        "callback_token": CONSENT_CALLBACK_TOKEN,
        "text": CONSENT_TEXT,
        "text_sha256": CONSENT_TEXT_SHA256,
        "service_mode": M1ServiceMode.FULL_REPRESENTATION.value,
    },
    SELF_FILING_CONSENT_CALLBACK_TOKEN: {
        "consent_type": SELF_FILING_CONSENT_TYPE,
        "consent_version": SELF_FILING_CONSENT_VERSION,
        "callback_token": SELF_FILING_CONSENT_CALLBACK_TOKEN,
        "text": SELF_FILING_CONSENT_TEXT,
        "text_sha256": SELF_FILING_CONSENT_TEXT_SHA256,
        "service_mode": M1ServiceMode.SELF_FILING_PACKAGE.value,
    },
}


def resolve_consent_contract(callback_token: str):
    """Resolve one exact immutable legal-text version without silent upgrades."""

    contract = _CONTRACTS.get(str(callback_token or ""))
    return dict(contract) if contract is not None else None


def consent_contract_for_service_mode(service_mode: str | None):
    if str(service_mode or "") == M1ServiceMode.SELF_FILING_PACKAGE.value:
        return resolve_consent_contract(SELF_FILING_CONSENT_CALLBACK_TOKEN)
    # Historical M1 rows predate service_mode and therefore remain full
    # representation unless the client explicitly selected SELF_FILING_PACKAGE.
    return resolve_consent_contract(CONSENT_CALLBACK_TOKEN)


__all__ = [
    "CONSENT_CALLBACK_TOKEN",
    "CONSENT_TEXT",
    "CONSENT_TEXT_SHA256",
    "CONSENT_TYPE",
    "CONSENT_VERSION",
    "SELF_FILING_CONSENT_CALLBACK_TOKEN",
    "SELF_FILING_CONSENT_TEXT",
    "SELF_FILING_CONSENT_TEXT_SHA256",
    "SELF_FILING_CONSENT_TYPE",
    "SELF_FILING_CONSENT_VERSION",
    "consent_contract_for_service_mode",
    "resolve_consent_contract",
]
