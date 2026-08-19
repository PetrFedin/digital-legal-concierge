from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_payment_service_serializes_active_attempt_creation_on_case_row():
    service = read("app/domain/payments/payment_service.py")

    block = service.split("async def get_or_create_payment", 1)[1].split(
        "async def success_fee_quote_for_case", 1
    )[0]
    assert "case_id = int(case.id)" in block
    assert "select(Case)" in block
    assert ".with_for_update()" in block
    lock_at = block.index(".with_for_update()")
    query_at = block.index("query = select(Payment)", lock_at)
    create_at = block.index("payment = Payment(", query_at)
    assert lock_at < query_at < create_at
    assert 'raise LookupError("Дело не найдено")' in block


def test_m2_old_active_link_is_flushed_expired_before_replacement_insert():
    service = read("app/domain/payments/payment_service.py")
    block = service.split("if payment_code == PaymentCode.M2_CONSULTATION_PAYMENT:", 1)[1].split(
        "received_conflict =", 1
    )[0]

    assert "_expire_stale_consultation_payments" in block
    assert "await self.db.flush()" in block


def test_received_money_review_blocks_second_charge_but_old_paid_m2_followup_can_be_distinct():
    service = read("app/domain/payments/payment_service.py")
    helper = service.split("async def _received_payment_conflict", 1)[1].split(
        "async def _restore_m2_slot_selection_after_hold_loss", 1
    )[0]
    create = service.split("async def get_or_create_payment", 1)[1].split(
        "async def success_fee_quote_for_case", 1
    )[0]

    assert "PaymentStatus.PAID_REVIEW" in helper
    assert "PaymentStatus.REFUND_PENDING" in helper
    assert "PaymentStatus.REFUND_DECLINED" in helper
    assert "payment_code != PaymentCode.M2_CONSULTATION_PAYMENT" in helper
    assert "existing.reservation_key == reservation_key" in helper
    assert "received_conflict = await self._received_payment_conflict" in create
    assert "Повторная оплата заблокирована" in create
    assert "клиент не должен платить повторно" in create


def test_provider_creation_uses_stable_internal_payment_idempotence_key():
    providers = read("app/domain/payments/providers.py")

    assert "def payment_idempotence_key(payment_id: int)" in providers
    assert "hashlib.sha256" in providers
    assert "settings.app_env" in providers
    assert '"Idempotence-Key": payment_idempotence_key(payment_id)' in providers
    assert 'provider_payment_id = f"fake-{payment_idempotence_key(payment_id)[:32]}"' in providers
    assert "uuid4" not in providers


def test_incomplete_yookassa_create_response_fails_before_internal_waiting_state_can_be_saved():
    providers = read("app/domain/payments/providers.py")
    service = read("app/domain/payments/payment_service.py")

    assert 'provider_payment_id = str(data.get("id") or "").strip()' in providers
    assert 'payment_url = str(confirmation.get("confirmation_url") or "").strip()' in providers
    assert "if not provider_payment_id or not payment_url:" in providers
    assert "Платёжный провайдер вернул неполный ответ" in providers
    assert "повтор с тем же idempotency key безопасно восстановит операцию" in providers

    link_block = service.split("async def create_payment_link", 1)[1].split(
        "async def mark_paid", 1
    )[0]
    result_call = link_block.index("result = await provider.create_payment(")
    waiting_transition = link_block.index("to_status=PaymentStatus.WAITING_CONFIRMATION")
    assert "PaymentLifecycleService.transition" in link_block
    assert result_call < waiting_transition


def test_webhook_processing_already_locks_payment_row_for_duplicate_provider_events():
    webhook = read("app/domain/payments/payment_webhook_service.py")

    assert "async def _lock_payment" in webhook
    assert ".with_for_update()" in webhook
    assert "payment = await self._lock_payment(payment.id)" in webhook
    assert "if payment.status == PaymentStatus.PAID:" in webhook


def test_payment_migration_enforces_one_active_case_code_and_provider_operation():
    migration = read("migrations/versions/20260819_0016_payment_idempotency_invariants.py")

    assert 'revision = "20260819_0016"' in migration
    assert 'down_revision = "20260819_0015"' in migration
    assert 'ACTIVE_INDEX = "uq_payments_one_active_attempt_per_case_code"' in migration
    assert 'PROVIDER_INDEX = "uq_payments_provider_operation"' in migration
    assert "status IN ('PENDING', 'WAITING_CONFIRMATION')" in migration
    assert "provider_payment_id IS NOT NULL" in migration
    assert "GROUP BY case_id, payment_code" in migration
    assert "GROUP BY COALESCE(provider, ''), provider_payment_id" in migration
    assert "will not expire, cancel, refund or choose a payment automatically" in migration
    assert "no payment rows are rewritten automatically" in migration
    assert '["case_id", "payment_code"]' in migration
    assert '["provider", "provider_payment_id"]' in migration
    assert "unique=True" in migration
