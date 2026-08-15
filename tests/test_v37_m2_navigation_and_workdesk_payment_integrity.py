from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_generic_consultation_navigation_resumes_exact_active_m2_stage():
    source = read("app/bot/screens/consultation_navigation_guard.py")

    assert '_ENTRY_CALLBACKS = {"contact_lawyer", "consult_start"}' in source
    assert "CaseStatus.M2_PAYMENT_PENDING" in source
    assert "guard_active_m2_payment_list(callback, db)" in source
    assert "CaseStatus.M2_CONSULTATION_BOOKED" in source
    assert '"consultation_booked_open"' in source
    assert "CaseStatus.M2_SLOT_PENDING" in source
    assert '"consult_booking_start"' in source
    assert "CaseStatus.M2_DESCRIPTION_PENDING" in source
    assert '"consult_subject_start"' in source
    assert "CaseStatus.M2_TO_M1" in source


def test_router_order_preserves_terminal_result_and_m1_rejection_before_m2_resume():
    bot = read("app/bot/bot.py")

    assert bot.index("consultation_results.router,") < bot.index(
        "telegram_safety_composite.router,"
    )
    assert bot.index("m1_rejection_recovery.router,") < bot.index(
        "telegram_safety_composite.router,"
    )
    assert bot.index("telegram_safety_composite.router,") < bot.index(
        "consultation_intake.router,"
    )


def test_safety_composite_includes_route_aware_navigation():
    source = read("app/bot/screens/telegram_safety_composite.py")
    assert "consultation_navigation_router" in source
    assert "router.include_router(consultation_navigation_router)" in source


def test_new_live_online_m2_ctas_use_exact_payment_center():
    source = read("app/bot/client_wording_patch.py")

    assert 'CLIENT_ACTIONS["M2_PAYMENT_PENDING"]' in source
    assert '"payments_open"' in source
    assert "route_aware_consultation_keyboard" in source
    assert 'callback == "consult_pay" and not payments_disabled()' in source
    assert 'callback = "payments_open"' in source
    assert "route_aware_after_documents_buttons" in source


def test_workdesk_flags_open_m2_payment_outside_payment_stage():
    source = read("app/api/workdesk_integrity_guard.py")
    assert "_add_m2_payment_reservation_integrity" in source
    assert "m2_live_payment_outside_payment_stage_" in source
    assert "M2_CONSULTATION_PAYMENT" in source
    assert "_M2_CLIENT_OPEN_PAYMENT_STATUSES" in source


def test_workdesk_compares_m2_payment_reservation_to_current_consultation_and_slot():
    source = read("app/api/workdesk_integrity_guard.py")
    block = source.split("async def _add_m2_payment_reservation_integrity", 1)[1].split(
        '@router.get("/admin/workdesk/integrity")', 1
    )[0]
    assert 'expected_key = f"consultation:{int(consultation.id)}:slot:{int(slot.id)}"' in block
    assert "payment.reservation_key" in block
    assert "m2_payment_reservation_mismatch_" in block
    assert "/admin/payment-reviews/ui?case_id=" in block
