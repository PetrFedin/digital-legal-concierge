from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def get(url: str) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            body = r.read().decode("utf-8")
            return True, body
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    base = "http://localhost:8000"
    checks = [
        ("health", f"{base}/health"),
        ("ready", f"{base}/ready"),
    ]
    ok_all = True
    print("Проверка работающего сервиса")
    for name, url in checks:
        ok, body = get(url)
        print(f"[{ 'OK' if ok else 'FAIL' }] {name}: {body[:300]}")
        ok_all = ok_all and ok
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())
