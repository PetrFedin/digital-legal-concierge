from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
required = [
    'app/main.py',
    'app/api/maintenance_center.py',
    'run.sh',
    'bot-control.sh',
    'acceptance.sh',
    'docs/START_SIMPLE_V30.md',
    'docs/WHAT_IS_READY_V30.md',
    'docs/NEXT_PRIORITIES_V30.md',
]
missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit('Missing required files: ' + ', '.join(missing))
for py in (ROOT / 'app').rglob('*.py'):
    py_compile.compile(str(py), doraise=True)
print('full_check_v30 OK')
