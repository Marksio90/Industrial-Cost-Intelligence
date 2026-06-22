#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ICI Platform — Health Check Script
#
# Usage: scripts/health-check.sh [--json]
#
# Checks every service and exits 0 only when all are healthy.
# ═══════════════════════════════════════════════════════════════════════════════

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'
JSON_MODE=false
[[ "${1:-}" == "--json" ]] && JSON_MODE=true

PASS=0; FAIL=0; WARN=0
declare -a RESULTS=()

_check_http() {
    local name="$1"
    local url="$2"
    local expected_field="${3:-}"

    local http_code
    local body
    body=$(curl -sf --max-time 5 "$url" 2>/dev/null) && http_code=200 || http_code=$?

    if [[ "$http_code" == "200" ]]; then
        if [[ -n "$expected_field" ]] && ! echo "$body" | grep -q "$expected_field"; then
            RESULTS+=("WARN|$name|responded but missing '$expected_field'")
            ((WARN++))
        else
            RESULTS+=("OK|$name|$url")
            ((PASS++))
        fi
    else
        RESULTS+=("FAIL|$name|$url (HTTP ${http_code})")
        ((FAIL++))
    fi
}

_check_docker() {
    local name="$1"
    local container="${2:-ici_${name}}"

    local status
    status=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not_found")

    case "$status" in
        healthy)   RESULTS+=("OK|$name (docker)|healthy"); ((PASS++)) ;;
        starting)  RESULTS+=("WARN|$name (docker)|still starting"); ((WARN++)) ;;
        unhealthy) RESULTS+=("FAIL|$name (docker)|unhealthy"); ((FAIL++)) ;;
        not_found) RESULTS+=("FAIL|$name (docker)|container not found"); ((FAIL++)) ;;
        *)         RESULTS+=("WARN|$name (docker)|status=${status}"); ((WARN++)) ;;
    esac
}

_check_postgres() {
    local dsn
    dsn=$(grep "^DATABASE_URL_SYNC=" .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
    dsn="${dsn:-postgresql://ici:ici@localhost:5432/ici}"

    local result
    if result=$(docker exec ici_postgres psql -U ici -d ici -c "SELECT COUNT(*) FROM ici.suppliers;" -t 2>/dev/null); then
        local count
        count=$(echo "$result" | tr -d ' ')
        RESULTS+=("OK|postgres (data)|${count} suppliers in ici.suppliers")
        ((PASS++))
    else
        RESULTS+=("FAIL|postgres (data)|cannot query ici.suppliers")
        ((FAIL++))
    fi
}

_check_redis() {
    local result
    if result=$(docker exec ici_redis redis-cli PING 2>/dev/null); then
        RESULTS+=("OK|redis|$result")
        ((PASS++))
    else
        RESULTS+=("FAIL|redis|PING failed")
        ((FAIL++))
    fi
}

_check_qdrant_collections() {
    local body
    if body=$(curl -sf --max-time 5 "http://localhost:6333/collections" 2>/dev/null); then
        local count
        count=$(echo "$body" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('result',{}).get('collections',[])))" 2>/dev/null || echo "?")
        RESULTS+=("OK|qdrant (collections)|${count} collection(s)")
        ((PASS++))
    else
        RESULTS+=("WARN|qdrant (collections)|cannot list collections")
        ((WARN++))
    fi
}

# ── Run checks ────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}ICI Platform — Health Check${RESET}\n"

# Docker healthchecks
_check_docker postgres
_check_docker redis
_check_docker qdrant
_check_docker backend
_check_docker worker
_check_docker "ml-inference" "ici_ml-inference"
_check_docker "rfq-agent"    "ici_rfq-agent"

# HTTP endpoints
_check_http "backend /health"         "http://localhost:8000/health"         '"status":"ok"'
_check_http "backend /health/ready"   "http://localhost:8000/health/ready"   '"status":"ready"'
_check_http "ml-inference /health"    "http://localhost:8002/health"         '"status"'
_check_http "rfq-agent /health"       "http://localhost:8001/health"         '"status"'
_check_http "mlflow"                  "http://localhost:5000/health"         ""
_check_http "prometheus"              "http://localhost:9090/-/ready"        ""
_check_http "grafana"                 "http://localhost:3000/api/health"     '"database":"ok"'
_check_http "nginx"                   "http://localhost/health"              '"status"'

# Deep checks
_check_postgres
_check_redis
_check_qdrant_collections

# ── Report ────────────────────────────────────────────────────────────────────
if $JSON_MODE; then
    python3 -c "
import json, sys
results = []
for r in sys.argv[1:]:
    parts = r.split('|', 2)
    results.append({'status': parts[0], 'service': parts[1], 'detail': parts[2] if len(parts) > 2 else ''})
print(json.dumps({'pass': $PASS, 'warn': $WARN, 'fail': $FAIL, 'checks': results}, indent=2))
" "${RESULTS[@]}"
else
    for r in "${RESULTS[@]}"; do
        IFS='|' read -r status service detail <<< "$r"
        case "$status" in
            OK)   echo -e "  ${GREEN}✓${RESET}  ${service}${RESET}  ${detail}" ;;
            WARN) echo -e "  ${YELLOW}⚠${RESET}  ${service}${RESET}  ${YELLOW}${detail}${RESET}" ;;
            FAIL) echo -e "  ${RED}✗${RESET}  ${service}${RESET}  ${RED}${detail}${RESET}" ;;
        esac
    done

    echo ""
    echo -e "  ${GREEN}Pass: ${PASS}${RESET}  ${YELLOW}Warn: ${WARN}${RESET}  ${RED}Fail: ${FAIL}${RESET}"
    echo ""
fi

[[ $FAIL -eq 0 ]]
