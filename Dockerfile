FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY orchestrator ./orchestrator
COPY steps ./steps
COPY gateway ./gateway
COPY api ./api
COPY workflows ./workflows
COPY config ./config
COPY ui ./ui
COPY temporal ./temporal
COPY deploy ./deploy
COPY scripts ./scripts

EXPOSE 8000 8080
