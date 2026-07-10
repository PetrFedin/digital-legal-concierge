from pathlib import Path
import py_compile

ROOT = Path(__file__).resolve().parents[1]
required = [
    'app/main.py',
    'app/api/go_live_center.py',
    'docs/START_HERE_FINAL_V27.md',
    'docs/WHAT_IS_READY_V27.md',
    'run.sh',
    'bot-control.sh',
    'acceptance.sh',
]
missing = [p for p in required if not (ROOT / p).exists()]
if missing:
    raise SystemExit('Missing files: ' + ', '.join(missing))
for py in (ROOT / 'app').rglob('*.py'):
    py_compile.compile(str(py), doraise=True)
print('full_check_v27 OK')
