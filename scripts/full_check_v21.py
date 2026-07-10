from pathlib import Path

checks = []

def ok(name, condition):
    checks.append((name, bool(condition)))

base = Path(__file__).resolve().parents[1]
ok('README exists', (base / 'README.md').exists())
ok('run.sh exists', (base / 'run.sh').exists())
ok('app main exists', (base / 'app' / 'main.py').exists())
ok('task center exists', (base / 'app' / 'api' / 'task_center.py').exists())
ok('settings ui exists', (base / 'app' / 'api' / 'settings_ui.py').exists())
ok('docs START v21 exists', (base / 'docs' / 'START_SIMPLE_V21.md').exists())
ok('task center route in main', 'task_center_router' in (base / 'app' / 'main.py').read_text(encoding='utf-8'))
ok('settings ui route in main', 'settings_ui_router' in (base / 'app' / 'main.py').read_text(encoding='utf-8'))

failed = [name for name, result in checks if not result]
for name, result in checks:
    print(('OK   ' if result else 'FAIL ') + name)

if failed:
    raise SystemExit('full_check_v21 failed: ' + ', '.join(failed))
print('full_check_v21 OK')
