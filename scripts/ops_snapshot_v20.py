from pathlib import Path
import json
from datetime import datetime

snapshot = {
    'version': 'v20',
    'generated_at': datetime.now().isoformat(timespec='seconds'),
    'pages': {
        'health_center': '/health-center/ui',
        'diagnostic_center': '/diagnostic-center/ui',
        'recovery_center': '/recovery-center/ui',
        'install_wizard': '/install-wizard/ui',
        'operator': '/operator',
        'admin': '/admin-ui',
        'launch_assistant': '/launch-assistant',
    },
    'operator_commands': ['./run.sh', './bot-control.sh', './backup.sh', './status.sh', './logs.sh'],
    'priority': 'Простой запуск, проверка готовности, диагностика, восстановление, demo mode.'
}
Path('launch_readiness_v20.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
print('launch_readiness_v20.json created')
