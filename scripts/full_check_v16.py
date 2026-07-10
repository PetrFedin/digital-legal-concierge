#!/usr/bin/env python3
from pathlib import Path
import compileall
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

REQUIRED = [
    'app/main.py', 'app/bot/bot.py', 'app/api/admin.py', 'app/api/web_admin.py',
    'app/api/operator.py', 'app/api/scenario_map.py', 'app/api/ops_guide.py',
    'scripts/init_db.py', 'scripts/e2e_smoke.py', 'scripts/e2e_m1_m2_full.py',
    'run.sh', 'bot-control.sh', '.env.example', 'docs/START_SIMPLE_V19.md'
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
        else: success = fail(f'нет файла {rel}') and success

    scenario_file = ROOT / 'app/api/scenario_map.py'
    text = scenario_file.read_text(encoding='utf-8')
    for screen in EXPECTED_SCREENS:
        if screen in text: pass
        else: success = fail(f'нет экрана {screen} в scenario_map') and success
    ok('B-001—B-028 описаны в scenario_map')

    if compileall.compile_dir(str(ROOT / 'app'), quiet=1): ok('compile app')
    else: success = fail('compile app') and success

    for script in ['scripts/init_db.py', 'scripts/e2e_smoke.py', 'scripts/e2e_m1_m2_full.py']:
        if not run([sys.executable, script]): success = False
        else: ok(script)

    print('\nИТОГ:', 'ГОТОВО' if success else 'ЕСТЬ ОШИБКИ')
    return 0 if success else 1

if __name__ == '__main__':
    raise SystemExit(main())
