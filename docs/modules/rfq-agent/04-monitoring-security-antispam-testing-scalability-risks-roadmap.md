# RFQ Agent — Monitoring, Security, Anti-spam, Testing, Scalability, Risks, Roadmap

## 15. Monitoring

### 15.1 Metryki Prometheus

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# --- RFQ Throughput ---
RFQA_CYCLES_TOTAL = Counter(
    "rfqa_cycles_total",
    "Total RFQ cycles by final state",
    ["state", "decision_outcome"],
)
RFQA_CYCLE_DURATION_SECONDS = Histogram(
    "rfqa_cycle_duration_seconds",
    "End-to-end RFQ cycle duration",
    ["auto_approved"],
    buckets=[60, 300, 900, 1800, 3600, 7200, 14400, 86400],
)
RFQA_SAVINGS_EUR = Histogram(
    "rfqa_savings_eur",
    "Savings achieved per RFQ cycle (vs CEE target)",
    buckets=[0, 100, 500, 1000, 5000, 10000, 50000, 100000],
)
RFQA_SAVINGS_PCT = Histogram(
    "rfqa_savings_pct",
    "Savings percentage per cycle",
    buckets=[0, 2, 5, 10, 15, 20, 30, 50],
)

# --- Agent Execution ---
RFQA_AGENT_ITERATIONS = Histogram(
    "rfqa_agent_iterations_per_cycle",
    "Number of ReAct iterations per RFQ cycle",
    buckets=[1, 5, 10, 15, 20, 30, 50],
)
RFQA_AGENT_TOKENS = Histogram(
    "rfqa_agent_tokens_total",
    "Total LLM tokens used per RFQ cycle",
    buckets=[1000, 5000, 10000, 25000, 50000, 100000, 200000],
)
RFQA_TOOL_CALLS_TOTAL = Counter(
    "rfqa_tool_calls_total",
    "Total agent tool calls",
    ["tool_name", "status"],             # status: success | error | blocked
)
RFQA_TOOL_LATENCY = Histogram(
    "rfqa_tool_latency_seconds",
    "Tool execution latency",
    ["tool_name"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 15.0, 30.0],
)
RFQA_LLM_LATENCY = Histogram(
    "rfqa_llm_latency_seconds",
    "LLM API call latency per ReAct step",
    buckets=[0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 20.0],
)

# --- Email ---
RFQA_EMAILS_SENT = Counter(
    "rfqa_emails_sent_total",
    "Emails sent to suppliers",
    ["language", "status"],
)
RFQA_EMAIL_REPLY_RATE = Gauge(
    "rfqa_email_reply_rate_7d",
    "7-day rolling reply rate for RFQ emails",
)
RFQA_OFFERS_PER_CYCLE = Histogram(
    "rfqa_offers_per_cycle",
    "Number of offers received per RFQ cycle",
    buckets=[0, 1, 2, 3, 5, 7, 10, 15],
)

# --- HITL ---
RFQA_HITL_REQUESTS_TOTAL = Counter(
    "rfqa_hitl_requests_total",
    "HITL requests by type",
    ["request_type"],
)
RFQA_HITL_RESOLUTION_SECONDS = Histogram(
    "rfqa_hitl_resolution_seconds",
    "Time to HITL decision",
    ["decision"],
    buckets=[300, 900, 1800, 3600, 7200, 14400, 86400],
)
RFQA_HITL_TIMEOUT_RATE = Gauge(
    "rfqa_hitl_timeout_rate_7d",
    "7-day HITL timeout rate",
)

# --- Supplier Discovery ---
RFQA_SUPPLIERS_DISCOVERED = Histogram(
    "rfqa_suppliers_discovered_per_cycle",
    "Suppliers discovered per cycle by source",
    ["source"],
    buckets=[0, 1, 3, 5, 10, 15, 20],
)
RFQA_SCRAPING_DURATION = Histogram(
    "rfqa_scraping_duration_seconds",
    "Web scraping duration per target",
    ["target"],
    buckets=[1, 5, 10, 20, 30, 60],
)
RFQA_SPAM_BLOCKS_TOTAL = Counter(
    "rfqa_spam_blocks_total",
    "Number of email sends blocked by anti-spam",
    ["reason"],
)

# --- Risk ---
RFQA_RISK_GATES_TOTAL = Counter(
    "rfqa_risk_gates_total",
    "Risk gate evaluations",
    ["level", "action"],                # action: pass | hitl | block
)
RFQA_ANOMALOUS_OFFERS = Counter(
    "rfqa_anomalous_offers_total",
    "Offers flagged as anomalous",
    ["flag_type"],
)

