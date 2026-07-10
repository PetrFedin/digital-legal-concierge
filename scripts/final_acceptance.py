from __future__ import annotations

import compileall
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(title: str, cmd: list[str]) -> bool:
    print(f'\n=== {title} ===')
    result = subprocess.run(cmd, cwd=ROOT, text=True)
    ok = result.returncode == 0
    print('OK' if ok else 'FAIL')
    return ok


def main() -> int:
    print('Финальная приемочная проверка v19')
    ok = True
    print('\n=== compileall ===')
    ok = compileall.compile_dir(str(ROOT / 'app'), quiet=1) and ok
    print('OK' if ok else 'FAIL')

    checks = [
        ('init_db', [sys.executable, 'scripts/init_db.py']),
        ('launch_check', [sys.executable, 'scripts/launch_check.py']),
        ('operator_check', [sys.executable, 'scripts/operator_check.py']),
        ('e2e_smoke', [sys.executable, 'scripts/e2e_smoke.py']),
        ('e2e_m1_m2_full', [sys.executable, 'scripts/e2e_m1_m2_full.py']),
    ]
    for title, cmd in checks:
        ok = run(title, cmd) and ok

    print('\nИтог:', 'ГОТОВО К ТЕСТОВОМУ ЗАПУСКУ' if ok else 'ЕСТЬ ОШИБКИ')
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
