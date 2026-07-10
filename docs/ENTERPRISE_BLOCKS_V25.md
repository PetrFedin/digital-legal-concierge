# Enterprise blocks v25

## Цель
Собрать поверх Telegram-бота единый слой эксплуатации: настройка, интеграции, мониторинг, backup, release и acceptance.

## Принцип
Администратор не должен лезть в код для обычных операций: суммы, тексты, интеграции, проверки и запуск должны быть доступны через интерфейсы и скрипты.

## Центры

- Initial Setup Wizard: первичная настройка компании, тарифов, рабочих часов.
- Template Builder: редактируемые тексты Telegram, юридические тексты и уведомления.
- Calculator Builder: параметры формулы, ставки, лимиты, округление.
- Integration Center: Telegram, платежи, storage, database, scheduler, search.
- Operations Center: очереди дел, документов, оплат, консультаций, сообщений и сроков.
- Monitoring Center: приложение, БД, диск, scheduler, Telegram, ошибки.
- Backup Manager: backup/restore, политики хранения.
- Release Manager: backup → migrations → compile → tests → restart → health.
- Acceptance Center: единая приемка продукта.
