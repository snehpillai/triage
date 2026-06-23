# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1: builder
# Install all dependencies into an isolated venv so the runtime image
# only copies the compiled output, not the build tools.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir hatch

WORKDIR /build
COPY pyproject.toml ./

# Install production dependencies into /opt/venv
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir --upgrade pip && \
    /opt/venv/bin/pip install --no-cache-dir \
        langgraph \
        langchain-anthropic \
        langsmith \
        anthropic \
        fastapi \
        "uvicorn[standard]" \
        pydantic \
        pydantic-settings \
        "psycopg[binary]" \
        pgvector \
        sqlalchemy \
        alembic \
        greenlet \
        redis \
        voyageai \
        openai \
        tiktoken \
        "prometheus-client" \
        loguru \
        streamlit \
        pandas

# ---------------------------------------------------------------------------
# Stage 2: runtime
# Lean image with only the venv, app source, and scripts.
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY src/ ./src/
COPY scripts/ ./scripts/
COPY demo/ ./demo/
COPY alembic.ini ./

# src/triage must be importable without installing the package
ENV PYTHONPATH="/app/src"

# Default: run the API. Override with `command:` in docker-compose.
CMD ["uvicorn", "triage.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
