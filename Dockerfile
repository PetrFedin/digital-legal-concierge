FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
COPY pyproject.toml README.md ./
COPY app ./app
COPY scripts ./scripts
COPY content ./content
COPY docs ./docs
COPY .env.example ./
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .
RUN mkdir -p /app/storage /app/logs
EXPOSE 8000
CMD ["sh", "-c", "python scripts/init_db.py && python -m app.main"]
