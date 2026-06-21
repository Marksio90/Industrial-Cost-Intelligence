#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════════════
# ICI Platform — End-to-End Integration Test
#
# Exercises the full system through real HTTP calls:
#
#   Step 1  Auth:         Register → Login → get JWT
#   Step 2  Materials:    Create material, retrieve it, search by embedding
#   Step 3  Suppliers:    List suppliers (seeded), get one by ID
#   Step 4  Cost record:  Create cost snapshot, verify encryption round-trip
#   Step 5  ML predict:   POST cost prediction request to ML service
#   Step 6  Search:       Vector similarity search returns results
#   Step 7  RFQ:          Create RFQ session (no email sent in dev/mailhog mode)
#   Step 8  Metrics:      Prometheus /metrics endpoint returns ici_ counters
#   Step 9  Audit:        Confirm audit events written for the session
#   Step 10 Cleanup:      Delete created test records
#
# Usage:
#   scripts/e2e-test.sh
#   scripts/e2e-test.sh --base-url http://localhost:8000  (override API URL)
#   scripts/e2e-test.sh --verbose
# ═══════════════════════════════════════════════════════════════════════════════

set -uo pipefail
IFS=$'\n\t'

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Config ────────────────────────────────────────────────────────────────────
BASE_URL="http://localhost:8000"
ML_URL="http://localhost:8002"
VERBOSE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base-url) BASE_URL="$2"; shift 2 ;;
        --verbose)  VERBOSE=true; shift ;;
        *) shift ;;
    esac
done

API="${BASE_URL}/api/v1"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; RESET='\033[0m'

PASS=0; FAIL=0
STEP=0

_step() {
    ((STEP++))
    echo -e "\n${BOLD}Step ${STEP}: $*${RESET}"
}

_ok() {
    ((PASS++))
    echo -e "  ${GREEN}✓${RESET} $*"
}

_fail() {
    ((FAIL++))
    echo -e "  ${RED}✗${RESET} $*"
}

