from fastapi import APIRouter
from app.config import settings

router = APIRouter(tags=["security"])


def build_security_checks() -> dict:
    checks = {
        "admin_api_token_changed": settings.admin_api_token not in {"", "dev-admin-token", "CHANGE_ME"},
        "payment_webhook_secret_changed": settings.payment_webhook_secret not in {"", "dev-payment-secret", "CHANGE_ME"},
        "admin_password_set": bool(settings.admin_password),
        "bot_token_configured": bool(settings.bot_token and settings.bot_token != "CHANGE_ME") or not settings.run_bot,
        "production_payment_provider_ready": settings.payment_provider == "fake" or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        "public_base_url_configured": bool(settings.public_base_url and settings.public_base_url.startswith("http")),
    }
    warnings = []
    if settings.allow_token_query:
        warnings.append("ALLOW_TOKEN_QUERY=true удобно для локального экспорта, но в production лучше выключить.")
    if settings.payment_provider == "fake" and settings.app_env == "production":
        warnings.append("В production включен fake-провайдер оплат. Для реальных денег подключите YooKassa.")
    return {"ok": all(checks.values()), "checks": checks, "warnings": warnings, "version": "1.0.0-v19"}


@router.get("/security-check")
async def security_check():
    return build_security_checks()
