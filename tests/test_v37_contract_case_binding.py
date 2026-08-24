from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_fresh_contract_confirmation_contains_case_document_and_version():
    source = read("app/bot/screens/service_contract.py")

    assert 'return f"contract_confirm:v2:{int(case_id)}:{int(document_id)}:{int(version)}"' in source
    assert 'value.startswith("contract_confirm:v2:")' in source
    assert "return case_id, document_id, version" in source


def test_contract_confirmation_fails_closed_after_case_switch():
    source = read("app/bot/screens/service_contract.py")

    handler = source.split("async def confirm_exact_service_contract", 1)[1].split(
        "async def _show_payment_result", 1
    )[0]
    assert "expected_case_id is not None and expected_case_id != case_id" in handler
    assert "Подтверждение и платёж не выполнены" in handler
    assert "current_service_contract(db, case_id=case_id)" in handler


def test_contract_payment_handoff_uses_exact_case_callback():
    source = read("app/bot/screens/service_contract.py")

    result = source.split("async def _show_payment_result", 1)[1]
    assert 'bound_case_callback("pay_start_30000", case_id)' in result
    assert 'bound_case_callback("message_create", case_id)' in result
    assert 'f"Обращение № {case_number}' in result


def test_legacy_generic_or_case_only_contract_sign_never_confirms_or_creates_payment():
    source = read("app/bot/screens/service_contract.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "contract_sign")' in source
    legacy = source.split("async def legacy_contract_confirmation", 1)[1].split(
        "async def confirm_exact_service_contract", 1
    )[0]
    assert "confirm_service_contract(" not in legacy
    assert "без идентификатора документа и номера версии" in legacy
    assert bot.index("service_contract.router") < bot.index("m1_stages.router")