# --- Price Index ---
RFQA_PRICE_INDEX_UPDATES = Counter(
    "rfqa_price_index_updates_total",
    "Market price index updates",
    ["material_code"],
)
```

### 15.2 Reguły Alertmanager

```yaml
groups:
  - name: rfqa_sla
    rules:
      - alert: RFQAHighCycleFailureRate
        expr: |
          rate(rfqa_cycles_total{state="FAILED"}[1h]) /
          rate(rfqa_cycles_total[1h]) > 0.10
        for: 15m
        labels:
          severity: critical
          team: rfqa
        annotations:
          summary: "RFQA cycle failure rate > 10%"
          runbook: "https://wiki.ici.internal/runbooks/rfqa-failures"

      - alert: RFQALowEmailReplyRate
        expr: rfqa_email_reply_rate_7d < 0.25
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "RFQA 7d email reply rate < 25% — supplier engagement low"

      - alert: RFQAHITLQueueBacklog
        expr: |
          count(rfqa_hitl_requests_total) - count(rfqa_hitl_decided_total) > 20
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "RFQA HITL queue backlog > 20 pending decisions"

      - alert: RFQAHITLTimeoutHigh
        expr: rfqa_hitl_timeout_rate_7d > 0.15
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "RFQA HITL timeout rate > 15% — reviewers not responding"

      - alert: RFQAHighLLMLatency
        expr: |
          histogram_quantile(0.95, rate(rfqa_llm_latency_seconds_bucket[10m])) > 15
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "RFQA P95 LLM latency > 15s — agent slowdown"

      - alert: RFQAScrapingFailureHigh
        expr: |
          rate(rfqa_tool_calls_total{tool_name="web_scrape",status="error"}[30m]) /
          rate(rfqa_tool_calls_total{tool_name="web_scrape"}[30m]) > 0.40
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "RFQA web scraping error rate > 40%"

      - alert: RFQASpamBlocksHigh
        expr: rate(rfqa_spam_blocks_total[1h]) > 5
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "RFQA spam blocks > 5/hour — possible misconfiguration"

      - alert: RFQAOutboxLag
        expr: |
          (time() - timestamp(rfqa_outbox_last_relay_timestamp > 0)) > 300
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "RFQA outbox relay not running for > 5 minutes"
```

### 15.3 Dashboardy Grafana (7)

| Dashboard | Panele kluczowe | Opis |
|-----------|----------------|------|
| **RFQA Overview** | cycle rate, state funnel, savings EUR/pct, auto-approval rate | Główny widok operacyjny |
| **Agent Performance** | tokens/cycle, iterations/cycle, LLM latency P50/P95, tool call distribution | Efektywność agenta AI |
| **Email & Reply** | emails sent/day, reply rate 7d, bounce rate, language breakdown | Monitoring komunikacji |
| **HITL Dashboard** | queue depth, resolution time, timeout rate, decisions by type | Panel dla zakupowców |
| **Supplier Analytics** | top suppliers by win rate, quality ratings, country heatmap | Zarządzanie dostawcami |
| **Market Prices** | price index by material, P10-P90 bands, savings vs market | Wywiad cenowy |
| **Risk & Security** | risk gate blocks, anomalous offers, spam blocks, blacklist events | Bezpieczeństwo |

---

## 16. Security

### 16.1 Architektura bezpieczeństwa

```
┌──────────────────────────────────────────────────────────────┐
│                      Security Layers                         │
│                                                              │
│  L1: API Gateway (JWT validation, rate limiting, WAF)        │
│  L2: RBAC middleware (7 roles, resource-level)               │
│  L3: Agent action authorization (RiskController pre-check)   │
│  L4: Data-level: PII masking, credential vault               │
│  L5: External comms: email DLP, scraping stealth limits      │
│  L6: Audit: immutable audit_log, tamper-evident chain        │
└──────────────────────────────────────────────────────────────┘
```

### 16.2 JWT Middleware

```python
import jwt
from datetime import datetime, timezone
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer

REQUIRED_ROLES: dict[str, str] = {
    "POST /rfq":                        "RFQA_USER",
    "GET /rfq":                         "RFQA_VIEWER",
    "POST /hitl/{id}/decide":           "RFQA_REVIEWER",
    "POST /suppliers/{id}/blacklist":   "RFQA_PROCUREMENT",
    "GET /admin/blacklist":             "RFQA_ADMIN",
}

ROLE_HIERARCHY = {
    "RFQA_VIEWER":      0,
    "RFQA_USER":        1,
    "RFQA_ANALYST":     2,
    "RFQA_REVIEWER":    3,
    "RFQA_PROCUREMENT": 4,
    "RFQA_OPS":         5,
    "RFQA_ADMIN":       6,
}

