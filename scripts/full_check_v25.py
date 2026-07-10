#!/usr/bin/env python3
import compileall
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
required = [
    'app/api/initial_setup_wizard.py',
    'app/api/template_builder.py',
    'app/api/calculator_builder.py',
    'app/api/integration_center.py',
    'app/api/operations_center.py',
    'app/api/monitoring_center.py',
    'app/api/backup_manager.py',
    'app/api/release_manager.py',
    'app/api/acceptance_center.py',
    'scripts/e2e_smoke.py',
    'scripts/e2e_m1_m2_full.py',
    'backup.sh',
    'restore.sh',
]
missing = [p for p in required if not (ROOT / p).exists()]
compile_ok = compileall.compile_dir(str(ROOT / 'app'), quiet=1)
result = {
    'version': 'v25',
    'compile_ok': bool(compile_ok),
    'missing': missing,
    'enterprise_centers': {
        'initial_setup_wizard': '/initial-setup-wizard/ui',
        'template_builder': '/template-builder/ui',
        'calculator_builder': '/calculator-builder/ui',
        'integration_center': '/integration-center/ui',
        'operations_center': '/operations-center/ui',
        'monitoring_center': '/monitoring-center/ui',
        'backup_manager': '/backup-manager/ui',
        'release_manager': '/release-manager/ui',
        'acceptance_center': '/acceptance-center/ui',
    },
    'ok': bool(compile_ok) and not missing,
}
print(json.dumps(result, ensure_ascii=False, indent=2))
raise SystemExit(0 if result['ok'] else 1)
