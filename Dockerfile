FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_CONSTRAINT=/app/constraints.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml constraints.txt README.md alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY content ./content
COPY docs ./docs
COPY .env.example ./
COPY docker-entrypoint.sh /usr/local/bin/dlc-entrypoint

RUN pip install --upgrade pip \
    && pip install . \
    && chmod 0755 /usr/local/bin/dlc-entrypoint \
    && mkdir -p /app/data /app/storage /app/logs /app/backups

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=7s --start-period=45s --retries=5 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)" || exit 1

ENTRYPOINT ["dlc-entrypoint"]
CMD ["python", "-m", "app.process"]
