#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ICI Platform — Bootstrap Script
#
# Usage:
#   scripts/bootstrap.sh          → development stack
#   scripts/bootstrap.sh --prod   → production stack (pre-built images)
#
# What it does (in order):
#   1. Validate prerequisites (Docker, compose plugin, .env)
#   2. Generate a .env from .env.example if missing (dev keys auto-generated)
#   3. Build Docker images
#   4. Start infrastructure services (postgres, redis, qdrant)
#   5. Wait for each to be healthy
#   6. Run database migrations (Alembic)
#   7. Load PostgreSQL seed data
#   8. Start application services (backend, worker, ml-inference, rfq-agent)
#   9. Wait for app services to be healthy
#  10. Seed vector embeddings into Qdrant
#  11. Start observability stack (prometheus, grafana) + nginx
#  12. Print service URLs
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Colours ───────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

log()  { echo -e "${CYAN}[bootstrap]${RESET} $*"; }
ok()   { echo -e "${GREEN}[bootstrap]${RESET} ✓ $*"; }
warn() { echo -e "${YELLOW}[bootstrap]${RESET} ⚠ $*"; }
die()  { echo -e "${RED}[bootstrap]${RESET} ✗ $*" >&2; exit 1; }

PROD_MODE=false
[[ "${1:-}" == "--prod" ]] && PROD_MODE=true

DC_BASE="docker compose"
if $PROD_MODE; then
    DC="$DC_BASE -f docker-compose.yml -f docker-compose.prod.yml"
else
    DC="$DC_BASE -f docker-compose.yml -f docker-compose.dev.yml"
fi

# ── Step 1: Prerequisites ─────────────────────────────────────────────────────
log "Checking prerequisites..."

command -v docker >/dev/null 2>&1 || die "Docker not found. Install from https://docs.docker.com/get-docker/"
docker compose version >/dev/null 2>&1 || die "Docker Compose plugin not found. Update Docker Desktop or install plugin."
docker info >/dev/null 2>&1 || die "Docker daemon not running. Start Docker and retry."

DOCKER_VERSION=$(docker --version | grep -oP '\d+\.\d+' | head -1)
ok "Docker ${DOCKER_VERSION} ready"

