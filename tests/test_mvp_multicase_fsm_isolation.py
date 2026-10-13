from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_message_draft_pins_case_before_sending():
    source = (ROOT / "app/bot/screens/messages.py").read_text()
    assert 'case_id=int(case.id) if case else None' in source
    assert 'int(case.id) != int(intended_case_id)' in source
    assert "Дело изменилось. Вопрос не отправлен." in source


def test_document_upload_pins_case_before_external_file_read():
    source = (ROOT / "app/bot/screens/documents.py").read_text()
    assert 'upload_case_id=int(case.id)' in source
    assert 'int(data["upload_case_id"]) != int(case.id)' in source
    assert source.index('int(data["upload_case_id"]) != int(case.id)') < source.index(
        'stored = await storage.save_telegram_file('
    )
