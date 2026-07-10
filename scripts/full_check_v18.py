#!/usr/bin/env python3
from pathlib import Path
import compileall
import subprocess
import sys
import json

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    'app/main.py', 'app/bot/bot.py', 'app/api/admin.py', 'app/api/web_admin.py',
    'app/api/operator.py', 'app/api/scenario_map.py', 'app/api/ops_guide.py', 'app/api/handover.py',
    'scripts/init_db.py', 'scripts/e2e_smoke.py', 'scripts/e2e_m1_m2_full.py',
    'scripts/launch_readiness_report.py', 'scripts/security_check.py', 'run.sh', 'bot-control.sh', '.env.example',
    'docs/START_SIMPLE_V19.md', 'docs/FINAL_HANDOVER_V19.md', 'docs/SECURITY_AND_ACCESS_V19.md'
]

EXPECTED_SCREENS = [f'B-{i:03d}' for i in range(1, 29)]

def ok(msg): print('OK  ', msg)
def fail(msg): print('FAIL', msg); return False

def run(cmd):
    print('$', ' '.join(cmd))
    res = subprocess.run(cmd, cwd=ROOT)
    return res.returncode == 0

def main():
    success = True
    for rel in REQUIRED:
        if (ROOT / rel).exists(): ok(rel)
        else:
            success = False
            fail(f'нет файла {rel}')

    scenario_file = ROOT / 'app/api/scenario_map.py'
    text = scenario_file.read_text(encoding='utf-8')
    missing = [screen for screen in EXPECTED_SCREENS if screen not in text]
    if missing:
        success = False
        fail('нет экранов в scenario_map: ' + ', '.join(missing))
    else:
        ok('B-001—B-028 описаны в scenario_map')

    main_text = (ROOT / 'app/main.py').read_text(encoding='utf-8')
    for endpoint in ['/ready', '/launch-check']:
        if endpoint in main_text: ok(endpoint)
        else:
            success = False
            fail(f'нет endpoint {endpoint}')

    if compileall.compile_dir(str(ROOT / 'app'), quiet=1): ok('compile app')
    else:
        success = False
        fail('compile app')

    for script in ['scripts/init_db.py', 'scripts/e2e_smoke.py', 'scripts/e2e_m1_m2_full.py', 'scripts/launch_readiness_report.py']:
        if not run([sys.executable, script]): success = False
        else: ok(script)

    result = {'ok': success, 'version': 'v19', 'project': 'digital-legal-concierge-telegram-bot'}
    (ROOT / 'launch_readiness_v19.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print('\nИТОГ:', 'ГОТОВО' if success else 'ЕСТЬ ОШИБКИ')
    return 0 if success else 1

if __name__ == '__main__':
    raise SystemExit(main())
