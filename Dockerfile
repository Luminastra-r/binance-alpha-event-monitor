# Binance Alpha Event Monitor — Docker image
# Zero third-party Python deps; runs on the standard library only.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
COPY app ./app

# SQLite data lives in /app/data (mounted as a volume in docker-compose)
RUN mkdir -p /app/data

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

VOLUME ["/app/data"]

# Healthcheck: `health` exits non-zero only when an ACTIVE source is stale
# (interval-aware per-source verdict; an intentionally BLOCKED channel such
# as Binance Square does not fail the container).
HEALTHCHECK --interval=60s --timeout=15s --start-period=30s --retries=3 \
    CMD ["python", "-m", "app", "health"]

CMD ["python", "-m", "app", "run"]