# ── Step 2: Environment file ──────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    log "No .env found — generating development defaults from .env.example..."
    cp .env.example .env

    # Auto-generate secrets for dev (not for prod)
    if ! $PROD_MODE; then
        # Generate SECRET_KEY
        if command -v python3 >/dev/null 2>&1; then
            SECRET=$(python3 -c "import secrets; print(secrets.token_hex(64))")
            sed -i "s|SECRET_KEY=.*|SECRET_KEY=${SECRET}|" .env

            # Generate ENCRYPTION_KEYS
            ENC_KEY=$(python3 -c "
from cryptography.fernet import Fernet
key = Fernet.generate_key().decode()
print(f'dev-key-1:{key}')
" 2>/dev/null || echo "dev-key-1:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")
            sed -i "s|ENCRYPTION_KEYS=.*|ENCRYPTION_KEYS=${ENC_KEY}|" .env
        fi
        warn ".env created with development defaults. Add ANTHROPIC_API_KEY for RFQ agent."
    else
        die "Production mode requires a fully configured .env. Copy .env.example, fill all values, then retry."
    fi
else
    ok ".env found"
fi

# Check for ANTHROPIC_API_KEY
ANTHROPIC_KEY=$(grep "^ANTHROPIC_API_KEY=" .env | cut -d= -f2 | tr -d '"' | tr -d "'")
if [[ -z "$ANTHROPIC_KEY" || "$ANTHROPIC_KEY" == "sk-ant-"* && ${#ANTHROPIC_KEY} -lt 20 ]]; then
    warn "ANTHROPIC_API_KEY not set — RFQ agent will start in degraded mode"
fi

# ── Step 3: Build images ──────────────────────────────────────────────────────
if ! $PROD_MODE; then
    log "Building Docker images (this takes 3-5 min on first run)..."
    $DC build --parallel 2>&1 | grep -E "(Step|Successfully built|error|Error)" || true
    ok "Images built"
fi

# ── Step 4 & 5: Infrastructure services ──────────────────────────────────────
log "Starting infrastructure services..."
$DC up -d postgres redis qdrant

_wait_healthy() {
    local service="$1"
    local max_wait="${2:-120}"
    local elapsed=0
    local interval=3

    log "Waiting for ${service} to be healthy..."
    while true; do
        STATUS=$(docker inspect --format='{{.State.Health.Status}}' "ici_${service}" 2>/dev/null || echo "missing")
        if [[ "$STATUS" == "healthy" ]]; then
            ok "${service} is healthy"
            return 0
        fi
        if [[ "$STATUS" == "unhealthy" ]]; then
            die "${service} is unhealthy. Check: docker logs ici_${service}"
        fi
        elapsed=$((elapsed + interval))
        if [[ $elapsed -ge $max_wait ]]; then
            die "${service} did not become healthy within ${max_wait}s. Check: docker logs ici_${service}"
        fi
        sleep $interval
        echo -n "."
    done
}

_wait_healthy postgres 120
_wait_healthy redis 60
_wait_healthy qdrant 90

# ── Step 6: Migrations ────────────────────────────────────────────────────────
log "Running database migrations..."
# Start the backend container just to run migrations, then stop it
$DC run --rm --no-deps \
    -e RUN_MODE=migrate \
    backend \
    alembic upgrade head 2>&1 | tail -20
ok "Migrations complete"

# ── Step 7: Seed data ─────────────────────────────────────────────────────────
log "Loading seed data..."
$DC exec -T postgres \
    psql -U ici -d ici \
    -v ON_ERROR_STOP=0 \
    -f /dev/stdin < database/seeds/007_seed_data.sql 2>&1 \
    | grep -E "(INSERT|ERROR|NOTICE)" | head -20 || true
ok "Seed data loaded (demo tenant + 10 suppliers + sample cost records)"

# ── Step 8 & 9: Application services ─────────────────────────────────────────
log "Starting application services..."
$DC up -d mlflow ml-inference rfq-agent backend worker

_wait_http() {
    local name="$1"
    local url="$2"
    local max_wait="${3:-120}"
    local elapsed=0
    local interval=3

    log "Waiting for ${name} HTTP health (${url})..."
    while true; do
        if curl -sf --max-time 3 "$url" >/dev/null 2>&1; then
            ok "${name} responded"
            return 0
        fi
        elapsed=$((elapsed + interval))
        if [[ $elapsed -ge $max_wait ]]; then
            warn "${name} not responding after ${max_wait}s — continuing anyway"
            return 0
        fi
        sleep $interval
        echo -n "."
    done
}

_wait_http "backend"      "http://localhost:8000/health"  120
_wait_http "ml-inference" "http://localhost:8002/health"  120
_wait_http "rfq-agent"    "http://localhost:8001/health"   90
_wait_http "mlflow"       "http://localhost:5000/health"   60

# ── Step 10: Vector seed ──────────────────────────────────────────────────────
log "Seeding vector embeddings into Qdrant..."
$DC exec -T backend \
    python -m scripts.seed_vectors \
    2>&1 | tail -5 || warn "Vector seed failed — run manually: make seed-vectors"
ok "Vector embeddings seeded"

# ── Step 11: Observability + nginx ────────────────────────────────────────────
log "Starting observability stack and nginx..."
$DC up -d prometheus grafana nginx

_wait_http "nginx"      "http://localhost/health"       60
_wait_http "prometheus" "http://localhost:9090/-/ready"  60
_wait_http "grafana"    "http://localhost:3000/api/health" 60

# ── Step 12: Summary ──────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}══════════════════════════════════════════════════${RESET}"
echo -e "${GREEN}${BOLD}  ICI Platform is running!${RESET}"
echo -e "${BOLD}══════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${CYAN}API${RESET}          http://localhost/api/v1/"
echo -e "  ${CYAN}API Docs${RESET}     http://localhost:8000/docs"
echo -e "  ${CYAN}ML Service${RESET}   http://localhost:8002/docs"
echo -e "  ${CYAN}RFQ Agent${RESET}    http://localhost:8001/docs"
echo -e "  ${CYAN}MLflow${RESET}       http://localhost:5000"
echo -e "  ${CYAN}Grafana${RESET}      http://localhost:3000  (admin / ici_grafana_dev)"
echo -e "  ${CYAN}Prometheus${RESET}   http://localhost:9090"
echo -e "  ${CYAN}Mailhog${RESET}      http://localhost:8025  (RFQ email preview)"
echo -e "  ${CYAN}pgAdmin${RESET}      http://localhost:5050  (admin@ici.internal)"
echo ""
echo -e "  ${CYAN}Demo login${RESET}   POST http://localhost:8000/api/v1/auth/login"
echo -e "             { \"email\": \"demo@ici.example.com\", \"password\": \"Demo1234!\" }"
echo ""
echo -e "  Run ${BOLD}make test${RESET} to verify the full integration."
echo -e "  Run ${BOLD}make logs${RESET} to tail all logs."
echo -e "  Run ${BOLD}make down${RESET} to stop."
echo ""
