FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY content ./content
COPY docs ./docs
COPY .env.example ./

RUN pip install --upgrade pip \
    && pip install -e .

RUN mkdir -p /app/data /app/storage /app/logs /app/backups

EXPOSE 8000

CMD ["sh", "-c", "python scripts/init_db.py && exec python -m app.main"]
