# ═══════════════════════════════════════════════════════════════════════════════
# ICI Platform — Makefile
#
# Quick start:
#   make up          → full dev stack (build + start + seed + health-check)
#   make down        → stop everything
#   make logs        → tail all service logs
#   make test        → run e2e integration test scenario
#   make reset       → wipe volumes + rebuild from scratch
#
# Individual targets listed in `make help`.
# ═══════════════════════════════════════════════════════════════════════════════

SHELL := /bin/bash
.DEFAULT_GOAL := help

DC      := docker compose
DC_DEV  := $(DC) -f docker-compose.yml -f docker-compose.dev.yml
DC_PROD := $(DC) -f docker-compose.yml -f docker-compose.prod.yml

# ── Colours ───────────────────────────────────────────────────────────────────
BOLD  := \033[1m
GREEN := \033[32m
CYAN  := \033[36m
RESET := \033[0m

.PHONY: help
help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\n$(BOLD)ICI Platform$(RESET)\n\nUsage:\n  make $(CYAN)<target>$(RESET)\n\nTargets:\n"} \
	  /^[a-zA-Z_-]+:.*?##/ { printf "  $(CYAN)%-22s$(RESET) %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
	@echo ""

# ── Primary entry points ──────────────────────────────────────────────────────

.PHONY: up
up: ## Build, start, migrate, seed — full one-command startup
	@scripts/bootstrap.sh

.PHONY: up-prod
up-prod: ## Start in production mode (pre-built images required)
	@scripts/bootstrap.sh --prod

.PHONY: down
down: ## Stop all containers (preserve volumes)
	$(DC_DEV) down --remove-orphans

.PHONY: reset
reset: ## Wipe all volumes + rebuild from scratch
	@echo -e "$(BOLD)⚠  This will destroy all data. Ctrl-C to abort...$(RESET)"
	@sleep 3
	$(DC_DEV) down --volumes --remove-orphans
	$(DC_DEV) build --no-cache
	@$(MAKE) up

# ── Build ─────────────────────────────────────────────────────────────────────

.PHONY: build
build: ## Build all Docker images (dev targets)
	$(DC_DEV) build

.PHONY: build-prod
build-prod: ## Build production Docker images
	$(DC_PROD) build

.PHONY: build-backend
build-backend: ## Build backend image only
	$(DC_DEV) build backend worker

.PHONY: build-ml
build-ml: ## Build ML inference image only
	$(DC_DEV) build ml-inference

# ── Lifecycle ─────────────────────────────────────────────────────────────────

.PHONY: start
start: ## Start containers (no build, no seed)
	$(DC_DEV) up -d

.PHONY: stop
stop: ## Stop containers without removing them
	$(DC_DEV) stop

.PHONY: restart
restart: ## Restart all containers
	$(DC_DEV) restart

.PHONY: restart-backend
restart-backend: ## Restart backend + worker only
	$(DC_DEV) restart backend worker

.PHONY: ps
ps: ## Show container status
	$(DC_DEV) ps

# ── Database ──────────────────────────────────────────────────────────────────

.PHONY: migrate
migrate: ## Run Alembic migrations inside running backend container
	$(DC_DEV) exec backend alembic upgrade head

.PHONY: migrate-create
migrate-create: ## Create new migration: make migrate-create msg="add foo table"
	$(DC_DEV) exec backend alembic revision --autogenerate -m "$(msg)"

.PHONY: seed
seed: ## Load demo seed data into PostgreSQL
	$(DC_DEV) exec -T postgres psql -U ici -d ici -f /docker-entrypoint-initdb.d/007_seed_data.sql 2>/dev/null || \
	  $(DC_DEV) exec -T postgres psql -U ici -d ici < database/seeds/007_seed_data.sql

.PHONY: seed-vectors
seed-vectors: ## Generate embeddings + load vector data into Qdrant
	$(DC_DEV) exec backend python -m scripts.seed_vectors

.PHONY: db-shell
db-shell: ## Open psql shell
	$(DC_DEV) exec postgres psql -U ici -d ici

.PHONY: db-backup
db-backup: ## Create timestamped PostgreSQL dump
	$(DC_DEV) exec postgres pg_dump -U ici ici | gzip > "backups/ici_$$(date +%Y%m%d_%H%M%S).sql.gz"
	@echo "Backup written to backups/"

# ── Logs ──────────────────────────────────────────────────────────────────────

.PHONY: logs
logs: ## Tail logs from all services
	$(DC_DEV) logs -f --tail=100

.PHONY: logs-backend
logs-backend: ## Tail backend logs
	$(DC_DEV) logs -f --tail=100 backend

.PHONY: logs-worker
logs-worker: ## Tail worker logs
	$(DC_DEV) logs -f --tail=100 worker

.PHONY: logs-ml
logs-ml: ## Tail ML inference logs
	$(DC_DEV) logs -f --tail=100 ml-inference

.PHONY: logs-rfq
logs-rfq: ## Tail RFQ agent logs
	$(DC_DEV) logs -f --tail=100 rfq-agent

# ── Testing ───────────────────────────────────────────────────────────────────

.PHONY: health
health: ## Run health checks against all services
	@scripts/health-check.sh

.PHONY: test
test: ## Run e2e integration test scenario
	@scripts/e2e-test.sh

.PHONY: test-unit
test-unit: ## Run Python unit tests inside backend container
	$(DC_DEV) exec backend pytest tests/unit -v --tb=short

.PHONY: test-integration
test-integration: ## Run integration tests (requires running stack)
	$(DC_DEV) exec backend pytest tests/integration -v --tb=short

.PHONY: lint
lint: ## Run ruff + mypy inside backend container
	$(DC_DEV) exec backend ruff check src/
	$(DC_DEV) exec backend mypy src/ --ignore-missing-imports

# ── Utilities ─────────────────────────────────────────────────────────────────

.PHONY: shell-backend
shell-backend: ## Open shell inside backend container
	$(DC_DEV) exec backend bash

.PHONY: shell-ml
shell-ml: ## Open shell inside ml-inference container
	$(DC_DEV) exec ml-inference bash

.PHONY: redis-cli
redis-cli: ## Open redis-cli
	$(DC_DEV) exec redis redis-cli

.PHONY: env-check
env-check: ## Validate .env file exists and has required keys
	@scripts/env-check.sh

.PHONY: clean
clean: ## Remove __pycache__, .pyc, build artefacts (local)
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; true
	find . -name "*.pyc" -delete 2>/dev/null; true
	find . -name "*.egg-info" -type d -exec rm -rf {} + 2>/dev/null; true
