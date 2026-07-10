#!/usr/bin/env python3
from app.api.security import build_security_checks

if __name__ == "__main__":
    result = build_security_checks()
    print("Security check v19")
    for key, value in result["checks"].items():
        print(("OK  " if value else "WARN"), key)
    for warning in result["warnings"]:
        print("NOTE", warning)
    raise SystemExit(0 if result["ok"] else 1)
