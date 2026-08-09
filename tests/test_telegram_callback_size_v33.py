from types import SimpleNamespace

from app.bot.screens.document_action_center import _reupload_callback


def test_document_reupload_snapshot_fits_telegram_callback_limit():
    document = SimpleNamespace(
        id=9_223_372_036_854_775_807,
        version=9_223_372_036_854_775_807,
    )

    callback_data = _reupload_callback(document)

    assert callback_data.startswith("document_reupload:")
    assert len(callback_data.encode("utf-8")) <= 64
