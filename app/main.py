import asyncio
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from app.config import settings
from app.api.admin import router as admin_router
from app.api.payment_webhooks import router as payment_router
from app.api.lawyer import router as lawyer_router
from app.api.runtime import router as runtime_router
from app.api.web_admin import router as web_admin_router
from app.api.operator import router as operator_router
from app.api.exports import router as exports_router
from app.api.scenario_map import router as scenario_map_router
from app.api.ops_guide import router as ops_guide_router
from app.api.handover import router as handover_router
from app.api.auth import router as auth_router
from app.api.access_management import router as access_management_router
from app.api.consultation_slots import router as consultation_slots_router
from app.api.case_assignment import router as case_assignment_router
from app.api.refund_center import router as refund_center_router
from app.api.payment_review_center import router as payment_review_center_router
from app.api.security import router as security_router
from app.api.launch_assistant import router as launch_assistant_router
from app.api.health_center import router as health_center_router
from app.api.diagnostic_center import router as diagnostic_center_router
from app.api.recovery_center import router as recovery_center_router
from app.api.install_wizard import router as install_wizard_router
from app.api.task_center import router as task_center_router
from app.api.settings_ui import router as settings_ui_router
from app.api.message_center import router as message_center_router
from app.api.audit_center import router as audit_center_router
from app.api.notification_center import router as notification_center_router
from app.api.backup_center import router as backup_center_router
from app.api.search_center import router as search_center_router
from app.api.initial_setup_wizard import router as initial_setup_wizard_router
from app.api.template_builder import router as template_builder_router
from app.api.calculator_builder import router as calculator_builder_router
from app.api.integration_center import router as integration_center_router
from app.api.operations_center import router as operations_center_router
from app.api.monitoring_center import router as monitoring_center_router
from app.api.backup_manager import router as backup_manager_router
from app.api.release_manager import router as release_manager_router
from app.api.acceptance_center import router as acceptance_center_router
from app.api.production_center import router as production_center_router
from app.api.go_live_center import router as go_live_center_router
from app.api.final_qa_center import router as final_qa_center_router
from app.api.final_handover_center import router as final_handover_center_router
from app.api.maintenance_center import router as maintenance_center_router


def create_app():
    app = FastAPI(title='Digital Legal Concierge Bot', version='1.0.0-v31')
    for router in [
        maintenance_center_router, final_handover_center_router, final_qa_center_router,
        go_live_center_router, production_center_router, initial_setup_wizard_router,
        template_builder_router, calculator_builder_router, integration_center_router,
        operations_center_router, monitoring_center_router, backup_manager_router,
        release_manager_router, acceptance_center_router, search_center_router,
        message_center_router, audit_center_router, notification_center_router,
        backup_center_router, auth_router, access_management_router,
        consultation_slots_router, case_assignment_router, refund_center_router,
        payment_review_center_router, security_router, launch_assistant_router,
        health_center_router, diagnostic_center_router, recovery_center_router,
        install_wizard_router, task_center_router, settings_ui_router, admin_router,
        payment_router, lawyer_router, runtime_router, web_admin_router,
        operator_router, exports_router, scenario_map_router, ops_guide_router,
        handover_router,
    ]:
        app.include_router(router)

    @app.get('/')
    async def root():
        return RedirectResponse(url='/maintenance-center/ui')

    @app.get('/health')
    async def health():
        return {'ok': True, 'env': settings.app_env, 'version': '1.0.0-v31'}

    @app.get('/ready')
    async def ready():
        from pathlib import Path
        checks = {
            'bot_token_configured': bool(settings.bot_token and settings.bot_token != 'CHANGE_ME') or not settings.run_bot,
            'admin_token_configured': bool(settings.admin_api_token and settings.admin_api_token != 'dev-admin-token'),
            'storage_dir_exists': Path(settings.storage_dir).exists(),
            'database_url_configured': bool(settings.database_url),
            'scheduler_enabled': settings.run_scheduler,
            'bot_enabled': settings.run_bot,
            'payment_provider_configured': settings.payment_provider == 'fake' or bool(settings.yookassa_shop_id and settings.yookassa_secret_key),
        }
        return {'ok': all(checks.values()), 'checks': checks, 'version': '1.0.0-v31'}

    @app.get('/launch-check')
    async def launch_check():
        return {
            'version': '1.0.0-v31',
            'handover': '/handover',
            'security_check': '/security-check',
            'launch_assistant': '/launch-assistant',
            'admin_ui': '/admin-ui',
            'access_management': '/access/ui',
            'consultation_slots_api': '/consultation-slots',
            'consultation_slots_ui': '/consultation-slots/ui',
            'case_assignment_api': '/admin/case-assignment',
            'refund_center_api': '/admin/refunds',
            'refund_center_ui': '/admin/refunds/ui',
            'payment_review_center_api': '/admin/payment-reviews',
            'payment_review_center_ui': '/admin/payment-reviews/ui',
            'health': '/health',
            'ready': '/ready',
            'bot_enabled': settings.run_bot,
            'scheduler_enabled': settings.run_scheduler,
            'payment_provider': settings.payment_provider,
            'storage_dir': settings.storage_dir,
        }

    return app


app = create_app()


async def main():
    if settings.run_bot:
        from app.bot.bot import run_bot
        asyncio.create_task(run_bot())
    if settings.run_scheduler:
        from app.scheduler.scheduler import AppScheduler
        asyncio.create_task(AppScheduler().run_forever())
    config = uvicorn.Config(app, host='0.0.0.0', port=8000, log_level='info')
    server = uvicorn.Server(config)
    await server.serve()


if __name__ == '__main__':
    asyncio.run(main())