class RFQAAuthMiddleware:

    def __init__(self, public_key: str, audience: str):
        self._public_key = public_key
        self._audience   = audience

    async def __call__(self, request: Request, call_next):
        token = self._extract_token(request)
        if not token:
            raise HTTPException(status_code=401, detail="Missing authorization token")

        try:
            claims = jwt.decode(
                token,
                self._public_key,
                algorithms=["RS256"],
                audience=self._audience,
            )
        except jwt.ExpiredSignatureError:
            raise HTTPException(status_code=401, detail="Token expired")
        except jwt.InvalidTokenError as e:
            raise HTTPException(status_code=401, detail=f"Invalid token: {e}")

        required_role = self._get_required_role(request)
        user_roles    = claims.get("roles", [])

        if required_role and not self._has_role(user_roles, required_role):
            raise HTTPException(status_code=403, detail=f"Role {required_role} required")

        request.state.user_id = claims["sub"]
        request.state.roles   = user_roles
        return await call_next(request)

    def _has_role(self, user_roles: list[str], required: str) -> bool:
        req_level = ROLE_HIERARCHY.get(required, 99)
        return any(
            ROLE_HIERARCHY.get(r, -1) >= req_level for r in user_roles
        )

    def _extract_token(self, request: Request) -> str | None:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return None

    def _get_required_role(self, request: Request) -> str | None:
        path   = request.url.path.replace("/v1/rfqa", "")
        method = request.method
        key    = f"{method} {path}"
        # Exact match
        if key in REQUIRED_ROLES:
            return REQUIRED_ROLES[key]
        # Pattern match (simplified)
        for pattern, role in REQUIRED_ROLES.items():
            if self._matches(pattern, key):
                return role
        return None

    def _matches(self, pattern: str, key: str) -> bool:
        import re
        regex = re.escape(pattern).replace(r"\{[^}]+\}", "[^/]+")
        return bool(re.fullmatch(regex, key))
```

### 16.3 Credential Vault (portal credentials)

```python
from cryptography.fernet import Fernet
import base64, os

class CredentialVault:
    """Encrypts/decrypts portal credentials using Fernet symmetric encryption."""

    def __init__(self, key: bytes | None = None):
        self._key    = key or Fernet.generate_key()
        self._fernet = Fernet(self._key)

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        return self._fernet.decrypt(ciphertext.encode()).decode()

    @classmethod
    def from_env(cls) -> "CredentialVault":
        key = os.environ.get("RFQA_VAULT_KEY")
        if not key:
            raise ValueError("RFQA_VAULT_KEY environment variable not set")
        return cls(base64.urlsafe_b64decode(key))
```

### 16.4 PII Masking

```python
import re

class PIIMasker:
    """Masks PII in agent logs and traces before persistence."""

    EMAIL_RE   = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b")
    PHONE_RE   = re.compile(r"\b\+?[\d\s\-().]{10,}\b")
    IBAN_RE    = re.compile(r"\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b")
    CC_RE      = re.compile(r"\b(?:\d[ -]?){13,16}\b")

    def mask(self, text: str, context: str = "log") -> str:
        if context == "trace":
            # In traces, only mask financial data
            text = self.IBAN_RE.sub("[IBAN_REDACTED]", text)
            text = self.CC_RE.sub("[CC_REDACTED]", text)
        else:
            text = self.EMAIL_RE.sub("[EMAIL_REDACTED]", text)
            text = self.PHONE_RE.sub("[PHONE_REDACTED]", text)
            text = self.IBAN_RE.sub("[IBAN_REDACTED]", text)
        return text
```

### 16.5 Kontrole bezpieczeństwa

| Kontrola | Implementacja |
|----------|--------------|
| Authentication | JWT RS256, 1h TTL, refresh tokens |
| Authorization | RBAC 7 ról + resource-level checks |
| Rate limiting | 100 RFQ/day per user, 1000/day org-wide |
| Email DLP | RegEx scan emaili wychodzących (brak cen docelowych, NDA) |
| Credential storage | Fernet encryption, vault in PG, key in env/HSM |
| Scraping rate limits | Max 1 req/sec per domain, rotating user-agents |
| Audit trail | Append-only rfqa.audit_log, PostgreSQL row-level security |
| Secret rotation | RFQA_VAULT_KEY rotatable without downtime via re-encryption |
| GDPR | Email addresses masked in logs, retention policy 2 years |
| SQL injection | asyncpg parameterized queries throughout |
| Prompt injection | Supplier response sandboxed in parse prompt; no tool access |
| Dependency scanning | Dependabot + Snyk in CI pipeline |

---

## 17. Anti-spam Rules

### 17.1 Silnik anti-spam

```python
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from enum import Enum

class SpamCheckResult(Enum):
    ALLOWED  = "ALLOWED"
    BLOCKED  = "BLOCKED"
    DEFERRED = "DEFERRED"

@dataclass
class SpamRuleOutcome:
    result:    SpamCheckResult
    reason:    str
    retry_after: datetime | None = None

