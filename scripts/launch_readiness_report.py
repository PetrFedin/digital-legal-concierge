#!/usr/bin/env python3
from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env'

def read_env():
    data = {}
    if ENV.exists():
        for line in ENV.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                data[k.strip()] = v.strip()
    return data

def is_set(value, bad):
    return bool(value) and value not in bad

def main():
    env = read_env()
    checks = {
        'env_file_exists': ENV.exists(),
        'bot_token_configured': is_set(env.get('BOT_TOKEN'), {'CHANGE_ME', '', None}),
        'admin_token_changed': is_set(env.get('ADMIN_API_TOKEN'), {'dev-admin-token', '', None}),
        'database_configured': bool(env.get('DATABASE_URL', 'sqlite+aiosqlite:///./legal_bot.db')),
        'storage_dir_exists': (ROOT / env.get('STORAGE_DIR', './storage')).exists(),
        'run_bot_selected': env.get('RUN_BOT', 'false').lower() in {'true', '1', 'yes'},
        'run_scheduler_selected': env.get('RUN_SCHEDULER', 'false').lower() in {'true', '1', 'yes'},
        'payment_provider_selected': env.get('PAYMENT_PROVIDER', 'fake') in {'fake', 'yookassa'},
        'handover_page_present': (ROOT / 'app/api/handover.py').exists(),
        'admin_ui_present': (ROOT / 'app/api/web_admin.py').exists(),
        'scenario_map_present': (ROOT / 'app/api/scenario_map.py').exists(),
        'backup_script_present': (ROOT / 'backup.sh').exists(),
    }
    report = {
        'project': 'Digital Legal Concierge Telegram Bot',
        'version': 'v19',
        'ok_for_local_demo': checks['env_file_exists'] and checks['database_configured'] and checks['storage_dir_exists'],
        'ok_for_real_bot': checks['bot_token_configured'] and checks['admin_token_changed'] and checks['database_configured'],
        'checks': checks,
        'next_actions': [],
    }
    if not checks['bot_token_configured']:
        report['next_actions'].append('Указать BOT_TOKEN в .env')
    if not checks['admin_token_changed']:
        report['next_actions'].append('Заменить ADMIN_API_TOKEN на длинный секрет')
    if env.get('PAYMENT_PROVIDER') == 'yookassa' and not (env.get('YOOKASSA_SHOP_ID') and env.get('YOOKASSA_SECRET_KEY')):
        report['next_actions'].append('Заполнить YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY')
    if not report['next_actions']:
        report['next_actions'].append('Запустить ./run.sh и открыть /handover')

    out_json = ROOT / 'launch_readiness_v19.json'
    out_md = ROOT / 'docs' / 'LAUNCH_READINESS_REPORT_V19.md'
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# Launch Readiness Report v19', '', f"Local demo: {'OK' if report['ok_for_local_demo'] else 'NEED SETUP'}", f"Real bot: {'OK' if report['ok_for_real_bot'] else 'NEED SETUP'}", '', '## Checks']
    for k, v in checks.items():
        lines.append(f"- {'✅' if v else '❌'} {k}")
    lines += ['', '## Next actions']
    for a in report['next_actions']:
        lines.append(f'- {a}')
    out_md.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
