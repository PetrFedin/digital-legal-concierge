import importlib
import subprocess
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

checks = []

def run(name, cmd):
    print(f"[v20] {name}...")
    result = subprocess.run(cmd, shell=True)
    ok = result.returncode == 0
    checks.append((name, ok))
    if not ok:
        raise SystemExit(f"FAILED: {name}")

for module in [
    'app.api.health_center', 'app.api.diagnostic_center', 'app.api.recovery_center', 'app.api.install_wizard', 'app.main'
]:
    importlib.import_module(module)
    checks.append((module, True))

run('compile', f'{sys.executable} -m compileall app scripts >/dev/null')
if Path('scripts/init_db.py').exists():
    run('init_db', f'{sys.executable} scripts/init_db.py')
if Path('scripts/e2e_smoke.py').exists():
    run('e2e_smoke', f'{sys.executable} scripts/e2e_smoke.py')

print('\nV20 FULL CHECK OK')
for name, ok in checks:
    print(f"{'OK' if ok else 'FAIL'} - {name}")
