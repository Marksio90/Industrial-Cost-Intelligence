#!/usr/bin/env bash
# Validates .env has all required keys with non-empty values.

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RESET='\033[0m'
FAIL=0; WARN=0

REQUIRED=(
    DATABASE_URL
    REDIS_URL
    SECRET_KEY
    ENCRYPTION_KEYS
    ENCRYPTION_PRIMARY_KEY_ID
    POSTGRES_PASSWORD
    REDIS_PASSWORD
)

RECOMMENDED=(
    ANTHROPIC_API_KEY
    QDRANT_API_KEY
    GRAFANA_ADMIN_PASSWORD
    RFQ_SMTP_HOST
)

[[ -f .env ]] || { echo -e "${RED}✗ .env not found — run: cp .env.example .env${RESET}"; exit 1; }
source <(grep -v '^#' .env | grep '=')

echo -e "\n${YELLOW}Required variables:${RESET}"
for key in "${REQUIRED[@]}"; do
    val="${!key:-}"
    if [[ -z "$val" ]]; then
        echo -e "  ${RED}✗ ${key} — MISSING${RESET}"; ((FAIL++))
    elif [[ "$val" == *"change-in-production"* || "$val" == *"CHANGE"* ]]; then
        echo -e "  ${YELLOW}⚠ ${key} — placeholder value${RESET}"; ((WARN++))
    else
        echo -e "  ${GREEN}✓ ${key}${RESET}"
    fi
done

echo -e "\n${YELLOW}Recommended variables:${RESET}"
for key in "${RECOMMENDED[@]}"; do
    val="${!key:-}"
    if [[ -z "$val" ]]; then
        echo -e "  ${YELLOW}⚠ ${key} — not set (some features degraded)${RESET}"; ((WARN++))
    else
        echo -e "  ${GREEN}✓ ${key}${RESET}"
    fi
done

echo ""
[[ $FAIL -eq 0 ]] || { echo -e "${RED}${FAIL} required variable(s) missing.${RESET}"; exit 1; }
[[ $WARN -eq 0 ]] && echo -e "${GREEN}All checks passed.${RESET}" || echo -e "${YELLOW}${WARN} warning(s). Review above.${RESET}"
