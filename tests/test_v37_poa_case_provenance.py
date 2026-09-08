from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_poa_upload_entry_is_exact_case_bound_and_arms_shared_document_guard():
    source = read("app/bot/screens/poa_handoff.py")

    assert 'callback_matches_action(c.data, "poa_upload_document")' in source
    upload = source.split("async def upload_poa_document", 1)[1].split(
        "async def report_poa_ready", 1
    )[0]
    assert 'action="poa_upload_document"' in upload
    assert "allow_legacy_message_case_context=True" in upload
    assert "document_case_id=case_id" in upload
    assert 'document_type="POWER_OF_ATTORNEY"' in upload
    assert "DocumentUploadStates.waiting_file" in upload


def test_exact_poa_ready_callback_is_owned_before_legacy_status_mutator():
    source = read("app/bot/screens/poa_handoff.py")
    bot = read("app/bot/bot.py")

    assert 'callback_matches_action(c.data, "poa_done")' in source
    report = source.split("async def report_poa_ready", 1)[1]
    assert 'action="poa_done"' in report
    assert "allow_legacy_message_case_context=True" in report
    assert "CLIENT_POA_READY_REPORTED" in source
    assert "change_status(" not in report
    assert bot.index("poa_handoff.router") < bot.index("m1_stages.router")


def test_poa_recovery_actions_keep_case_identity():
    source = read("app/bot/screens/poa_handoff.py")

    show = source.split("async def _show", 1)[1].split(
        "async def upload_poa_document", 1
    )[0]
    assert 'bound_case_callback("poa_upload_document", int(case_id))' in show
    assert 'bound_case_callback("message_create", int(case_id))' in show
    assert 'f"Обращение № {case_number}' in source
