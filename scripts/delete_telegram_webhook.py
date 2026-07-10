from __future__ import annotations
import json, os, urllib.parse, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

def load_env():
    p = ROOT / '.env'
    if p.exists():
        for line in p.read_text(encoding='utf-8').splitlines():
            if '=' in line and not line.strip().startswith('#'):
                k,v=line.split('=',1); os.environ.setdefault(k.strip(), v.strip())

def main():
    load_env(); token=os.environ.get('BOT_TOKEN')
    if not token or token == 'CHANGE_ME': raise SystemExit('BOT_TOKEN не заполнен')
    url=f'https://api.telegram.org/bot{token}/deleteWebhook'
    data=urllib.parse.urlencode({'drop_pending_updates':'false'}).encode()
    with urllib.request.urlopen(url,data=data,timeout=20) as r: print(json.dumps(json.loads(r.read().decode()), ensure_ascii=False, indent=2))
if __name__=='__main__': main()
