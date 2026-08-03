FROM python:3.11-slim

WORKDIR /app

ARG APP_RELEASE=dev
ARG GIT_COMMIT_SHA=unknown
ARG BUILD_TIMESTAMP=unknown
ARG APP_IMAGE_REPOSITORY=digital-legal-concierge
ARG APP_IMAGE_TAG=dev

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_CONSTRAINT=/app/constraints.txt \
    APP_RELEASE=${APP_RELEASE} \
    GIT_COMMIT_SHA=${GIT_COMMIT_SHA} \
    BUILD_TIMESTAMP=${BUILD_TIMESTAMP} \
    APP_IMAGE_REPOSITORY=${APP_IMAGE_REPOSITORY} \
    APP_IMAGE_TAG=${APP_IMAGE_TAG}

LABEL org.opencontainers.image.title="Digital Legal Concierge" \
      org.opencontainers.image.version="${APP_RELEASE}" \
      org.opencontainers.image.revision="${GIT_COMMIT_SHA}" \
      org.opencontainers.image.created="${BUILD_TIMESTAMP}"

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
