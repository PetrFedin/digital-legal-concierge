from __future__ import annotations

from app.config import settings

VALID_PAYMENT_PROVIDERS = frozenset({"disabled", "fake", "yookassa"})
LOCAL_PAYMENT_BYPASS_ENVS = frozenset({"local", "test"})


def _app_env() -> str:
    return str(settings.app_env or "").strip().lower()


def payment_provider_name() -> str:
    return str(settings.payment_provider or "").strip().lower()


def payment_provider_is_disabled() -> bool:
    """Return the configured switch without implying a successful payment."""

    return payment_provider_name() == "disabled"


def payments_disabled() -> bool:
    """Allow the no-provider payment bypass only in local/test environments.

    Legacy pilot code uses this function to continue a flow without contacting a
    provider and immediately records the internal payment as paid. That behavior
    is useful for isolated local tests, but it is not an acceptable production or
    staging payment mode. Outside local/test, ``PAYMENT_PROVIDER=disabled`` is an
    invalid fail-closed configuration: PaymentService reaches
    DisabledPaymentProvider, receives an error, and leaves the business flow at
    its payment prerequisite instead of fabricating a successful payment.
    """

    return payment_provider_is_disabled() and _app_env() in LOCAL_PAYMENT_BYPASS_ENVS


def payments_enabled() -> bool:
    provider = payment_provider_name()
    if provider == "yookassa":
        return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)
    if provider == "fake":
        # Fake payment links are a development/test facility only. A forgotten
        # DEMO_MODE flag must never turn them back on in staging/production.
        return _app_env() in LOCAL_PAYMENT_BYPASS_ENVS
    return False


def payment_mode_valid() -> bool:
    provider = payment_provider_name()
    if provider == "disabled":
        # "disabled" means auto-paid only for local/test fixtures. A deployed
        # service must configure a real provider rather than silently bypassing
        # the money prerequisite.
        return _app_env() in LOCAL_PAYMENT_BYPASS_ENVS
    return provider in VALID_PAYMENT_PROVIDERS and payments_enabled()
