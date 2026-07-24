# syntax=docker/dockerfile:1
# Phantom StockForge — Zeabur / Docker ready.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Node is needed only if you launch via the Bankr CLI backend (BANKR_BACKEND=cli).
# Kept optional and lean; comment out if you only use the REST backend.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @bankr/cli \
    && apt-get purge -y gnupg \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY pyproject.toml README.md ./
COPY src/ ./src/
COPY prompts/ ./prompts/
RUN pip install --no-deps -e .

# Persist SQLite state on a mounted volume in production.
ENV STOCKFORGE_DB_PATH=/data/stockforge.sqlite
VOLUME ["/data"]

# Long-running autonomous loop.
CMD ["python", "-m", "stockforge.cli", "run"]
