import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
files = []
for path in sorted(ROOT.rglob('*')):
    if path.is_file() and '.egg-info' not in path.parts and '__pycache__' not in path.parts and path.name != 'legal_bot.db':
        files.append(str(path.relative_to(ROOT)))
print(json.dumps({"files": files, "count": len(files)}, ensure_ascii=False, indent=2))
