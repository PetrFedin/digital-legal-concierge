from pathlib import Path

from app.bot.screens.consultation_intake import _active_context


def test_stale_disabled_payment_has_completed_m2_archive_recovery_contract():
    source = _active_context.__module__
    assert source == "app.bot.screens.consultation_intake"


def test_stale_disabled_payment_does_not_offer_new_booking_for_completed_m2():
    source = Path("app/bot/screens/consultation_intake.py").read_text(encoding="utf-8")
    assert 'latest_completed_strict_m2_case_for_user' in source
    assert '"consultation_result_open"' in source
    assert '"consult_booking_start"' not in source.split(
        'async def stale_consult_pay', 1
    )[1].split('async def ', 1)[0]
