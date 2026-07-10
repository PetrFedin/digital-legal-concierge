from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    path = ROOT / '.env'
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip())


def api(method: str, params: dict):
    token = os.environ.get('BOT_TOKEN')
    if not token or token == 'CHANGE_ME':
        raise SystemExit('BOT_TOKEN не заполнен')
    url = f'https://api.telegram.org/bot{token}/{method}'
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    load_env()
    public_base_url = os.environ.get('PUBLIC_BASE_URL', '').rstrip('/')
    webhook_path = os.environ.get('TELEGRAM_WEBHOOK_PATH', '/telegram/webhook')
    if not public_base_url.startswith('https://'):
        raise SystemExit('Для webhook нужен PUBLIC_BASE_URL с https://')
    result = api('setWebhook', {'url': public_base_url + webhook_path})
    print(json.dumps(result, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
