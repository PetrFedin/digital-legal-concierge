from pathlib import Path

base = Path(__file__).resolve().parents[1]
checks = []

def ok(name, condition):
    checks.append((name, bool(condition)))

ok('README exists', (base / 'README.md').exists())
ok('run.sh exists', (base / 'run.sh').exists())
ok('main exists', (base / 'app' / 'main.py').exists())
ok('message center exists', (base / 'app' / 'api' / 'message_center.py').exists())
ok('audit center exists', (base / 'app' / 'api' / 'audit_center.py').exists())
ok('message center route in main', 'message_center_router' in (base / 'app' / 'main.py').read_text(encoding='utf-8'))
ok('audit center route in main', 'audit_center_router' in (base / 'app' / 'main.py').read_text(encoding='utf-8'))
ok('operator links message center', '/message-center/ui' in (base / 'app' / 'api' / 'operator.py').read_text(encoding='utf-8'))
ok('operator links audit center', '/audit-center/ui' in (base / 'app' / 'api' / 'operator.py').read_text(encoding='utf-8'))
ok('docs start v23 exists', (base / 'docs' / 'START_SIMPLE_V23.md').exists())
ok('docs ready v23 exists', (base / 'docs' / 'WHAT_IS_READY_V23.md').exists())

failed = [name for name, result in checks if not result]
for name, result in checks:
    print(('OK   ' if result else 'FAIL ') + name)
if failed:
    raise SystemExit('full_check_v23 failed: ' + ', '.join(failed))
print('full_check_v23 OK')
