FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml poetry.lock* requirements.txt* ./
RUN pip install --upgrade pip && \
    if [ -f requirements.txt ]; then pip install -r requirements.txt; else pip install .; fi

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

# Не запускаем процесс от root — стандартная практика для контейнеров, к которым
# теоретически может быть применён RCE через уязвимость в зависимости
RUN groupadd --system syncscribe && useradd --system --gid syncscribe --home /srv/app syncscribe \
    && chown -R syncscribe:syncscribe /srv/app
USER syncscribe

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["gunicorn", "app.main:app", "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60"]