class AntiSpamEngine:
    """
    Enforces all anti-spam rules before any email dispatch.
    Rules stack: all must pass for email to be allowed.
    """

    COOLDOWN_DAYS          = 30     # Same supplier + material
    MAX_EMAILS_PER_DAY     = 3      # Across all RFQs for one supplier
    MAX_EMAILS_PER_WEEK    = 8
    GLOBAL_SEND_RATE       = 200    # Max total emails per hour (org-wide)
    BLACKLISTED_DOMAINS    = {
        "spam.com", "tempmail.com", "throwaway.email",
        "mailinator.com", "guerrillamail.com", "yopmail.com",
    }
    BLOCKED_TLD            = {".ru", ".cn"}     # configurable per deployment
    MIN_DOMAIN_AGE_DAYS    = 90     # Reject emails to domains < 90 days old

    def __init__(self, db: "asyncpg.Pool"):
        self._db = db

    async def check(
        self,
        supplier_id:   str | None,
        email_address: str,
        material_code: str,
        rfq_id:        str,
    ) -> SpamRuleOutcome:
        domain = email_address.split("@")[-1].lower() if "@" in email_address else ""

        # Rule 1: Blocked domain
        if domain in self.BLACKLISTED_DOMAINS:
            return SpamRuleOutcome(SpamCheckResult.BLOCKED, f"Blocked domain: {domain}")

        # Rule 2: Blocked TLD
        for tld in self.BLOCKED_TLD:
            if domain.endswith(tld):
                return SpamRuleOutcome(SpamCheckResult.BLOCKED, f"Blocked TLD: {tld}")

        # Rule 3: Supplier blacklist
        if supplier_id and await self._is_blacklisted(supplier_id):
            return SpamRuleOutcome(SpamCheckResult.BLOCKED, "Supplier on blacklist")

        # Rule 4: Same material cooldown (30 days)
        if supplier_id:
            last_contact = await self._last_contact_date(supplier_id, material_code)
            if last_contact:
                cooldown_until = last_contact + timedelta(days=self.COOLDOWN_DAYS)
                if datetime.now(timezone.utc) < cooldown_until:
                    return SpamRuleOutcome(
                        SpamCheckResult.DEFERRED,
                        f"Cooldown active until {cooldown_until.date()}",
                        retry_after=cooldown_until,
                    )

        # Rule 5: Daily supplier send rate
        if supplier_id:
            today_count = await self._count_sent_today(supplier_id)
            if today_count >= self.MAX_EMAILS_PER_DAY:
                tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).replace(
                    hour=0, minute=0, second=0
                )
                return SpamRuleOutcome(
                    SpamCheckResult.DEFERRED,
                    f"Daily limit ({self.MAX_EMAILS_PER_DAY}) reached for this supplier",
                    retry_after=tomorrow,
                )

        # Rule 6: Weekly supplier rate
        if supplier_id:
            week_count = await self._count_sent_this_week(supplier_id)
            if week_count >= self.MAX_EMAILS_PER_WEEK:
                return SpamRuleOutcome(
                    SpamCheckResult.DEFERRED,
                    f"Weekly limit ({self.MAX_EMAILS_PER_WEEK}) reached",
                )

        # Rule 7: Global hourly rate
        hourly_total = await self._count_global_hourly()
        if hourly_total >= self.GLOBAL_SEND_RATE:
            return SpamRuleOutcome(
                SpamCheckResult.DEFERRED,
                f"Global hourly rate limit ({self.GLOBAL_SEND_RATE}) reached",
                retry_after=datetime.now(timezone.utc) + timedelta(hours=1),
            )

        return SpamRuleOutcome(SpamCheckResult.ALLOWED, "")

    async def _is_blacklisted(self, supplier_id: str) -> bool:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM rfqa.supplier_blacklist WHERE supplier_id=$1 AND active=TRUE",
                supplier_id,
            )
        return row is not None

    async def _last_contact_date(self, supplier_id: str, material_code: str) -> datetime | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT MAX(sent_at) AS last FROM rfqa.email_log "
                "WHERE supplier_id=$1 AND material_code=$2 AND status != 'FAILED'",
                supplier_id, material_code,
            )
        return row["last"] if row and row["last"] else None

    async def _count_sent_today(self, supplier_id: str) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM rfqa.email_log "
                "WHERE supplier_id=$1 AND sent_at > CURRENT_DATE::TIMESTAMPTZ",
                supplier_id,
            )
        return int(row["n"]) if row else 0

    async def _count_sent_this_week(self, supplier_id: str) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM rfqa.email_log "
                "WHERE supplier_id=$1 AND sent_at > date_trunc('week', now())",
                supplier_id,
            )
        return int(row["n"]) if row else 0

    async def _count_global_hourly(self) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM rfqa.email_log "
                "WHERE sent_at > now() - INTERVAL '1 hour' AND status='SENT'",
            )
        return int(row["n"]) if row else 0
