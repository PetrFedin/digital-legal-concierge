import compileall
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
checks = []

checks.append(('compile_app', compileall.compile_dir(str(ROOT / 'app'), quiet=1)))

required = [
    ROOT / 'run.sh',
    ROOT / 'bot-control.sh',
    ROOT / 'docs/START_SIMPLE_V24.md',
    ROOT / 'docs/WHAT_IS_READY_V24.md',
    ROOT / 'docs/OPERATOR_WORKFLOW_V24.md',
    ROOT / 'app/api/search_center.py',
]
for item in required:
    checks.append((f'exists:{item.relative_to(ROOT)}', item.exists()))

main_text = (ROOT / 'app/main.py').read_text(encoding='utf-8')
checks.append(('main_includes_search_center', 'search_center_router' in main_text))
checks.append(('main_version_v24', '1.0.0-v24' in main_text))

operator_text = (ROOT / 'app/api/operator.py').read_text(encoding='utf-8')
checks.append(('operator_links_search_center', '/search-center/ui' in operator_text))

failed = [name for name, ok in checks if not ok]
for name, ok in checks:
    print(('OK   ' if ok else 'FAIL ') + name)

if failed:
    print('FAILED:', ', '.join(failed))
    sys.exit(1)
print('full_check_v24 OK')
