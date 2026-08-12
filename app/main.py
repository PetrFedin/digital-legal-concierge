import asyncio
from collections import Counter

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.acceptance_center import router as acceptance_center_router
from app.api.access_management import router as access_management_router
from app.api.admin import router as admin_router
from app.api.assignment_queue import router as assignment_queue_router
from app.api.audit_center import router as audit_center_router
from app.api.auth import router as auth_router
from app.api.backup_center import router as backup_center_router
from app.api.backup_manager import router as backup_manager_router
from app.api.calculator_builder import router as calculator_builder_router
from app.api.case_assignment import router as case_assignment_router
from app.api.consultation_outcomes import router as consultation_outcomes_router
from app.api.consultation_slots import router as consultation_slots_router
from app.api.diagnostic_center import router as diagnostic_center_router
from app.api.document_access import router as document_access_router
from app.api.document_access_portal import router as document_access_portal_router
from app.api.exports import router as exports_router
from app.api.final_handover_center import router as final_handover_center_router
from app.api.final_qa_center import router as final_qa_center_router
from app.api.go_live_center import router as go_live_center_router
from app.api.guided_consultation_outcomes import router as guided_consultation_outcomes_router
from app.api.guided_lawyer_ui import router as guided_lawyer_ui_router
from app.api.guided_message_center import router as guided_message_center_router
from app.api.handover import router as handover_router
from app.api.health_center import router as health_center_router
from app.api.initial_setup_wizard import router as initial_setup_wizard_router
from app.api.install_wizard import router as install_wizard_router
from app.api.integration_center import router as integration_center_router
from app.api.launch_assistant import router as launch_assistant_router
from app.api.lawyer import router as lawyer_router
from app.api.lawyer_consultation_desk import router as lawyer_consultation_desk_router
from app.api.lawyer_m1_claim import router as lawyer_m1_claim_router
from app.api.lawyer_m1_enforcement import router as lawyer_m1_enforcement_router
from app.api.lawyer_workspace import router as lawyer_workspace_router
from app.api.lawyer_workspace_rejection_ui import router as lawyer_workspace_rejection_ui_router
from app.api.maintenance_center import router as maintenance_center_router
from app.api.message_center import router as message_center_router
from app.api.message_center_role_ui import router as message_center_role_ui_router
from app.api.mfa import router as mfa_router
from app.api.monitoring_center import router as monitoring_center_router
from app.api.notification_center import router as notification_center_router
from app.api.operations_center import router as operations_center_router
from app.api.operator import router as operator_router
from app.api.ops_guide import router as ops_guide_router
from app.api.payment_review_center import router as payment_review_center_router
from app.api.payment_webhooks import router as payment_router
from app.api.production_center import router as production_center_router
from app.api.recovery_center import router as recovery_center_router
from app.api.refund_center import router as refund_center_router
from app.api.release_manager import router as release_manager_router
from app.api.retention_center import router as retention_center_router
from app.api.runtime import router as runtime_router
from app.api.scenario_map import router as scenario_map_router
from app.api.search_center import router as search_center_router
from app.api.security import router as security_router
from app.api.security_event_center import router as security_event_center_router
from app.api.settings_ui import router as settings_ui_router
from app.api.sla_center import router as sla_center_router
from app.api.task_center import router as task_center_router
from app.api.template_builder import router as template_builder_router
from app.api.web_admin import router as web_admin_router
from app.api.workdesk import router as workdesk_router
from app.api.workdesk_timeline import router as workdesk_timeline_router
from app.config import settings
from app.domain.payments.mode import (
    payment_mode_valid,
    payment_provider_name,
    payments_disabled,
    payments_enabled,
)
from app.security.backup_freshness import backup_freshness_status
from app.security.client_address import (
    TrustedProxyClientAddressMiddleware,
    trusted_proxy_networks,
)
from app.security.http_security import (
    RequestOriginGuardMiddleware,
    SecurityHeadersMiddleware,
)
from app.security.keyring import security_key_status
from app.security.session_guard import AdminSessionGuardMiddleware