```

### 17.2 Tabela reguł anti-spam

| Reguła | Próg | Akcja | Override |
|--------|------|-------|----------|
| Blocked domain | Lista | BLOCK | RFQA_ADMIN |
| Supplier blacklist | Aktywny wpis | BLOCK | RFQA_ADMIN |
| Same material cooldown | 30 dni | DEFER | RFQA_OPS |
| Daily supplier limit | 3/dzień | DEFER | RFQA_PROCUREMENT |
| Weekly supplier limit | 8/tydzień | DEFER | RFQA_PROCUREMENT |
| Global hourly rate | 200/h | DEFER | RFQA_OPS |
| Duplicate content | MD5 hash | BLOCK | auto |
| Missing unsubscribe | Brak link | WARN | — |
| Non-business hours send | 22:00–06:00 | DEFER | RFQA_USER |

---

## 18. Testing

### 18.1 Macierz testów

| Typ | Narzędzie | Zakres | Cel |
|-----|-----------|--------|-----|
| Unit | pytest | Parser, normalizer, scorer, anti-spam engine | Logika biznesowa |
| Integration | pytest + Testcontainers | Agent → PG + Redis + Kafka | Kontrakty DB |
| Agent simulation | pytest + LLM mock | Pełny cykl z mock tools | ReAct loop |
| Contract | Pact | RFQA → SIE, RFQA → SCSE, RFQA → CEE | API compatibility |
| Email | mailtrap + pytest | EmailDispatcher + anti-spam | Email delivery |
| Scraping | playwright + fixtures | SupplierPortalScraper | Scraping accuracy |
| Load | k6 | POST /rfq × 50 VU | P95 API < 500ms |
| Security | OWASP ZAP + bandit | API + code | OWASP Top 10 |

### 18.2 Testy jednostkowe

```python
import pytest
from unittest.mock import AsyncMock, patch
import asyncio

class TestAntiSpamEngine:

    @pytest.mark.asyncio
    async def test_blocked_domain(self, spam_engine):
        result = await spam_engine.check(
            supplier_id=None,
            email_address="contact@mailinator.com",
            material_code="1.0503",
            rfq_id="test-rfq-1",
        )
        assert result.result.value == "BLOCKED"
        assert "mailinator" in result.reason

    @pytest.mark.asyncio
    async def test_cooldown_active(self, spam_engine, db_pool):
        # Seed a recent email
        async with db_pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO rfqa.email_log (email_id, rfq_id, supplier_id, to_address, "
                "material_code, status, sent_at) VALUES "
                "(gen_random_uuid(), 'rfq-1', $1, 'a@b.com', '1.0503', 'SENT', now())",
                "00000000-0000-0000-0000-000000000001",
            )
        result = await spam_engine.check(
            supplier_id="00000000-0000-0000-0000-000000000001",
            email_address="supplier@example.com",
            material_code="1.0503",
            rfq_id="rfq-2",
        )
        assert result.result.value == "DEFERRED"
        assert result.retry_after is not None

    @pytest.mark.asyncio
    async def test_allowed_first_contact(self, spam_engine):
        result = await spam_engine.check(
            supplier_id="00000000-0000-0000-0000-000000000999",
            email_address="new@legitcompany.com",
            material_code="3.7164",
            rfq_id="rfq-new",
        )
        assert result.result.value == "ALLOWED"

class TestOfferNormalizer:

    @pytest.mark.asyncio
    async def test_currency_conversion(self, normalizer):
        parsed = ParsedResponse(
            raw_source="...", channel=ResponseChannel.EMAIL,
            supplier_id="sup-1", supplier_name="Acme", rfq_id="rfq-1",
            unit_price=1200.0, currency="USD",
            total_price=None, quantity=100.0,
            delivery_days=14, delivery_date=None,
            payment_terms="Net 30", incoterms="DAP",
            parse_confidence=0.90,
        )
        offer = await normalizer.normalize(parsed, quantity=100.0)
        # USD at ~1.08 rate: 1200 / 1.08 ≈ 1111 EUR, then +1.5% DAP adj
        assert offer.currency_original == "USD"
        assert offer.unit_price_eur < 1200.0  # Converted to EUR
        assert offer.incoterms_original == "DAP"

    @pytest.mark.asyncio
    async def test_exw_has_highest_adder(self, normalizer):
        base = ParsedResponse(
            raw_source="", channel=ResponseChannel.EMAIL,
            supplier_id="s1", supplier_name="X", rfq_id="r1",
            unit_price=100.0, currency="EUR", total_price=None,
            quantity=1.0, delivery_days=10, delivery_date=None,
            payment_terms="Net 30", incoterms="EXW", parse_confidence=0.85,
        )
        ddp = ParsedResponse(**{**base.__dict__, "incoterms": "DDP"})

        offer_exw = await normalizer.normalize(base, quantity=100.0)
        offer_ddp = await normalizer.normalize(ddp,  quantity=100.0)

        assert offer_exw.unit_price_eur > offer_ddp.unit_price_eur

    def test_risk_score_prepayment(self, normalizer):
        parsed = ParsedResponse(
            raw_source="", channel=ResponseChannel.EMAIL,
            supplier_id=None, supplier_name="", rfq_id="r1",
            unit_price=50.0, currency="EUR", total_price=None,
            quantity=100.0, delivery_days=30, delivery_date=None,
            payment_terms="Prepayment", incoterms="DDP", parse_confidence=0.80,
        )
        score, flags = normalizer._assess_risk(parsed, 50.0)
        assert score > 0.15
        assert any("Prepayment" in f for f in flags)

