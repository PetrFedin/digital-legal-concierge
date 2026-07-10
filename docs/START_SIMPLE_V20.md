# Простой запуск v20

## 1. Запуск одной командой

```bash
unzip digital-legal-concierge-telegram-bot_v20_ops_center.zip
cd tg_ready_v20
./run.sh
```

## 2. Что открыть после запуска

- Health Center: `http://localhost:8000/health-center/ui`
- Install Wizard: `http://localhost:8000/install-wizard/ui`
- Diagnostic Center: `http://localhost:8000/diagnostic-center/ui`
- Recovery Center: `http://localhost:8000/recovery-center/ui`
- Оператор: `http://localhost:8000/operator`
- Админка: `http://localhost:8000/admin-ui`

## 3. Управление без запоминания команд

```bash
./bot-control.sh
```

## 4. Demo Mode

Для демонстрации без реальных платежей и без реального боевого Telegram можно оставить:

```env
PAYMENT_PROVIDER=fake
DEMO_MODE=true
RUN_BOT=false
```

Для боевого запуска:

```env
RUN_BOT=true
BOT_TOKEN=ваш_токен
PUBLIC_BASE_URL=https://ваш-домен
```
