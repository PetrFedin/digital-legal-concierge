import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    'run.sh', 'bot-control.sh', 'acceptance.sh', '.env.example', 'README.md',
    'app/main.py', 'app/bot/bot.py', 'app/api/final_handover_center.py',
    'docs/START_SIMPLE_V29.md', 'docs/WHAT_IS_READY_V29.md', 'docs/HANDOVER_V29.md',
]
missing = [p for p in required if not (ROOT / p).exists()]
report = {
    'version': '1.0.0-v29',
    'status': 'READY' if not missing else 'MISSING_FILES',
    'missing': missing,
    'start_command': './run.sh',
    'control_command': './bot-control.sh',
    'first_url': 'http://localhost:8000/final-handover/ui',
    'operator_url': 'http://localhost:8000/operator',
    'admin_url': 'http://localhost:8000/admin-ui',
}
out = ROOT / 'handover_report_v29.json'
out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(report, ensure_ascii=False, indent=2))
if missing:
    raise SystemExit(1)
