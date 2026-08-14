from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_proof_bound_payment_recovery_is_reachable_before_legacy_workdesk():
    timeline = read("app/api/workdesk_timeline.py")
    initial_setup = read("app/api/initial_setup_wizard.py")
    main = read("app/main.py")

    assert "router.include_router(m1_internal_payment_recovery_router)" in timeline
    assert "router.include_router(workdesk_integrity_guard_router)" in initial_setup
    assert "router.include_router(workdesk_integrity_guard_router)" not in timeline
    assert main.index('(\"initial_setup_wizard\", initial_setup_wizard_router)') < main.index(
        '(\"workdesk_timeline\", workdesk_timeline_router)'
    )
    assert main.index('(\"workdesk_timeline\", workdesk_timeline_router)') < main.index(
        '(\"workdesk\", workdesk_router)'
    )


def test_assignment_excludes_business_profiles_without_live_lawyer_login():
    source = read("app/domain/cases/assignment_service.py")

    assert "from app.models.admin_user import AdminUser" in source
    assert "ROLE_LAWYER" in source
    assert "normalize_roles" in source
    assert "_active_lawyer_login_emails" in source
    assert "AdminUser.is_active.is_(True)" in source
    assert "ROLE_LAWYER in normalize_roles(roles)" in source
    assert "await self._assert_lawyer_login_ready(lawyer)" in source
    assert "login_ready" in source


def test_legacy_lawyer_creation_cannot_manufacture_an_unreachable_assignee():
    source = read("app/api/admin_queue_guard.py")
    main = read("app/main.py")

    assert '@router.get("/lawyers")' in source
    assert '@router.post("/lawyers")' in source
    retired = source.split('@router.post("/lawyers")', 1)[1].split(
        '@router.post("/scheduler/run-once")', 1
    )[0]
    assert "Lawyer(" not in retired
    assert '"canonical_path": "/access/ui"' in retired
    assert '"required_role": "superadmin"' in retired
    assert main.index('(\"admin_queue_guard\", admin_queue_guard_router)') < main.index(
        '(\"admin\", admin_router)'
    )


def test_manual_full_scheduler_requires_personal_mfa_superadmin_and_audit():
    source = read("app/api/admin_queue_guard.py")
    block = source.split('@router.post("/scheduler/run-once")', 1)[1]

    assert "resolve_document_actor" in block
    assert "actor.role != ROLE_SUPERADMIN" in block
    assert '"RUN_SCHEDULER_ONCE"' in block
    assert 'action="scheduler.manual_run_requested"' in block
    assert 'action="scheduler.manual_run_completed"' in block
    assert "await AppScheduler().run_once()" in block


def test_workdesk_flags_old_unreachable_assignments_and_paid_transient_stalls():
    guard = read("app/api/workdesk_integrity_guard.py")

    assert '"assigned_lawyer_cannot_login"' in guard
    assert '"m1_internal_paid_stage_stuck"' in guard
    assert '"m1_internal_paid_stage_without_proof"' in guard
    assert "/recover-payment-stage/ui" in guard
    assert "M1InternalPaymentRecoveryService.plan_for_status" in guard
    assert "PaymentStatus.PAID.value" in guard


def test_telegram_stale_m1_views_are_mounted_before_legacy_m1_handlers():
    source = read("app/bot/bot.py")
    stale = read("app/bot/screens/m1_stale_view_guard.py")

    assert "m1_stale_view_guard," in source
    assert source.index("m1_stale_view_guard.router,") < source.index("m1_stages.router,")
    assert 'callback.data != "poa_instruction"' in stale
    assert 'callback.data != "court_status"' in stale
    assert "Старое сообщение ничего не изменило" in stale


def test_consultation_cancel_and_reschedule_are_bound_to_current_booking_snapshot():
    source = read("app/bot/screens/consultations.py")

    assert "consult_reschedule_confirm:{consultation.id}:{old_slot_id}:{new_slot.id}" in source
    assert "int(consultation.id) != expected_consultation_id" in source
    assert "expected_old_slot_id=expected_old_slot_id" in source
    assert "consult_cancel_confirm:{consultation.id}:{int(consultation.slot_id or 0)}" in source
    assert 'if callback.data == "consult_cancel_confirm":' in source
    assert "int(consultation.slot_id or 0) != expected_slot_id" in source