class TestDecisionEngine:

    @pytest.mark.asyncio
    async def test_auto_select_high_confidence(self, decision_engine, config):
        offers = [
            NormalizedOffer(
                offer_id="o1", rfq_id="r1", supplier_id="s1", supplier_name="Best Co",
                unit_price_eur=95.0, total_price_eur=9500.0,
                currency_original="EUR", fx_rate_used=1.0,
                incoterms_original="DDP", incoterms_adj_eur=0.0,
                payment_terms_std=0.0, delivery_days=14,
                certifications=["ISO9001", "IATF16949"], quality_score=0.90,
                risk_score=0.05, risk_flags=[],
                validity_until=None, parse_confidence=0.92,
            ),
            NormalizedOffer(
                offer_id="o2", rfq_id="r1", supplier_id="s2", supplier_name="Runner Co",
                unit_price_eur=108.0, total_price_eur=10800.0,
                currency_original="EUR", fx_rate_used=1.0,
                incoterms_original="DDP", incoterms_adj_eur=0.0,
                payment_terms_std=0.0, delivery_days=20,
                certifications=["ISO9001"], quality_score=0.80,
                risk_score=0.10, risk_flags=[],
                validity_until=None, parse_confidence=0.88,
            ),
        ]
        scored = await decision_engine.score_offers(
            offers=offers,
            target_price=100.0,
            required_certs=["ISO9001"],
            required_delivery_days=21,
        )
        assert scored[0].supplier_id == "s1"
        assert scored[0].composite_score > scored[1].composite_score

class TestAgentReActLoop:

    @pytest.mark.asyncio
    async def test_full_cycle_mock(self, rfq_agent, mock_tools, simple_rfq_request):
        """Test agent completes cycle with mocked tool responses."""
        mock_tools.supplier_search.return_value = {
            "suppliers": [{"supplier_id": "s1", "name": "Mock Supplier",
                          "email": "q@mock.com", "confidence": 0.85}]
        }
        mock_tools.email_send.return_value = {"message_id": "msg-1", "status": "SENT"}
        mock_tools.parse_response.return_value = {
            "unit_price": 95.0, "currency": "EUR", "delivery_days": 14,
            "payment_terms": "Net 30", "incoterms": "DDP",
            "parse_confidence": 0.90, "risk_flags": [],
        }
        mock_tools.compare_offers.return_value = {
            "winner_supplier_id": "s1", "savings_eur": 500.0,
            "auto_approved": True, "decision_outcome": "AUTO_SELECT",
        }

        result = await rfq_agent.run(simple_rfq_request)
        assert result.status in ("COMPLETED", "AWAITING_HITL")

    @pytest.mark.asyncio
    async def test_risk_gate_blocks_large_spend(self, rfq_agent, large_spend_rfq):
        """Agent must request HITL when spend > max_auto_spend_eur."""
        result = await rfq_agent.run(large_spend_rfq)
        assert result.status == "AWAITING_HITL"
```

### 18.3 Load test (k6)

```javascript
// k6/rfqa_load.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter, Rate } from "k6/metrics";

const rfqLatency  = new Trend("rfq_create_latency");
const rfqErrors   = new Counter("rfq_errors");
const successRate = new Rate("success_rate");

export const options = {
  scenarios: {
    steady: {
      executor:    "constant-arrival-rate",
      rate:        10,                  // 10 RFQ/s
      timeUnit:    "1s",
      duration:    "5m",
      preAllocatedVUs: 30,
    },
  },
  thresholds: {
    "rfq_create_latency": ["p(95)<500", "p(99)<2000"],
    "success_rate":        ["rate>0.99"],
  },
};

const BASE_URL = __ENV.RFQA_URL || "http://rfqa.internal/v1/rfqa";
const TOKEN    = __ENV.JWT_TOKEN || "load-test-token";

const PAYLOAD = JSON.stringify({
  product_name:   "Test Shaft",
  material_code:  "1.0503",
  quantity:       1000,
  unit:           "pcs",
  required_delivery: "2025-03-01",
  quote_deadline: "2025-01-15",
  target_price_eur: 95.0,
  budget_limit_eur: 100000,
  preferred_location: "PL",
  required_certifications: ["ISO9001"],
});

