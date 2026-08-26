from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import live_required_smoke


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "live-required.yml"


def test_live_required_workflow_is_manual_complete_and_fail_closed() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in text
    assert "pull_request:" not in text
    assert "continue-on-error" not in text

    for job in (
        "postgres:",
        "redis:",
        "telegram:",
        "browser:",
        "provider-sandbox:",
        "live-required:",
    ):
        assert job in text

    assert "LIVE_TELEGRAM_BOT_TOKEN" in text
    assert "LIVE_YOOKASSA_SHOP_ID" in text
    assert "LIVE_YOOKASSA_SECRET_KEY" in text
    assert "LIVE_PUBLIC_BASE_URL" in text
    assert "Fail closed when Telegram live secret is absent" in text
    assert "Fail closed when provider sandbox secrets are absent" in text

    assert "if: ${{ always() }}" in text
    assert "POSTGRES_RESULT: ${{ needs.postgres.result }}" in text
    assert "REDIS_RESULT: ${{ needs.redis.result }}" in text
    assert "TELEGRAM_RESULT: ${{ needs.telegram.result }}" in text
    assert "BROWSER_RESULT: ${{ needs.browser.result }}" in text
    assert "PROVIDER_RESULT: ${{ needs.provider-sandbox.result }}" in text
    assert 'if [ "$result" != "success" ]; then' in text


def _provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("YOOKASSA_SHOP_ID", "test-shop")
    monkeypatch.setenv("YOOKASSA_SECRET_KEY", "test-secret")
    monkeypatch.setenv("LIVE_PROVIDER_AMOUNT_MINOR", "100")
    monkeypatch.setenv("LIVE_PROVIDER_CURRENCY", "RUB")
    monkeypatch.setattr(
        live_required_smoke,
        "settings",
        SimpleNamespace(
            payment_provider="yookassa",
            public_base_url="https://staging.example.test",
        ),
    )


@pytest.mark.asyncio
async def test_provider_smoke_uses_production_adapter_contract_and_requires_test_shop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider_env(monkeypatch)
    calls: list[dict[str, object]] = []

    class Provider:
        async def create_payment(self, **kwargs):  # noqa: ANN003, ANN202
            calls.append(kwargs)
            return SimpleNamespace(
                provider_payment_id="provider-payment",
                payment_url="https://yookassa.test/confirmation",
                raw={"test": False},
            )

    monkeypatch.setattr(live_required_smoke, "YooKassaPaymentProvider", Provider)

    with pytest.raises(RuntimeError, match="not marked test=true"):
        await live_required_smoke._provider_smoke()

    assert len(calls) == 1
    call = calls[0]
    assert set(call) == {"payment_id", "amount", "currency", "title", "metadata"}
    assert call["amount"] == Decimal("1.00")
    assert call["currency"] == "RUB"
    assert call["metadata"]["live_required"] == "true"  # type: ignore[index]


@pytest.mark.asyncio
async def test_provider_smoke_accepts_only_test_mode_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider_env(monkeypatch)

    class Provider:
        async def create_payment(self, **kwargs):  # noqa: ANN003, ANN202
            return SimpleNamespace(
                provider_payment_id="provider-payment",
                payment_url="https://yookassa.test/confirmation",
                raw={"test": True},
            )

    monkeypatch.setattr(live_required_smoke, "YooKassaPaymentProvider", Provider)
    await live_required_smoke._provider_smoke()


def test_required_live_secret_never_degrades_to_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="BOT_TOKEN is required for LIVE_REQUIRED"):
        live_required_smoke._required_env("BOT_TOKEN")
