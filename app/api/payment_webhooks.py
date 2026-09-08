"""Single runtime owner for real payment-provider webhook paths.

The historical module is preserved as ``payment_webhooks_impl``. Fake payment
handlers remain importable for ``payment_safety_guard`` but are not registered
here, so simulated payment endpoints cannot shadow the production guard.
"""

from fastapi import APIRouter

from app.api import payment_webhooks_impl as _impl

router = APIRouter(prefix="/webhooks/payments", tags=["payment-webhooks"])

router.add_api_route(
    "/yookassa",
    _impl.yookassa_payment_webhook,
    methods=["POST"],
    name="yookassa_payment_webhook",
)
router.add_api_route(
    "/payment-result",
    _impl.payment_result,
    methods=["GET"],
    name="payment_result",
)

# Local/test safety guard still delegates to these implementation handlers.
fake_payment_webhook = _impl.fake_payment_webhook
fake_payment_page = _impl.fake_payment_page
fake_payment_success = _impl.fake_payment_success
fake_payments_enabled = _impl.fake_payments_enabled
require_fake_payments = _impl.require_fake_payments
parse_provider_datetime = _impl.parse_provider_datetime
validate_verified_yookassa_payment = _impl.validate_verified_yookassa_payment
yookassa_payment_webhook = _impl.yookassa_payment_webhook
payment_result = _impl.payment_result


def __getattr__(name: str):
    if name == "router":
        return router
    return getattr(_impl, name)


__all__ = [
    "fake_payment_page",
    "fake_payment_success",
    "fake_payment_webhook",
    "fake_payments_enabled",
    "parse_provider_datetime",
    "payment_result",
    "require_fake_payments",
    "router",
    "validate_verified_yookassa_payment",
    "yookassa_payment_webhook",
]