export default function () {
  const res = http.post(`${BASE_URL}/rfq`, PAYLOAD, {
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${TOKEN}`,
    },
    timeout: "10s",
  });

  const ok = check(res, {
    "status 202": (r) => r.status === 202,
    "has rfq_id": (r) => r.json("rfq_id") !== undefined,
  });

  rfqLatency.add(res.timings.duration);
  successRate.add(ok);
  if (!ok) rfqErrors.add(1);
  sleep(0.1);
}
```

---

## 19. Scalability

### 19.1 Poziomy skalowalności

| Poziom | Wolumen | Konfiguracja |
|--------|---------|-------------|
| L1 Dev | < 10 RFQ/dzień | 1 instancja, 1 agent worker, PG single |
| L2 Small | < 200 RFQ/dzień | 2 API pods + 2 agent workers, Redis Sentinel |
| L3 Medium | < 2K RFQ/dzień | 4-8 pods HPA, agent worker pool 10, PG + replika |
| L4 Enterprise | > 10K RFQ/dzień | Kubernetes HPA (4-30 pods), agent pool 50, GPU LLM inference |

### 19.2 Kubernetes HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: rfqa-api-hpa
  namespace: rfqa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: rfqa-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: External
      external:
        metric:
          name: rfqa_active_cycles
        target:
          type: AverageValue
          averageValue: "50"

---
# Agent worker pool (separate deployment for LLM-heavy tasks)
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: rfqa-agent-worker-hpa
  namespace: rfqa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: rfqa-agent-worker
  minReplicas: 2
  maxReplicas: 30
  metrics:
    - type: External
      external:
        metric:
          name: rfqa_job_queue_depth
        target:
          type: AverageValue
          averageValue: "5"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30
      policies:
        - type: Pods
          value: 3
          periodSeconds: 30
    scaleDown:
      stabilizationWindowSeconds: 600
```

### 19.3 Agent worker concurrency model

```python
import asyncio
from asyncio import Semaphore

class AgentWorkerPool:
    """
    Runs multiple RFQ agent cycles concurrently.
    LLM calls are I/O-bound — asyncio handles concurrency.
    Playwright scraping uses thread pool to avoid blocking.
    """

    def __init__(
        self,
        max_concurrent_rfq: int = 10,
        max_concurrent_scrape: int = 5,
    ):
        self._rfq_sem    = Semaphore(max_concurrent_rfq)
        self._scrape_sem = Semaphore(max_concurrent_scrape)
        self._jobs: dict[str, asyncio.Task] = {}

    async def submit(self, rfq_request: RFQRequest, agent: RFQAgent) -> None:
        async def run():
            async with self._rfq_sem:
                await agent.run(rfq_request)

        task = asyncio.create_task(run())
        self._jobs[rfq_request.rfq_id] = task
        task.add_done_callback(lambda _: self._jobs.pop(rfq_request.rfq_id, None))

    async def cancel(self, rfq_id: str) -> bool:
        task = self._jobs.get(rfq_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    @property
    def active_count(self) -> int:
        return len(self._jobs)
```

### 19.4 Distributed email queue

```python
# Redis-backed email queue for rate-limited dispatch
import aioredis
import json

class DistributedEmailQueue:

    QUEUE_KEY    = "rfqa:email:queue"
    RATE_KEY     = "rfqa:email:rate:{hour}"
    MAX_PER_HOUR = 1000

    def __init__(self, redis: "aioredis.Redis"):
        self._redis = redis

    async def enqueue(self, email: GeneratedEmail, priority: int = 5) -> None:
        item = json.dumps({
            "rfq_id":      email.rfq_id,
            "supplier_id": email.supplier_id,
            "to":          email.to,
            "subject":     email.subject,
            "body_html":   email.body_html,
            "priority":    priority,
        })
        await self._redis.zadd(self.QUEUE_KEY, {item: -priority})

    async def dequeue(self) -> dict | None:
        """Pop highest-priority item if under hourly rate limit."""
        hour_key = self.RATE_KEY.format(hour=datetime.utcnow().strftime("%Y%m%d%H"))
        rate     = await self._redis.incr(hour_key)
        if rate == 1:
            await self._redis.expire(hour_key, 3600)
        if rate > self.MAX_PER_HOUR:
            return None

        items = await self._redis.zpopmin(self.QUEUE_KEY, count=1)
        if not items:
            return None
        return json.loads(items[0][0])
```

---

## 20. Risks

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|--------------------|-------|-----------|
| R01 | Agent wysyła email do błędnego dostawcy (hallucination) | MEDIUM | HIGH | Supplier ID zawsze weryfikowany w DB przed emailem; HITL przy nowych dostawcach |
| R02 | Scraping zablokowany przez Cloudflare / bot detection | HIGH | MEDIUM | Rotating user-agents, stealth playwright, fallback do manual |
| R03 | LLM parsuje ofertę błędnie (fałszywa cena) | MEDIUM | HIGH | parse_confidence < 0.60 → mandatory HITL; cross-check z regex |
| R04 | Anti-spam cooldown zbyt restrykcyjny → brak ofert | MEDIUM | MEDIUM | Configurable cooldown per material category; RFQA_OPS override |
| R05 | Prompt injection przez dostawcę w emailu odpowiedzi | LOW | HIGH | Parse prompt sandboxed: brak dostępu do narzędzi; LLM nie wykonuje instrukcji z treści emaila |
| R06 | HITL timeout → automatyczne anulowanie RFQ | MEDIUM | HIGH | Escalation po 12h do senior buyer; configurable auto-action per type |
| R07 | Portal credentials wyciekły | LOW | CRITICAL | Fernet encryption at rest, HSM key storage, 90-day rotation |
| R08 | LLM model unavailability (Anthropic API outage) | LOW | HIGH | Retry 3× exponential; fallback do formularzowego emaila (no LLM) |
| R09 | FX rate stale → błędna normalizacja | MEDIUM | MEDIUM | ECB fallback; flag w normalizacji gdy rate > 1h; manual override |
| R10 | Anomalny dostawca oferuje cenę <30% target (fraud) | LOW | HIGH | Price anomaly rule → BLOCK + audit log + HITL; supplier flagged for review |
| R11 | Agent w pętli nieskończonej (max_iterations=50 nie wystarczy) | LOW | MEDIUM | Hard limit 50 iteracji; circuit breaker; cost monitoring alert |
| R12 | GDPR: email adresy dostawców przetwarzane bez podstawy | MEDIUM | HIGH | Legitimate interest / contract; data retention policy 2 lata; masking w logach |
| R13 | Zbyt agresywny web scraping → IP ban lub kary prawne | MEDIUM | MEDIUM | Rate limiting 1 req/s, robots.txt compliance, ToS review per portal |
| R14 | PostgreSQL single-point dla HITL queue | LOW | HIGH | PG HA z Patroni, automatic failover < 30s |
| R15 | Błędna decyzja HITL zatwierdza ofertę powyżej budżetu | LOW | HIGH | Post-HITL budget re-validation w kodzie; HITL reviewer widzi limit budżetu |

---

## 21. Roadmap

### Faza 1: Foundation (S1–S8) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S1–S2 | Schema PG, domain model, anti-spam engine, supplier profiles DB, RBAC middleware |
| S3–S4 | SupplierDiscoveryEngine (internal DB + SIE + SCSE), EmailGenerator (Claude), EmailDispatcher |
| S5–S6 | EmailResponseParser (Claude extraction), OfferNormalizer (FX + incoterms + payment terms) |
| S7–S8 | DecisionEngine + DecisionRuleEngine, PricingComparator, PriceDBUpdater, HITLGateway |

**Deliverables:** Manualny cykl RFQ (human triggering, agent executing), podstawowe HITL, email dispatch z anti-spam

### Faza 2: Autonomous Agent (S9–S16) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S9–S10 | ReAct Agent Core (Claude claude-opus-4-8 tool use), ToolRegistry, RiskController (5 warstw) |
| S11–S12 | SupplierPortalScraper (Playwright: Thomasnet, Europages, Kompass) |
| S13–S14 | AuditLogger, AgentWorkerPool, NotificationService (Slack + Email) |
| S15–S16 | Kafka event system (10 tematów + Avro), outbox publisher, CEE/SCSE/SIE integrations |

**Deliverables:** Autonomiczny cykl end-to-end, scraping 3 portale, full event bus

### Faza 3: Intelligence (S17–S24) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S17–S18 | Negocjacje automatyczne (follow-up + negotiation email templates) |
| S19–S20 | ML scoring: LightGBM supplier win predictor (na danych historycznych CHE) |
| S21–S22 | Multi-language support (8 języków), automatic language detection |
| S23–S24 | Advanced analytics dashboard, market price benchmarking, savings reports |

**Deliverables:** Negocjacje AI, ML-scored supplier ranking, 8 języków, analytics portal

### Faza 4: Scale & Compliance (S25–S32) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S25–S26 | EDI integration (PEPPOL BIS 3.0, ANSI X12 850), ERP connector (SAP MM/Ariba) |
| S27–S28 | Multi-tenant architecture, per-tenant anti-spam policies, tenant isolation |
| S29–S30 | GDPR compliance audit, data retention automation, right-to-erasure workflows |
| S31–S32 | GPU LLM inference (on-premise option), batch RFQ optimization, A/B prompt testing |

**Deliverables:** Enterprise ERP integration, multi-tenant, GDPR-certified, GPU-accelerated

### Docelowe KPIs (po fazie 2)

| KPI | Cel |
|-----|-----|
| Czas cyklu RFQ (koniec do końca) | < 48h (vs 5–10 dni manualnie) |
| Auto-approval rate | > 60% cykli |
| Email reply rate | > 35% |
| Średnie oszczędności vs target | > 5% |
| HITL timeout rate | < 10% |
| Scraping success rate | > 70% per portal |
| API P95 latency | < 500ms |
| Agent token cost per RFQ | < $2.00 USD |
