from __future__ import annotations

from pathlib import Path
from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from app.config import settings

router = APIRouter(prefix='/maintenance-center', tags=['maintenance-center'])

CRITICAL_LINKS = [
    ('Запуск / Go-live', '/go-live/ui', 'Финальная проверка перед запуском'),
    ('Production Center', '/production-center/ui', 'Сводная готовность production'),
    ('Final QA', '/final-qa/ui', 'Ручная проверка клиент/админ/юрист'),
    ('Final Handover', '/final-handover/ui', 'Передача проекта оператору'),
    ('Health Center', '/health-center/ui', 'Здоровье сервиса'),
    ('Diagnostic Center', '/diagnostic-center/ui', 'Диагностика окружения'),
    ('Recovery Center', '/recovery-center/ui', 'Восстановление типовых сбоев'),
    ('Install Wizard', '/install-wizard/ui', 'Проверка установки'),
    ('Initial Setup', '/initial-setup-wizard/ui', 'Первичная настройка компании'),
    ('Settings', '/settings-ui', 'Суммы, проценты, сроки'),
    ('Operations Center', '/operations-center/ui', 'Операционные очереди'),
    ('Task Center', '/task-center/ui', 'Задачи'),
    ('Message Center', '/message-center/ui', 'Сообщения клиентов'),
    ('Notification Center', '/notification-center/ui', 'Уведомления'),
    ('Audit Center', '/audit-center/ui', 'Журнал действий'),
    ('Backup Manager', '/backup-manager/ui', 'Резервные копии'),
    ('Search Center', '/search-center/ui', 'Поиск'),
    ('Admin UI', '/admin-ui', 'Админка'),
    ('Scenario Map', '/scenario-map-ui', 'Карта экранов B-001—B-028'),
]


def _check_file(path: str) -> bool:
    return Path(path).exists()


@router.get('/status')
async def maintenance_status():
    storage_dir = Path(settings.storage_dir)
    docs = Path('docs')
    checks = {
        'env_exists': _check_file('.env'),
        'env_example_exists': _check_file('.env.example'),
        'db_configured': bool(settings.database_url),
        'bot_configured_or_disabled': bool(settings.bot_token and settings.bot_token != 'CHANGE_ME') or not settings.run_bot,
        'admin_password_changed': bool(settings.admin_password and settings.admin_password != 'admin'),
        'storage_dir_exists': storage_dir.exists(),
        'docs_exist': docs.exists(),
        'run_script_exists': _check_file('run.sh'),
        'control_script_exists': _check_file('bot-control.sh'),
        'acceptance_script_exists': _check_file('acceptance.sh'),
        'production_compose_exists': _check_file('docker-compose.production.yml'),
    }
    return {
        'ok': all(checks.values()),
        'version': '1.0.0-v30',
        'checks': checks,
        'links': [{'title': t, 'url': u, 'description': d} for t, u, d in CRITICAL_LINKS],
        'operator_commands': ['./run.sh', './bot-control.sh', './acceptance.sh', './backup.sh', './status.sh', './logs.sh'],
    }


@router.get('/ui', response_class=HTMLResponse)
async def maintenance_ui():
    status = await maintenance_status()
    rows = ''.join(
        f"<tr><td>{name}</td><td>{'✅' if value else '⚠️'}</td></tr>"
        for name, value in status['checks'].items()
    )
    links = ''.join(
        f"<a class='card' href='{url}'><b>{title}</b><span>{desc}</span></a>"
        for title, url, desc in CRITICAL_LINKS
    )
    commands = ''.join(f"<code>{cmd}</code>" for cmd in status['operator_commands'])
    return f"""
    <html><head><meta charset='utf-8'><title>Maintenance Center v30</title>
    <style>
    body{{font-family:Arial,sans-serif;background:#f6f6f6;margin:32px;color:#111}}
    h1{{margin-bottom:6px}} .ok{{font-size:20px;margin:14px 0 24px}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:12px;margin:20px 0}}
    .card{{display:block;background:white;border:1px solid #ddd;border-radius:12px;padding:14px;text-decoration:none;color:#111}}
    .card span{{display:block;color:#666;margin-top:6px;font-size:13px}}
    table{{border-collapse:collapse;background:white;width:100%;max-width:760px}}
    td{{border:1px solid #ddd;padding:10px}}
    code{{display:inline-block;background:#111;color:white;padding:8px 10px;border-radius:8px;margin:4px}}
    .note{{background:#fff8d8;border:1px solid #e7d27a;padding:14px;border-radius:12px;max-width:900px}}
    </style></head><body>
    <h1>🛠 Maintenance Center v30</h1>
    <div class='ok'>Общий статус: {'✅ готов к операторской проверке' if status['ok'] else '⚠️ требуется настройка'}</div>
    <div class='note'>Это единая страница обслуживания: запуск, диагностика, настройки, операции, backup, поиск, аудит и финальная приемка. Если оператор потерялся — открывать сюда. Бот не должен превращаться в квест с факелом.</div>
    <h2>Быстрые команды</h2><div>{commands}</div>
    <h2>Проверки</h2><table>{rows}</table>
    <h2>Центры управления</h2><div class='grid'>{links}</div>
    </body></html>
    """