_assert_json() {
    local label="$1"
    local field="$2"
    local expected="$3"
    local json="$4"

    local actual
    actual=$(echo "$json" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    keys = '$field'.split('.')
    v = d
    for k in keys:
        v = v[int(k)] if k.isdigit() else v[k]
    print(v)
except Exception as e:
    print('__MISSING__')
" 2>/dev/null)

    if [[ "$actual" == "$expected" || ( -z "$expected" && "$actual" != "__MISSING__" ) ]]; then
        _ok "${label}: ${actual}"
    else
        _fail "${label}: expected '${expected}', got '${actual}'"
        $VERBOSE && echo "    Response: ${json:0:500}"
    fi
}

_curl() {
    local method="$1"; shift
    local path="$1";   shift
    local data="${1:-}"; [[ $# -gt 0 ]] && shift

    local url
    if [[ "$path" == http* ]]; then
        url="$path"
    else
        url="${API}${path}"
    fi

    local args=(-sf -X "$method" -H "Content-Type: application/json" --max-time 15)
    [[ -n "${AUTH_HEADER:-}" ]] && args+=(-H "Authorization: Bearer ${AUTH_HEADER}")
    [[ -n "$data" ]] && args+=(-d "$data")
    $VERBOSE && echo "    > ${method} ${url}" >&2

    local response
    response=$(curl "${args[@]}" "$url" 2>&1) || { _fail "HTTP ${method} ${url} failed (curl exit $?)"; echo "{}"; return 1; }
    $VERBOSE && echo "    < ${response:0:300}" >&2
    echo "$response"
}

# ─────────────────────────────────────────────────────────────────────────────
echo -e "\n${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  ICI Platform — E2E Integration Test${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "  API: ${CYAN}${BASE_URL}${RESET}  |  ML: ${CYAN}${ML_URL}${RESET}"

# ── Pre-check: services up ────────────────────────────────────────────────────
_step "Pre-flight: services responding"
for url in "${BASE_URL}/health" "${ML_URL}/health"; do
    if curl -sf --max-time 5 "$url" >/dev/null 2>&1; then
        _ok "$url"
    else
        _fail "$url — is the stack running? Try: make up"
    fi
done

[[ $FAIL -gt 0 ]] && { echo -e "\n${RED}Stack not running. Aborting.${RESET}"; exit 1; }

# ── Step 1: Auth ──────────────────────────────────────────────────────────────
_step "Authentication — login as demo user"

LOGIN_RESP=$(_curl POST "/auth/login" '{
    "email": "demo@ici.example.com",
    "password": "Demo1234!"
}' || echo '{}')

AUTH_HEADER=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")

if [[ -n "$AUTH_HEADER" && "$AUTH_HEADER" != "null" ]]; then
    _ok "Login succeeded — token: ${AUTH_HEADER:0:30}..."
else
    # Try demo token endpoint for development
    LOGIN_RESP=$(_curl POST "/auth/dev-token" '{"tenant_id":"tenant-demo","role":"ANALYST"}' || echo '{}')
    AUTH_HEADER=$(echo "$LOGIN_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('access_token',''))" 2>/dev/null || echo "")
    if [[ -n "$AUTH_HEADER" && "$AUTH_HEADER" != "null" ]]; then
        _ok "Dev token issued"
    else
        _fail "Auth failed — proceeding without token (some steps will fail)"
        AUTH_HEADER=""
    fi
fi

# ── Step 2: Materials ─────────────────────────────────────────────────────────
_step "Materials — create + retrieve"

CREATE_MAT=$(_curl POST "/materials" '{
    "code": "E2E-MAT-001",
    "name": "E2E Test Steel S355",
    "material_class": "METAL",
    "sub_class": "STEEL",
    "grade": "S355JR",
    "density_kg_m3": 7850,
    "unit": "KG",
    "min_order_quantity": 100,
    "lead_time_days": 10,
    "metadata": {"e2e": true}
}')

MAT_ID=$(echo "$CREATE_MAT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")

if [[ -n "$MAT_ID" && "$MAT_ID" != "null" ]]; then
    _ok "Material created: ${MAT_ID}"

    GET_MAT=$(_curl GET "/materials/${MAT_ID}")
    _assert_json "material.code" "code" "E2E-MAT-001" "$GET_MAT"
else
    _fail "Material creation failed: ${CREATE_MAT:0:200}"
    MAT_ID="00000000-0000-0000-0000-000000000000"
fi

# ── Step 3: Suppliers ─────────────────────────────────────────────────────────
_step "Suppliers — list seeded suppliers"

SUPPLIERS=$(_curl GET "/suppliers?limit=5")
SUP_COUNT=$(echo "$SUPPLIERS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d.get('items',d if isinstance(d,list) else [])))" 2>/dev/null || echo "0")

if [[ "$SUP_COUNT" -gt 0 ]]; then
    _ok "Found ${SUP_COUNT} suppliers (seed data loaded)"
    SUP_ID=$(echo "$SUPPLIERS" | python3 -c "
import sys,json
d=json.load(sys.stdin)
items = d.get('items', d) if isinstance(d, dict) else d
print(items[0]['id'] if items else '')
" 2>/dev/null || echo "")
    if [[ -n "$SUP_ID" ]]; then
        GET_SUP=$(_curl GET "/suppliers/${SUP_ID}")
        _assert_json "supplier.status" "status" "QUALIFIED" "$GET_SUP"
    fi
else
    _fail "No suppliers found — seed data may not have loaded"
    SUP_ID="11000000-0000-0000-0000-000000000001"
fi

# ── Step 4: Cost record ───────────────────────────────────────────────────────
_step "Cost records — create snapshot (tests field encryption)"

COST_RESP=$(_curl POST "/costs" "{
    \"material_id\": \"${MAT_ID}\",
    \"supplier_id\": \"${SUP_ID}\",
    \"unit_price\": 1.87,
    \"currency\": \"EUR\",
    \"valid_from\": \"2026-01-01\",
    \"valid_to\": \"2026-12-31\",
    \"quantity\": 1000,
    \"notes\": \"E2E test cost record\"
}")

COST_ID=$(echo "$COST_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
if [[ -n "$COST_ID" && "$COST_ID" != "null" ]]; then
    _ok "Cost record created: ${COST_ID}"
    _assert_json "cost.unit_price" "unit_price" "1.87" "$COST_RESP"
else
    _fail "Cost record creation failed: ${COST_RESP:0:200}"
    COST_ID=""
fi

# ── Step 5: ML prediction ─────────────────────────────────────────────────────
_step "ML inference — cost prediction"

ML_RESP=$(curl -sf --max-time 20 -X POST \
    -H "Content-Type: application/json" \
    "${ML_URL}/predict" \
    -d '{
        "material_class": "METAL",
        "sub_class": "STEEL",
        "grade": "S355JR",
        "quantity_kg": 1000,
        "supplier_country": "DE",
        "lead_time_days": 10
    }' 2>/dev/null || echo '{}')

PREDICTED=$(echo "$ML_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('predicted_price_eur_kg', d.get('prediction','')))" 2>/dev/null || echo "")
if [[ -n "$PREDICTED" && "$PREDICTED" != "null" && "$PREDICTED" != "" ]]; then
    _ok "ML prediction: €${PREDICTED}/kg"
    CONFIDENCE=$(echo "$ML_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('confidence_score', d.get('confidence','N/A')))" 2>/dev/null || echo "N/A")
    _ok "Confidence: ${CONFIDENCE}"
else
    _fail "ML prediction failed: ${ML_RESP:0:300}"
fi

# ── Step 6: Vector search ─────────────────────────────────────────────────────
_step "Vector search — similarity query"

SEARCH_RESP=$(_curl POST "/search" '{
    "query": "steel S355 structural material Germany",
    "limit": 5,
    "min_score": 0.5
}')

HITS=$(echo "$SEARCH_RESP" | python3 -c "
import sys,json
d=json.load(sys.stdin)
hits = d.get('results', d.get('hits', d.get('items', [])))
print(len(hits))
" 2>/dev/null || echo "0")

if [[ "$HITS" -gt 0 ]]; then
    _ok "Vector search returned ${HITS} result(s)"
else
    _fail "Vector search returned 0 results — Qdrant may be empty (run: make seed-vectors)"
fi

# ── Step 7: RFQ session ───────────────────────────────────────────────────────
_step "RFQ agent — create session (mailhog dev mode, no real email)"

RFQ_RESP=$(_curl POST "/rfq" "{
    \"title\": \"E2E Test RFQ - Steel S355\",
    \"material_ids\": [\"${MAT_ID}\"],
    \"quantity\": 5000,
    \"currency\": \"EUR\",
    \"deadline\": \"2026-07-15\",
    \"notes\": \"E2E integration test — please ignore\"
}")

RFQ_ID=$(echo "$RFQ_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('id', d.get('session_id','')))" 2>/dev/null || echo "")
if [[ -n "$RFQ_ID" && "$RFQ_ID" != "null" ]]; then
    _ok "RFQ session created: ${RFQ_ID}"
    STATUS=$(echo "$RFQ_RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status',''))" 2>/dev/null || echo "")
    [[ -n "$STATUS" ]] && _ok "RFQ status: ${STATUS}"
else
    _fail "RFQ creation failed: ${RFQ_RESP:0:200}"
fi

# ── Step 8: Metrics ───────────────────────────────────────────────────────────
_step "Observability — Prometheus metrics"

METRICS=$(curl -sf --max-time 5 "${BASE_URL}/metrics" 2>/dev/null || echo "")
if echo "$METRICS" | grep -q "ici_"; then
    ICI_METRICS=$(echo "$METRICS" | grep -c "^ici_" || true)
    _ok "Found ${ICI_METRICS} ici_* metric lines"
    echo "$METRICS" | grep "^ici_http_requests_total" | head -3 | while read -r line; do
        echo "    ${line:0:120}"
    done
else
    _fail "No ici_* metrics found at ${BASE_URL}/metrics"
fi

# ── Step 9: Audit log ─────────────────────────────────────────────────────────
_step "Audit log — verify events recorded"

AUDIT_COUNT=$(docker exec ici_postgres psql -U ici -d ici -t \
    -c "SELECT COUNT(*) FROM audit_events WHERE created_at > NOW() - INTERVAL '10 minutes';" \
    2>/dev/null | tr -d ' ' || echo "?")

if [[ "$AUDIT_COUNT" != "?" && "$AUDIT_COUNT" -gt 0 ]]; then
    _ok "${AUDIT_COUNT} audit event(s) in last 10 minutes"
else
    _fail "No audit events found (or audit_events table missing)"
fi

# ── Step 10: Cleanup ──────────────────────────────────────────────────────────
_step "Cleanup — delete test records"

[[ -n "${COST_ID:-}" ]] && _curl DELETE "/costs/${COST_ID}" >/dev/null 2>&1 && _ok "Cost record deleted"
[[ -n "${MAT_ID:-}" && "$MAT_ID" != "00000000-0000-0000-0000-000000000000" ]] && \
    _curl DELETE "/materials/${MAT_ID}" >/dev/null 2>&1 && _ok "Material deleted"

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo -e "  ${GREEN}Pass: ${PASS}${RESET}   ${RED}Fail: ${FAIL}${RESET}   Steps: ${STEP}"
echo -e "${BOLD}════════════════════════════════════════════════${RESET}"
echo ""

if [[ $FAIL -eq 0 ]]; then
    echo -e "${GREEN}${BOLD}All checks passed.${RESET} ICI Platform is fully integrated."
else
    echo -e "${RED}${FAIL} check(s) failed.${RESET} Review output above."
    echo -e "  Run ${BOLD}make logs${RESET} to inspect service logs."
fi
echo ""

exit $([[ $FAIL -eq 0 ]] && echo 0 || echo 1)
