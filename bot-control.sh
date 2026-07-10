#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

show_menu() {
  echo ""
  echo "Digital Legal Concierge Telegram Bot v20"
  echo "1) Первый запуск / настройка .env"
  echo "2) Проверить готовность"
  echo "3) Production-мастер: токен, домен, платежи"
  echo "4) Инициализировать базу"
  echo "5) Запустить сервис"
  echo "6) Полная проверка v20"
  echo "7) Финальная приемка"
  echo "8) Создать резервную копию"
  echo "9) Показать статус"
  echo "10) Запуск: проверка + сервис"
  echo "11) Отчет готовности запуска"
  echo "12) Подсказка по страницам"
  echo "13) Security check
  14) Health Center / Diagnostic / Recovery
  15) Install Wizard status
  16) Ops snapshot v20"
  echo "0) Выход"
  echo ""
}

ensure_venv() {
  if [ ! -d .venv ]; then python3 -m venv .venv; fi
  . .venv/bin/activate
  python -m pip install --upgrade pip >/dev/null
  python -m pip install -e . >/dev/null
}

cmd="${1:-menu}"
if [ "$cmd" = "menu" ]; then
  show_menu
  read -r -p "Выберите действие: " choice
else
  choice="$cmd"
fi

case "$choice" in
  1|setup) ensure_venv; python scripts/first_run.py ;;
  2|check) ensure_venv; python scripts/preflight_v19.py || true
      python scripts/launch_check.py ;;
  3|prod) ensure_venv; python scripts/production_wizard.py ;;
  4|init) ensure_venv; python scripts/init_db.py ;;
  5|start) ./run.sh ;;
  6|full) ensure_venv; python scripts/full_check_v20.py ;;
  7|accept) ensure_venv; python scripts/final_acceptance.py ;;
  8|backup) ./backup.sh ;;
  9|status) ./status.sh ;;
  10|checkrun) ./check-and-run.sh ;;
  11|report) ensure_venv; python scripts/launch_readiness_report.py ;;
  12|pages) echo "После запуска откройте: /health-center/ui, /diagnostic-center/ui, /recovery-center/ui, /install-wizard/ui, /operator, /admin-ui, /ready" ;;
  13|security) ensure_venv; python scripts/security_check.py ;;
  14|health) echo "Откройте: http://localhost:8000/health-center/ui и /diagnostic-center/ui" ;;
  15|wizard) echo "Откройте: http://localhost:8000/install-wizard/ui" ;;
  16|snapshot) ensure_venv; python scripts/ops_snapshot_v20.py ;;
  0|exit) exit 0 ;;
  *) echo "Неизвестная команда: $choice"; exit 1 ;;
esac
