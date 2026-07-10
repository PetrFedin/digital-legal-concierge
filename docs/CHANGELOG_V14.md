# Changelog v14

## Главное

v14 делает пакет ближе к реальному запуску Telegram-бота, а не к демо.

## Добавлено

- Опциональный платежный провайдер YooKassa.
- Fake-платежи оставлены для локального тестирования.
- Webhook `/webhooks/payments/yookassa`.
- Страница результата платежа `/payment-result`.
- Production-мастер `scripts/production_wizard.py`.
- Быстрая инструкция `docs/START_SIMPLE_V14.md`.
- Операторский процесс `docs/OPERATOR_WORKFLOW_V14.md`.
- Описание готовности `docs/WHAT_IS_READY_V14.md`.
- Обновлены версии endpoint и интерфейсов до v14.

## Запуск

```bash
./run.sh
```

Или через меню:

```bash
./bot-control.sh
```

## Проверка

```bash
python scripts/full_check_v14.py
```
