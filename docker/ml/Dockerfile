# ═══════════════════════════════════════════════════════════════════════════════
# ICI ML Inference — Multi-stage Dockerfile
# Stages:
#   base        → Python + OS deps (includes BLAS for LightGBM/XGBoost)
#   builder     → install Python packages into /app/.venv
#   development → hot-reload
#   production  → minimal, non-root
# ═══════════════════════════════════════════════════════════════════════════════

ARG PYTHON_VERSION=3.11

# ── Stage 1: base ─────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim-bookworm AS base

ARG PYTHON_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100 \
    VIRTUAL_ENV=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    # sentence-transformers / HuggingFace cache
    HF_HOME=/app/.cache/huggingface \
    TRANSFORMERS_CACHE=/app/.cache/huggingface \
    # Prevent OpenMP over-subscription
    OMP_NUM_THREADS=4

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    libgomp1 \
    libopenblas-dev \
    liblapack-dev \
    libpq-dev \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 2: builder ──────────────────────────────────────────────────────────
FROM base AS builder

RUN pip install uv==0.2.0

COPY ml/pyproject.toml ml/README.md* ./
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv ".[dev]"

# ── Stage 3: development ──────────────────────────────────────────────────────
FROM base AS development

COPY --from=builder /app/.venv /app/.venv

RUN pip install watchdog[watchmedo]

WORKDIR /app

ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appgroup && \
    useradd -u $UID -g $GID -s /bin/bash -d /app appuser && \
    mkdir -p /app/.cache/huggingface && \
    chown -R appuser:appgroup /app
USER appuser

EXPOSE 8002

# ── Stage 4: production ───────────────────────────────────────────────────────
FROM base AS production

COPY --from=builder /app/.venv /app/.venv

COPY ml/cost_prediction /app/cost_prediction
COPY ml/pyproject.toml  /app/pyproject.toml

RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g 1001 -s /bin/sh -d /app appuser && \
    mkdir -p /app/.cache/huggingface && \
    chown -R appuser:appgroup /app

USER appuser

EXPOSE 8002

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8002/health || exit 1

CMD ["uvicorn", "cost_prediction.inference.api:app", \
     "--host", "0.0.0.0", \
     "--port", "8002", \
     "--workers", "2"]

# ── Metadata ──────────────────────────────────────────────────────────────────
LABEL org.opencontainers.image.title="ICI ML Inference" \
      org.opencontainers.image.description="Industrial Cost Intelligence — ML Inference Service" \
      org.opencontainers.image.vendor="ICI" \
      org.opencontainers.image.version="1.0.0"
