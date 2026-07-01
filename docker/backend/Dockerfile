# ═══════════════════════════════════════════════════════════════════════════════
# ICI Backend — Multi-stage Dockerfile
# Stages:
#   base        → shared Python + OS deps
#   builder     → install Python packages into /app/.venv
#   development → hot-reload, dev tools, editable install
#   production  → minimal image, non-root user, no dev deps
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
    PATH="/app/.venv/bin:$PATH"

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Build tools for C extensions
    gcc \
    g++ \
    libpq-dev \
    # Runtime: curl for healthchecks, ca-certs for HTTPS
    curl \
    ca-certificates \
    # Playwright system dependencies (used by rfq-agent)
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ── Stage 2: builder ──────────────────────────────────────────────────────────
FROM base AS builder

# Install uv for fast dependency resolution
RUN pip install uv==0.2.0

COPY backend/pyproject.toml backend/README.md* ./
# Create venv and install all dependencies
RUN uv venv /app/.venv && \
    uv pip install --python /app/.venv ".[dev]"

# Install agents dependencies (for rfq-agent support)
COPY agents/pyproject.toml /tmp/agents/
RUN cd /tmp/agents && \
    uv pip install --python /app/.venv "." 2>/dev/null || true

# Install Playwright browsers for rfq-agent support
RUN uv pip install --python /app/.venv "playwright>=1.45.0" && \
    /app/.venv/bin/playwright install chromium --with-deps 2>/dev/null || true

# ── Stage 3: development ──────────────────────────────────────────────────────
FROM base AS development

# Copy venv from builder
COPY --from=builder /app/.venv /app/.venv

# Install watchdog for auto-reload
RUN pip install watchdog[watchmedo]

# Source is mounted at runtime (bind mount in docker-compose.dev.yml)
WORKDIR /app

# Dev user = same uid as host developer to avoid permission issues
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID appgroup && \
    useradd -u $UID -g $GID -s /bin/bash -d /app appuser && \
    chown -R appuser:appgroup /app
USER appuser

EXPOSE 8000 8001

# ── Stage 4: production ───────────────────────────────────────────────────────
FROM base AS production

# Copy only the venv (no dev tools)
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /root/.cache/ms-playwright /root/.cache/ms-playwright 2>/dev/null || true

# Copy application source
COPY backend/src       /app/src
COPY backend/alembic   /app/alembic
COPY backend/alembic.ini /app/alembic.ini
COPY agents/rfq_agent  /app/rfq_agent
COPY docker/backend/logging.json /app/logging.json

# Non-root user for security
RUN groupadd -g 1001 appgroup && \
    useradd -u 1001 -g 1001 -s /bin/sh -d /app appuser && \
    chown -R appuser:appgroup /app

USER appuser

# Expose backend + agent ports
EXPOSE 8000 8001

# Default: production backend
CMD ["uvicorn", "src.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "4", \
     "--log-config", "/app/logging.json", \
     "--no-access-log"]

# ── Metadata ──────────────────────────────────────────────────────────────────
LABEL org.opencontainers.image.title="ICI Backend" \
      org.opencontainers.image.description="Industrial Cost Intelligence — FastAPI Backend" \
      org.opencontainers.image.vendor="ICI" \
      org.opencontainers.image.version="1.0.0" \
      org.opencontainers.image.base.name="python:${PYTHON_VERSION}-slim-bookworm"
