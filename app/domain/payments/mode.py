from __future__ import annotations

from app.config import settings

VALID_PAYMENT_PROVIDERS = frozenset({"disabled", "fake", "yookassa"})


def payment_provider_name() -> str:
    return str(settings.payment_provider or "").strip().lower()


def payments_disabled() -> bool:
    return payment_provider_name() == "disabled"


def payments_enabled() -> bool:
    provider = payment_provider_name()
    if provider == "yookassa":
        return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
    if provider == "fake":
        # Fake payment links are a development/test facility only. A forgotten
        # DEMO_MODE flag must never turn them back on in staging/production.
        return settings.app_env in {"local", "test"}
    return False


def payment_mode_valid() -> bool:
    provider = payment_provider_name()
    if provider == "disabled":
        return True
    return provider in VALID_PAYMENT_PROVIDERS and payments_enabled()