VERSION = "1.0.0-v46"


def _namespace_duplicate_route_names(router_specs):
    """Make reverse-route names deterministic without changing unique public names."""
    counts = Counter(
        route.name
        for _, router in router_specs
        for route in router.routes
        if getattr(route, "name", None)
    )
    used_names = {name for name, count in counts.items() if count == 1}

    for namespace, router in router_specs:
        for route in router.routes:
            original_name = getattr(route, "name", None)
            if not original_name or counts[original_name] <= 1:
                continue
            candidate = f"{namespace}_{original_name}"
            suffix = 2
            while candidate in used_names:
                candidate = f"{namespace}_{original_name}_{suffix}"
                suffix += 1
            route.name = candidate
            used_names.add(candidate)


def create_app():
    app = FastAPI(title="Digital Legal Concierge Bot", version=VERSION)
    app.add_middleware(AdminSessionGuardMiddleware)
    app.add_middleware(RequestOriginGuardMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(TrustedProxyClientAddressMiddleware)

    router_specs = [
        ("maintenance_center", maintenance_center_router),
        ("final_handover_center", final_handover_center_router),
        ("final_qa_center", final_qa_center_router),
        ("go_live_center", go_live_center_router),
        ("production_center", production_center_router),
        ("initial_setup_wizard", initial_setup_wizard_router),
        ("template_builder", template_builder_router),
        ("calculator_builder", calculator_builder_router),
        ("integration_center", integration_center_router),
        ("operations_center", operations_center_router),
        ("monitoring_center", monitoring_center_router),
        ("backup_manager", backup_manager_router),
        ("release_manager", release_manager_router),
        ("acceptance_center", acceptance_center_router),
        ("search_center", search_center_router),
        ("message_center_role_ui", message_center_role_ui_router),
        ("guided_message_center", guided_message_center_router),
        ("message_center", message_center_router),
        ("audit_center", audit_center_router),
        ("security_event_center", security_event_center_router),
        ("notification_center", notification_center_router),
        ("backup_center", backup_center_router),
        ("auth", auth_router),
        ("mfa", mfa_router),
        ("access_management", access_management_router),
        ("consultation_slots", consultation_slots_router),
        ("case_assignment", case_assignment_router),
        ("refund_center", refund_center_router),
        ("retention_center", retention_center_router),
        ("payment_review_center", payment_review_center_router),
        ("guided_consultation_outcomes", guided_consultation_outcomes_router),
        ("consultation_outcomes", consultation_outcomes_router),
        ("sla_center", sla_center_router),
        ("security", security_router),
        ("launch_assistant", launch_assistant_router),
        ("health_center", health_center_router),
        ("diagnostic_center", diagnostic_center_router),
        ("recovery_center", recovery_center_router),
        ("install_wizard", install_wizard_router),
        ("task_center", task_center_router),
        ("settings_ui", settings_ui_router),
        ("admin", admin_router),
        ("payment_webhooks", payment_router),
        ("lawyer_workspace_rejection_ui", lawyer_workspace_rejection_ui_router),
        ("guided_lawyer_ui", guided_lawyer_ui_router),
        ("lawyer", lawyer_router),
        ("lawyer_m1_claim", lawyer_m1_claim_router),
        ("lawyer_m1_enforcement", lawyer_m1_enforcement_router),
        ("lawyer_workspace", lawyer_workspace_router),
        ("lawyer_consultation_desk", lawyer_consultation_desk_router),
        ("document_access", document_access_router),
        ("document_access_portal", document_access_portal_router),
        ("runtime", runtime_router),
        ("assignment_queue", assignment_queue_router),
        ("workdesk_timeline", workdesk_timeline_router),
        ("workdesk", workdesk_router),
        ("web_admin", web_admin_router),
        ("operator", operator_router),
        ("exports", exports_router),
        ("scenario_map", scenario_map_router),
        ("ops_guide", ops_guide_router),
        ("handover", handover_router),
    ]
    _namespace_duplicate_route_names(router_specs)
    for _, router in router_specs:
        app.include_router(router)

    @app.get("/")
    async def root():
        return RedirectResponse(url="/operator")

    @app.get("/health")
    async def health():
        return {"ok": True, "env": settings.app_env, "version": VERSION}

    @app.get("/ready")
    async def ready():
        from pathlib import Path

        key_status = security_key_status()
        backup_freshness = await asyncio.to_thread(backup_freshness_status)
        provider = payment_provider_name()
        payment_disabled = payments_disabled()
        payment_webhook_secret_ready = payment_disabled or (
            settings.app_env != "production"
            or (
                len(str(settings.payment_webhook_secret or "")) >= 32
                and settings.payment_webhook_secret
                not in {"dev-payment-secret", "change-this-payment-secret"}
            )
        )
        try:
            proxy_networks = trusted_proxy_networks()
            trusted_proxy_config_valid = True
        except RuntimeError:
            proxy_networks = ()
            trusted_proxy_config_valid = False

        checks = {
            "bot_token_configured": bool(
                settings.bot_token and settings.bot_token != "CHANGE_ME"
            )
            or not settings.run_bot,
            "admin_token_configured": bool(
                settings.admin_api_token
                and settings.admin_api_token != "dev-admin-token"
            ),
            "storage_dir_exists": Path(settings.storage_dir).exists(),
            "document_upload_limit_valid": (
                1 <= int(settings.max_document_upload_mb) <= 100
            ),
            "quarantine_retention_valid": (
                1 <= int(settings.upload_quarantine_retention_days) <= 90
            ),
            "document_access_ttl_valid": (
                30 <= int(settings.document_access_grant_ttl_seconds) <= 300
            ),
            "document_access_limit_valid": (
                1 <= int(settings.document_access_max_active_grants) <= 20
            ),
            "backup_size_limit_valid": 1 <= int(settings.max_backup_mb) <= 10240,
            "backup_retention_valid": (
                1 <= int(settings.backup_retention_days) <= 3650
            ),
            "backup_max_age_valid": (
                1
                <= int(settings.backup_max_age_hours)
                <= min(8760, int(settings.backup_retention_days) * 24)
            ),
            "backup_freshness_cache_valid": (
                0 <= int(settings.backup_freshness_cache_seconds) <= 3600
            ),
            "backup_clock_skew_valid": (
                0 <= int(settings.backup_future_clock_skew_seconds) <= 3600
            ),
            "recent_verified_backup": bool(backup_freshness.ok),
            "closed_case_retention_valid": (
                30 <= int(settings.closed_case_retention_days) <= 36500
            ),
            "case_retention_scan_batch_valid": (
                1 <= int(settings.case_retention_scan_batch_size) <= 1000
            ),
            "case_retention_execution_timeout_valid": (
                60
                <= int(settings.case_retention_execution_timeout_seconds)
                <= 86400
            ),
            "payment_webhook_body_limit_valid": (
                1 <= int(settings.max_payment_webhook_kb) <= 1024
            ),
            "payment_webhook_timeout_valid": (
                30
                <= int(settings.payment_webhook_processing_timeout_seconds)
                <= 3600
            ),
            "payment_webhook_attempt_limit_valid": (
                1 <= int(settings.payment_webhook_max_attempts) <= 50
            ),
            "payment_webhook_secret_ready": payment_webhook_secret_ready,
            "payment_mode_valid": payment_mode_valid(),
            # Kept for compatibility with deployment scripts and dashboards.
            "payment_provider_configured": payment_mode_valid(),
            "trusted_proxy_config_valid": trusted_proxy_config_valid,
            "trusted_proxy_hop_limit_valid": (
                1 <= int(settings.trusted_proxy_max_hops) <= 20
            ),
            "database_url_configured": bool(settings.database_url),
            "scheduler_enabled": settings.run_scheduler,
            "bot_enabled": settings.run_bot,
            "legacy_admin_token_disabled_in_production": (
                settings.app_env != "production"
                or settings.admin_api_token != "dev-admin-token"
            ),
            "public_base_url_is_https": (
                settings.app_env != "production"
                or settings.public_base_url.lower().startswith("https://")
            ),
            "security_keys_ready": bool(key_status["ok"]),
        }
        return {
            "ok": all(checks.values()),
            "checks": checks,
            "payment_mode": {
                "provider": provider,
                "enabled": payments_enabled(),
                "disabled_by_configuration": payment_disabled,
                "payment_links_created": payments_enabled(),
                "pilot_flows_continue_without_payment": payment_disabled,
            },
            "security_keys": key_status,
            "trusted_proxy_security": {
                "enabled": bool(proxy_networks),
                "trusted_cidrs_count": len(proxy_networks),
                "max_hops": settings.trusted_proxy_max_hops,
                "trust_forwarded_proto": settings.trust_forwarded_proto,
                "untrusted_forwarded_headers": "ignored",
                "malformed_trusted_chain": "rejected_and_audited",
            },
            "document_upload_security": {
                "max_upload_mb": settings.max_document_upload_mb,
                "quarantine_enabled": settings.quarantine_rejected_uploads,
                "quarantine_retention_days": (
                    settings.upload_quarantine_retention_days
                ),
                "allowed_formats": ["pdf", "docx", "jpeg", "png"],
                "encryption_at_rest": True,
                "encryption_key_id": key_status["active_key_ids"].get(
                    "document_encryption"
                ),
            },
            "document_delivery_security": {
                "enabled": True,
                "personal_sessions_only": True,
                "one_time_grants": True,
                "grant_ttl_seconds": settings.document_access_grant_ttl_seconds,
                "max_active_grants": settings.document_access_max_active_grants,
                "session_bound": True,
                "direct_storage_paths_exposed": False,
            },
            "case_retention": {
                "enabled": True,
                "retention_days": settings.closed_case_retention_days,
                "scheduler_dry_run": settings.case_retention_dry_run,
                "execution_timeout_seconds": (
                    settings.case_retention_execution_timeout_seconds
                ),
                "two_person_approval": True,
                "personal_mfa_superadmin_required": True,
                "legal_hold": True,
                "payment_ledger_preserved": True,
                "audit_chain_preserved": True,
                "physical_block_overwrite_claimed": False,
            },
            "backup_security": {
                "enabled": True,
                "encryption": "AES-256-GCM",
                "key_id": key_status["active_key_ids"].get("backup_encryption"),
                "manifest_sha256": True,
                "secrets_included": False,
                "restore_mode": "verified_staging_only",
                "max_backup_mb": settings.max_backup_mb,
                "retention_days": settings.backup_retention_days,
                "readiness_required_in_production": (
                    settings.backup_readiness_required_in_production
                ),
                "max_age_hours": settings.backup_max_age_hours,
                "freshness": backup_freshness.as_dict(),
            },
            "payment_webhook_security": {
                "enabled": not payment_disabled,
                "max_body_kb": settings.max_payment_webhook_kb,
                "idempotent_ledger": True,
                "replay_payload_conflict_detection": True,
                "processing_timeout_seconds": (
                    settings.payment_webhook_processing_timeout_seconds
                ),
                "max_attempts": settings.payment_webhook_max_attempts,
                "authoritative_provider_recheck": provider == "yookassa",
                "raw_provider_payload_persisted": False,
            },
            "security_event_monitoring": {
                "enabled": True,
                "privacy_preserving_identifiers": True,
                "tamper_evident_storage": True,
            },
            "version": VERSION,
        }

    @app.get("/launch-check")
    async def launch_check():
        return {
            "version": VERSION,
            "handover": "/handover",
            "security_check": "/security-check",
            "launch_assistant": "/launch-assistant",
            "operator_workspace": "/operator",
            "admin_ui": "/admin-ui",
            "admin_workdesk_ui": "/admin/workdesk/ui",
            "lawyer_ui": "/lawyer/ui",
            "access_management": "/access/ui",
            "mfa_management": "/mfa/manage",
            "consultation_slots_api": "/consultation-slots",
            "consultation_slots_ui": "/consultation-slots/ui",
            "consultation_outcomes_api": "/admin/consultation-outcomes",
            "consultation_outcomes_ui": "/admin/consultation-outcomes/ui",
            "case_sla_api": "/admin/sla",
            "case_sla_ui": "/admin/sla/ui",
            "case_assignment_api": "/admin/case-assignment/lawyers",
            "refund_center_api": "/admin/refunds",
            "refund_center_ui": "/admin/refunds/ui",
            "payment_review_center_api": "/admin/payment-reviews",
            "payment_review_center_ui": "/admin/payment-reviews/ui",
            "audit_integrity_api": "/audit-center/integrity",
            "audit_center_ui": "/audit-center/ui",
            "security_event_center_api": "/security-events/status",
            "security_event_center_ui": "/security-events/ui",
            "document_access_api": "/document-access/cases/{case_id}/documents",
            "document_access_ui": "/document-access/ui",
            "backup_center_ui": "/backup-center/ui",
            "backup_center_status": "/backup-center/status",
            "retention_center_ui": "/retention/ui",
            "retention_center_status": "/retention/status",
            "health": "/health",
            "ready": "/ready",
            "bot_enabled": settings.run_bot,
            "scheduler_enabled": settings.run_scheduler,
            "payment_provider": payment_provider_name(),
            "payments_enabled": payments_enabled(),
            "payments_disabled": payments_disabled(),
            "pilot_flows_continue_without_payment": payments_disabled(),
            "storage_dir": settings.storage_dir,
            "trusted_proxy_client_resolution": True,
            "trusted_proxy_allowlist_required": True,
            "untrusted_forwarded_headers_ignored": True,
            "forwarded_proto_trusted_only": True,
            "http_origin_guard": True,
            "security_headers": True,
            "security_keyring": True,
            "mfa_key_rotation_job": True,
            "document_content_inspection": True,
            "document_quarantine": settings.quarantine_rejected_uploads,
            "legacy_document_rescan_job": True,
            "document_encryption_at_rest": True,
            "document_encryption_rotation_job": True,
            "secure_document_delivery": True,
            "one_time_document_grants": True,
            "session_bound_document_grants": True,
            "document_grant_cleanup_job": True,
            "encrypted_backup_format": True,
            "backup_manifest_integrity": True,
            "backup_secrets_excluded": True,
            "verified_staging_restore": True,
            "encrypted_backup_retention_job": True,
            "closed_case_retention_discovery": True,
            "case_legal_hold": True,
            "case_retention_two_person_approval": True,
            "case_content_deletion_resumable": True,
            "bounded_payment_webhook_body": True,
            "idempotent_payment_webhook_ledger": True,
            "payment_webhook_replay_protection": True,
            "payment_webhook_dead_letter_state": True,
            "sanitized_payment_provider_payloads": True,
            "tamper_evident_audit_chain": True,
            "immutable_audit_events": True,
            "tamper_evident_security_events": True,
            "cross_site_security_event_logging": True,
            "privacy_preserving_security_identifiers": True,
        }

    return app


app = create_app()


async def main():
    """Compatibility entrypoint delegated to the canonical supervisor."""
    from app.process import main as run_supervised_process

    return await run_supervised_process()


if __name__ == "__main__":
    asyncio.run(main())