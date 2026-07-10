from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
required = [
    'app/main.py',
    'app/api/notification_center.py',
    'app/api/backup_center.py',
    'app/api/operator.py',
    'docs/START_SIMPLE_V23.md',
    'docs/WHAT_IS_READY_V23.md',
    'docs/OPERATOR_WORKFLOW_V23.md',
    'run.sh',
]
missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit('Missing files: ' + ', '.join(missing))
for p in (ROOT / 'app').rglob('*.py'):
    py_compile.compile(str(p), doraise=True)
print('full_check_v23 OK')
