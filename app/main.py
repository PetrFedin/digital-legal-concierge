import asyncio
from collections import Counter

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.api.acceptance_center import router as acceptance_center_router
from app.api.access_management import router as access_management_router
from app.api.admin import router as admin_router
from app.api.admin_queue_guard import router as admin_queue_guard_router
from app.api.assignment_queue_product import router as assignment_queue_product_router
from app.api.audit_center import router as audit_center_router
from app.api.auth import router as auth_router
from app.api.backup_center import router as backup_center_router
from app.api.backup_manager import router as backup_manager_router
from app.api.calculator_builder import router as calculator_builder_router
from app.api.case_assignment import router as case_assignment_router
from app.api.consultation_outcomes_product import router as consultation_outcomes_product_router
from app.api.consultation_slots import router as consultation_slots_router
from app.api.contract_center import router as contract_center_router
from app.api.diagnostic_center import router as diagnostic_center_router
from app.api.document_access_portal import router as document_access_portal_router
from app.api.document_access_product import router as document_access_product_router
from app.api.exports import router as exports_router
from app.api.final_handover_center import router as final_handover_center_router
from app.api.final_qa_center import router as final_qa_center_router
from app.api.go_live_center import router as go_live_center_router
from app.api.handover import router as handover_router
from app.api.health_center import router as health_center_router
from app.api.initial_setup_wizard import router as initial_setup_wizard_router
from app.api.install_wizard import router as install_wizard_router
from app.api.integration_center import router as integration_center_router
from app.api.launch_assistant import router as launch_assistant_router
from app.api.lawyer import router as lawyer_router
from app.api.lawyer_m1_claim import router as lawyer_m1_claim_router
from app.api.lawyer_m1_enforcement import router as lawyer_m1_enforcement_router
from app.api.lawyer_product import router as lawyer_product_router
from app.api.maintenance_center import router as maintenance_center_router
from app.api.message_center_product import router as message_center_product_router
from app.api.message_center_role_ui import router as message_center_role_ui_router
from app.api.mfa import router as mfa_router
from app.api.monitoring_center import router as monitoring_center_router
from app.api.notification_center import router as notification_center_router
from app.api.operations_center import router as operations_center_router
from app.api.operator import router as operator_router
from app.api.ops_guide import router as ops_guide_router
from app.api.payment_review_product import router as payment_review_product_router
from app.api.payment_safety_guard import router as payment_safety_guard_router
from app.api.payment_webhooks import router as payment_router
from app.api.production_center import router as production_center_router
from app.api.recovery_center import router as recovery_center_router
from app.api.refund_product import router as refund_product_router
from app.api.release_manager import router as release_manager_router
from app.api.retention_center import router as retention_center_router
from app.api.runtime import router as runtime_router
from app.api.scenario_map import router as scenario_map_router
from app.api.search_center import router as search_center_router
from app.api.security import router as security_router
from app.api.security_event_center import router as security_event_center_router
from app.api.self_filing_product import router as self_filing_product_router
from app.api.settings_ui import router as settings_ui_router
from app.api.sla_product import router as sla_product_router
from app.api.task_center import router as task_center_router
from app.api.technical_cases_compat import router as technical_cases_compat_router
from app.api.template_builder import router as template_builder_router
from app.api.web_admin import router as web_admin_router
from app.api.workdesk_integrity_product import router as workdesk_integrity_product_router
from app.api.workdesk_product import router as workdesk_product_router
from app.api.workdesk_timeline import router as workdesk_timeline_router
from app.config import settings
from app.release import APPLICATION_VERSION
from app.domain.payments.mode import (
    payment_mode_valid,
    payment_provider_name,
    payments_disabled,
    payments_enabled,
    payments_offline,
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

VERSION = APPLICATION_VERSION


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
    app = FastAPI(title="Юридический сервис", version=VERSION)
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
        ("message_center_product", message_center_product_router),
        ("audit_center", audit_center_router),
        ("security_event_center", security_event_center_router),
        ("notification_center", notification_center_router),
        ("backup_center", backup_center_router),
        ("auth", auth_router),
        ("mfa", mfa_router),
        ("access_management", access_management_router),
        ("consultation_slots", consultation_slots_router),
        ("case_assignment", case_assignment_router),
        ("refund_product", refund_product_router),
        ("retention_center", retention_center_router),
        ("payment_review_product", payment_review_product_router),
        ("consultation_outcomes_product", consultation_outcomes_product_router),
        ("sla_product", sla_product_router),
        ("security", security_router),
        ("launch_assistant", launch_assistant_router),
        ("health_center", health_center_router),
        ("diagnostic_center", diagnostic_center_router),
        ("recovery_center", recovery_center_router),
        ("install_wizard", install_wizard_router),
        ("task_center", task_center_router),
        ("settings_ui", settings_ui_router),
        ("technical_cases_compat", technical_cases_compat_router),
        ("admin_queue_guard", admin_queue_guard_router),
        ("admin", admin_router),
        ("payment_safety_guard", payment_safety_guard_router),
        ("payment_webhooks", payment_router),
        ("self_filing_product", self_filing_product_router),
        ("contract_center", contract_center_router),
        ("lawyer_product", lawyer_product_router),
        ("lawyer", lawyer_router),
        ("lawyer_m1_claim", lawyer_m1_claim_router),
        ("lawyer_m1_enforcement", lawyer_m1_enforcement_router),
        ("document_access_product", document_access_product_router),
        ("document_access_portal", document_access_portal_router),
        ("runtime", runtime_router),
        ("assignment_queue_product", assignment_queue_product_router),
        ("workdesk_integrity_product", workdesk_integrity_product_router),
        ("workdesk_timeline", workdesk_timeline_router),
        ("workdesk_product", workdesk_product_router),
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

        from app.security.malware_scanning import malware_scanner_readiness

        key_status = security_key_status()
        backup_freshness = await asyncio.to_thread(backup_freshness_status)
        malware_scanner = await malware_scanner_readiness()
        provider = payment_provider_name()
        payment_disabled = payments_disabled()
        payment_offline = payments_offline()
        payment_webhook_secret_ready = (
            payment_offline
            or payment_disabled
            or settings.app_env != "production"
            or (
                provider != "yookassa"
            )
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

        runtime_role = str(settings.runtime_role or "all").strip().lower()
        role_valid = runtime_role in {"all", "web", "bot"}
        bot_expected = runtime_role in {"all", "bot"}
        scheduler_expected = runtime_role in {"all", "bot"}

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
            "document_malware_scanner_ready": bool(
                malware_scanner.get("available")
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
            "payment_provider_configured": payment_mode_valid(),
            "trusted_proxy_config_valid": trusted_proxy_config_valid,
            "trusted_proxy_hop_limit_valid": (
                1 <= int(settings.trusted_proxy_max_hops) <= 20
            ),
            "database_url_configured": bool(settings.database_url),
            "runtime_role_valid": role_valid,
            "scheduler_mode_matches_runtime_role": (
                bool(settings.run_scheduler) == scheduler_expected if role_valid else False
            ),
            "bot_mode_matches_runtime_role": (
                bool(settings.run_bot) == bot_expected if role_valid else False
            ),
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
                "offline_manual_confirmation": payments_offline(),
                "disabled_by_configuration": payment_disabled,
                "payment_links_created": payments_enabled(),
                "pilot_flows_continue_without_payment": payment_disabled,
            },
            "runtime": {
                "role": runtime_role,
                "run_bot": bool(settings.run_bot),
                "run_scheduler": bool(settings.run_scheduler),
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
                "malware_scanner": malware_scanner,
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
                "enabled": provider == "yookassa",
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

    return app


app = create_app()


async def main():
    """Compatibility entrypoint delegated to the canonical supervisor."""
    from app.process import main as run_supervised_process

    return await run_supervised_process()


if __name__ == "__main__":
    asyncio.run(main())
