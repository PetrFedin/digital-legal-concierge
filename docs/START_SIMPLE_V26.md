# Быстрый запуск v27

## Локально

```bash
cp .env.example .env
./run.sh
```

Открыть:

- http://localhost:8000/production-center/ui
- http://localhost:8000/operator
- http://localhost:8000/admin-ui

## Production через Docker

```bash
cp .env.production.example .env
nano .env
./acceptance.sh
docker compose -f docker-compose.production.yml up -d
```

## Главная проверка

```bash
python scripts/production_acceptance.py
```
