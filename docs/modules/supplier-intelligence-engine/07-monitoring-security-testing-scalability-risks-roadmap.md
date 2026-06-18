# Supplier Intelligence Engine — Monitoring, Security, Testing, Scalability, Risks & Roadmap

## 19. Monitoring

### Prometheus Metrics

```python
from prometheus_client import Counter, Histogram, Gauge

# API
sie_api_requests_total = Counter(
    "sie_api_requests_total",
    "Total API requests", ["method", "endpoint", "status_code"]
)
sie_api_latency_seconds = Histogram(
    "sie_api_latency_seconds",
    "API response latency", ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0]
)

# Scorecard
sie_scorecard_calculations_total = Counter(
    "sie_scorecard_calculations_total",
    "Scorecard calculations", ["trigger", "status"]
)
sie_scorecard_duration_seconds = Histogram(
    "sie_scorecard_duration_seconds",
    "Time to calculate a scorecard"
)

# Supplier counts
sie_suppliers_total = Gauge(
    "sie_suppliers_total",
    "Total suppliers by status and tier", ["status", "tier"]
)
sie_suppliers_rating_a = Gauge(
    "sie_suppliers_rating_a",
    "Number of suppliers with rating class A"
)
sie_critical_alerts_open = Gauge(
    "sie_critical_alerts_open",
    "Open critical/high risk alerts"
)

# Quality
sie_avg_ppm_by_tier = Gauge(
    "sie_avg_ppm_by_tier",
    "Average PPM per strategic tier", ["tier"]
)
sie_open_ncr_total = Gauge(
    "sie_open_ncr_total",
    "Open NCRs by severity", ["severity"]
)

# Delivery
sie_avg_otd_pct = Gauge(
    "sie_avg_otd_pct",
    "Average OTD % across approved suppliers"
)

# Pricing
sie_price_offers_expiring_7d = Gauge(
    "sie_price_offers_expiring_7d",
    "Price offers expiring in next 7 days"
)
sie_certifications_expiring_30d = Gauge(
    "sie_certifications_expiring_30d",
    "Certifications expiring in next 30 days"
)

# AI / Embeddings
sie_embedding_refresh_duration_seconds = Histogram(
    "sie_embedding_refresh_duration_seconds",
    "Time to generate and store an embedding"
)
sie_semantic_search_latency_seconds = Histogram(
    "sie_semantic_search_latency_seconds",
    "Semantic search query latency",
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0]
)

# Kafka
sie_kafka_events_published_total = Counter(
    "sie_kafka_events_published_total",
    "Kafka events published", ["topic"]
)
sie_kafka_consumer_lag = Gauge(
    "sie_kafka_consumer_lag",
    "Kafka consumer lag", ["consumer_group", "topic"]
)
```

### Alertmanager Rules

```yaml
groups:
  - name: sie_critical
    rules:
      - alert: SIEAPIHighErrorRate
        expr: |
          rate(sie_api_requests_total{status_code=~"5.."}[5m]) /
          rate(sie_api_requests_total[5m]) > 0.05
        for: 3m
        labels: {severity: critical, team: procurement-platform}
        annotations:
          summary: "SIE API error rate > 5%"

      - alert: SIEAPISlowP95
        expr: |
          histogram_quantile(0.95, rate(sie_api_latency_seconds_bucket[5m])) > 1.0
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "SIE API P95 latency > 1s"

      - alert: SIECriticalRiskAlertsHigh
        expr: sie_critical_alerts_open > 10
        for: 1m
        labels: {severity: warning, team: procurement-director}
        annotations:
          summary: "More than 10 critical risk alerts open in SIE"

      - alert: SIEEmbeddingRefreshStalled
        expr: |
          rate(sie_embedding_refresh_duration_seconds_count[30m]) == 0
        for: 30m
        labels: {severity: warning}
        annotations:
          summary: "SIE embedding refresh has stalled for 30 minutes"

      - alert: SIEKafkaConsumerLagHigh
        expr: sie_kafka_consumer_lag > 5000
        for: 5m
        labels: {severity: warning}
        annotations:
          summary: "SIE Kafka consumer lag > 5000 messages"

      - alert: SIECertificationsExpiring
        expr: sie_certifications_expiring_30d > 0
        for: 1m
        labels: {severity: info, team: supplier-quality}
        annotations:
          summary: "{{ $value }} supplier certifications expiring in 30 days"

      - alert: SIEPriceOffersExpiring
        expr: sie_price_offers_expiring_7d > 5
        for: 1m
        labels: {severity: info, team: procurement-buyers}
        annotations:
          summary: "{{ $value }} price offers expiring in 7 days"

      - alert: SIENoTier1Suppliers
        expr: sie_suppliers_rating_a < 3
        for: 5m
        labels: {severity: warning, team: procurement-director}
        annotations:
          summary: "Less than 3 Rating-A suppliers in the system"
```

### Grafana Dashboards

| Dashboard | Panels |
|-----------|--------|
| **SIE Overview** | Suppliers by status/tier, Rating distribution, Open alerts heatmap, OTD trend, PPM trend |
| **Scorecard Analytics** | Score distribution, Score vs prior period, Top movers, Bottom quartile watchlist |
| **Risk Monitoring** | Open alerts by severity/category, Country risk map, Concentration HHI per category |
| **Quality Tracker** | PPM by supplier/period, NCR waterfall (open/raised/closed), 8D closure SLA |
| **Delivery Performance** | OTD %, OTIF %, Avg delay days, Delay category distribution |
| **Pricing Intelligence** | Price vs benchmark heatmap, Expiring offers, Price trend by material class |
| **AI Layer** | Embedding refresh lag, Semantic search P95, Recommendation engine usage, Anomaly count |
| **API & Infrastructure** | Request rate, Error rate, P50/P95/P99 latency, Kafka lag, Cache hit rate |

### Health Check Endpoint

```json
GET /health
{
  "status": "healthy",
  "version": "1.2.0",
  "checks": {
    "database":    {"status": "healthy", "latency_ms": 2},
    "kafka":       {"status": "healthy", "consumer_lag": 12},
    "redis":       {"status": "healthy", "hit_rate_pct": 87},
    "embedding":   {"status": "healthy", "last_refresh_min": 4},
    "risk_feeds":  {"status": "healthy", "last_sync": "2026-06-18T06:00:00Z"}
  },
  "suppliers_approved": 1247,
  "critical_alerts_open": 2
}
```

---

## 20. Security

### RBAC Roles

| Rola | Uprawnienia |
|------|------------|
| `SUPPLIER_VIEWER` | Read approved suppliers, scorecards, certifications, capabilities |
| `SUPPLIER_MANAGER` | Full CRUD on suppliers, certifications; raise/close NCRs; record deliveries |
| `PRICE_MANAGER` | Full CRUD on price offers, benchmarks; read all suppliers |
| `VENDOR_RISK_ANALYST` | Read/write risk profiles, alerts; read financial profiles |
| `PROCUREMENT_DIRECTOR` | All read access; approve/suspend suppliers; acknowledge risk alerts |
| `SIE_ADMIN` | Full access to all SIE resources; role management |
| `SYSTEM_INTEGRATOR` | Service-to-service via mTLS; full API read/write |

### Row-Level Security

```sql
-- SUPPLIER_VIEWER: only approved suppliers
CREATE POLICY sie_viewer_policy ON suppliers
    FOR SELECT
    USING (
        status IN ('APPROVED','CONDITIONAL') OR
        current_setting('app.user_role', TRUE) IN (
            'SUPPLIER_MANAGER','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR'
        )
    );

-- PRICE_MANAGER: full price access; others see only their category
CREATE POLICY sie_price_policy ON price_offers
    FOR ALL
    USING (
        current_setting('app.user_role', TRUE) IN (
            'PRICE_MANAGER','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR'
        ) OR
        category_id::TEXT = current_setting('app.category_id', TRUE)
    );

-- Financial profiles: restricted to risk analysts and directors
CREATE POLICY sie_financial_policy ON financial_profiles
    FOR SELECT
    USING (
        current_setting('app.user_role', TRUE) IN (
            'VENDOR_RISK_ANALYST','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR'
        )
    );

-- Risk alerts: only risk analysts and procurement director can manage
CREATE POLICY sie_risk_alert_policy ON risk_alerts
    FOR ALL
    USING (
        current_setting('app.user_role', TRUE) IN (
            'VENDOR_RISK_ANALYST','PROCUREMENT_DIRECTOR','SIE_ADMIN','SYSTEM_INTEGRATOR'
        )
    );
```

### Security Controls

| Layer | Control |
|-------|---------|
| Transport | TLS 1.3 everywhere; mTLS for service-to-service |
| Authentication | JWT RS256 (15min TTL) + refresh tokens; OAuth2 / OIDC via Keycloak |
| Authorization | RBAC + RLS policies (see above) |
| API Gateway | Rate limiting (100 req/min per token); WAF (OWASP ruleset) |
| Data encryption | AES-256-GCM at rest (financial data, D&B scores); TDE via PostgreSQL |
| Sensitive fields | DUNS masked in logs; financial data excluded from audit log JSONB |
| Audit | Immutable audit_log table; all INSERT/UPDATE/DELETE captured with user context |
| Sanctions screening | OFAC/EU/UK list checked on supplier registration and daily refresh |
| Input validation | Strict OpenAPI schema validation; parameterized queries only; no dynamic SQL |
| Dependency scanning | Trivy in CI/CD; SBOM generated per release |

---

## 21. Testing

### Unit Tests

```python
import pytest
from datetime import date

class TestScorecardCalculator:
    calc = ScorecardCalculator()

    @pytest.mark.parametrize("ppm,expected", [
        (0, 40.0), (9, 40.0), (10, 38.0), (99, 34.0),
        (500, 20.0), (5001, 5.0),
    ])
    def test_ppm_score(self, ppm, expected):
        assert self.calc._score_ppm(ppm) == expected

    def test_composite_weights_sum_to_one(self):
        assert abs(sum(self.calc.WEIGHTS.values()) - 1.0) < 1e-9

    def test_composite_score_bounds(self):
        inp = ScorecardInput(
            supplier_id="test", period_start=date(2025, 1, 1), period_end=date(2025, 3, 31),
            ppm=0, ncr_count=0, ncr_critical_count=0, open_8d_overdue_count=0,
            active_cert_count=5, required_cert_count=5,
            otd_pct=99, otif_pct=98, avg_delay_days=0, lead_time_reliability_pct=98,
            price_vs_benchmark_pct=-10, price_trend_yoy_pct=-3, price_stability_cv=0.03,
            avg_response_hours=2, avg_8d_closure_days=10, portal_compliance_pct=100,
            escalation_count=0,
            financial_risk_score=90, geopolitical_risk_score=85, concentration_risk_score=80,
        )
        result = self.calc.calculate(inp)
        assert 0 <= result.composite_score <= 100
        assert result.rating_class == RatingClass.A

    def test_suspended_supplier_gets_f_on_low_score(self):
        assert self.calc._classify(20.0) == RatingClass.F

class TestAltmanZScore:
    calc = AltmanZScoreCalculator()

    def test_safe_zone(self):
        fs = FinancialStatement(
            supplier_id="x", fiscal_year=2024,
            working_capital=500_000, total_assets=1_000_000,
            retained_earnings=300_000, ebit=150_000,
            book_value_equity=600_000, total_liabilities=400_000,
            revenue=2_000_000, net_income=120_000,
            current_ratio=2.1, quick_ratio=1.5,
            debt_to_equity=0.67, interest_coverage=8.0,
        )
        result = self.calc.calculate(fs)
        assert result.zone == "SAFE"
        assert result.z_score > 2.9

    def test_zero_assets_raises(self):
        with pytest.raises(ValueError):
            self.calc.calculate(FinancialStatement(
                supplier_id="x", fiscal_year=2024,
                working_capital=0, total_assets=0,
                retained_earnings=0, ebit=0,
                book_value_equity=0, total_liabilities=1,
                revenue=0, net_income=0,
                current_ratio=0, quick_ratio=0,
                debt_to_equity=0, interest_coverage=0,
            ))

class TestConcentrationAnalyzer:
    analyzer = ConcentrationAnalyzer()

    def test_monopoly_is_high_risk(self):
        result = self.analyzer.analyze("cat-1", {"supplier-A": 100.0})
        assert result.herfindahl_index == 1.0
        assert result.risk_level == "HIGH"

    def test_perfect_distribution(self):
        spend = {f"s-{i}": 10.0 for i in range(10)}
        result = self.analyzer.analyze("cat-2", spend)
        assert result.herfindahl_index == pytest.approx(0.1, abs=0.01)
        assert result.risk_level == "LOW"
```

### Integration Tests (Testcontainers)

```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.kafka import KafkaContainer

@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        pg.exec(f"psql -U test -c 'CREATE EXTENSION vector; CREATE EXTENSION pg_trgm;'")
        yield pg

@pytest.fixture(scope="session")
def kafka():
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as k:
        yield k

class TestSupplierRepository:
    def test_create_and_retrieve_supplier(self, postgres):
        repo = SupplierRepository(postgres.get_connection_url())
        supplier_id = repo.create(SupplierCreateRequest(
            legal_name="Test Manufacturer GmbH",
            supplier_type="MANUFACTURER",
            country_code="DE",
        ))
        s = repo.get(supplier_id)
        assert s.legal_name == "Test Manufacturer GmbH"
        assert s.status == "PENDING"

    def test_semantic_search_returns_results(self, postgres):
        engine = SemanticSearchEngine(mock_embedding_svc, postgres.get_connection_url())
        results = engine.search(
            "CNC machining specialist aluminum parts",
            filters=SearchFilters(status_filter=["APPROVED"]),
            top_k=5,
        )
        assert len(results) <= 5
        for r in results:
            assert r.similarity_score >= 0.70

class TestScorecardKafkaIntegration:
    def test_delivery_recorded_triggers_scorecard(self, postgres, kafka):
        producer = DeliveryEventProducer(kafka.get_bootstrap_server())
        producer.publish(DeliveryRecordedEvent(
            supplier_id="sup-001",
            delay_days=0,
            is_on_time=True,
            is_in_full=True,
        ))
        # Consumer should process and trigger scorecard recalculation
        consumer = ScorecardCalcConsumer(kafka.get_bootstrap_server(), postgres.get_connection_url())
        consumer.process_once(timeout=10)

        scorecard = ScorecardRepository(postgres.get_connection_url()).get_latest("sup-001")
        assert scorecard is not None
```

### Contract Tests (Pact)

```python
from pact import Consumer, Provider

def test_sie_contract_for_rfq_engine():
    pact = Consumer("RFQEngine").has_pact_with(Provider("SIE"))

    pact.given("Approved supplier exists with ID sup-001") \
        .upon_receiving("a request for supplier scorecard") \
        .with_request("GET", "/sie/v1/suppliers/sup-001/scorecards") \
        .will_respond_with(200, body={
            "items": [
                {
                    "composite_score": Like(75.0),
                    "rating_class":    Like("B"),
                    "period_end":      Like("2025-12-31"),
                }
            ]
        })
    with pact:
        result = RFQClient("http://localhost").get_supplier_scorecard("sup-001")
        assert result[0]["rating_class"] in ["A","B","C","D","E","F"]
```

### Test Matrix

| Typ testu | Narzędzie | Cel | SLA |
|-----------|-----------|-----|-----|
| Unit | pytest | Score algorithms, risk models, Z-score | 100% pass, >90% coverage |
| Integration | Testcontainers + pytest | DB queries, Kafka consumers, embedding upsert | 100% pass |
| Contract | Pact | SIE ↔ RFQ, SIE ↔ Cost Calc, SIE ↔ MIE | 100% pass |
| API | pytest + httpx | All endpoints, edge cases, auth scenarios | 100% pass |
| Load | k6 | P95 < 500ms, 500 RPS sustained | P95 < 500ms |
| Security | OWASP ZAP | OWASP Top 10, auth bypass | 0 critical findings |
| Data quality | great_expectations | Schema, null checks, score bounds | 100% expectations |
| E2E | Playwright (optional) | Procurement buyer workflow | Key flows green |

### k6 Load Test

```javascript
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "1m", target: 100 },
    { duration: "3m", target: 500 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<500"],
    http_req_failed:   ["rate<0.01"],
  },
};

const BASE = "https://api.ici.internal/sie/v1";

export default function () {
  const suppliers = http.get(`${BASE}/suppliers?status=APPROVED&page_size=20`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
  check(suppliers, { "list 200": (r) => r.status === 200 });

  const ranking = http.get(`${BASE}/scorecards/ranking?category_id=${__ENV.CATEGORY_ID}`, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
  check(ranking, { "ranking 200": (r) => r.status === 200 });

  sleep(0.2);
}
```

---

## 22. Scalability

### Caching Strategy

| Cache Key Pattern | TTL | Invalidated By |
|-------------------|-----|----------------|
| `sie:supplier:{id}` | 10 min | SupplierUpdated event |
| `sie:scorecard:latest:{id}` | 30 min | ScorecardUpdated event |
| `sie:scorecard:history:{id}` | 60 min | ScorecardUpdated event |
| `sie:ranking:{category_id}` | 15 min | ScorecardUpdated event (any supplier in category) |
| `sie:risk:{id}` | 60 min | RiskAlertRaised / RiskAlertResolved event |
| `sie:price:active:{supplier_id}:{material_id}` | 5 min | PriceOfferReceived / PriceExpired |
| `sie:certs:{supplier_id}` | 60 min | CertificationAdded / CertificationExpired |
| `sie:search:fts:{hash}` | 5 min | Time-based TTL only |

```python
class SIECacheManager:
    def __init__(self, redis_client):
        self.r = redis_client

    def invalidate_supplier(self, supplier_id: str):
        keys = [
            f"sie:supplier:{supplier_id}",
            f"sie:scorecard:latest:{supplier_id}",
            f"sie:scorecard:history:{supplier_id}",
            f"sie:risk:{supplier_id}",
            f"sie:certs:{supplier_id}",
        ]
        pipe = self.r.pipeline()
        for k in keys:
            pipe.delete(k)
        pipe.execute()

    def invalidate_ranking(self, category_id: str):
        self.r.delete(f"sie:ranking:{category_id}")
```

### Scalability Tiers

| Tier | Suppliers | RPS | Architecture |
|------|-----------|-----|-------------|
| Small | < 5,000 | < 100 | Single PostgreSQL + Redis + API pod |
| Medium | 5k–50k | 100–500 | PostgreSQL primary + 2 read replicas, Redis cluster, 3 API pods |
| Large | 50k+ | 500+ | Citus (sharded by supplier_id), Redis cluster, 5+ API pods, ClickHouse for analytics |

### PostgreSQL Read Replica Routing

```python
class DatabaseRouter:
    def __init__(self, primary_url: str, replica_urls: list[str]):
        self.primary  = create_engine(primary_url, pool_size=10, max_overflow=20)
        self.replicas = [create_engine(url, pool_size=5) for url in replica_urls]
        self._idx = 0

    def get_write(self):
        return self.primary

    def get_read(self):
        engine = self.replicas[self._idx % len(self.replicas)]
        self._idx += 1
        return engine
```

### HNSW Index Tuning

```sql
-- For > 500k supplier embeddings:
DROP INDEX IF EXISTS idx_embeddings_hnsw;
CREATE INDEX idx_embeddings_hnsw ON supplier_intelligence.supplier_embeddings
    USING hnsw (embedding_vector vector_cosine_ops)
    WITH (m = 32, ef_construction = 128);

SET hnsw.ef_search = 100;    -- balance recall vs speed
```

---

## 23. Risk Register

| # | Ryzyko | P | I | Mitygacja |
|---|--------|---|---|-----------|
| R1 | Brak danych finansowych dla MSP (D&B gap) | W | S | Alternatywne źródła: branżowe rejestry kredytowe; pytanie w onboardingu |
| R2 | Opóźnienie danych z ERP (GR/PO sync lag) | Ś | S | Asynchroniczne pobieranie z kolejką Dead Letter; alerty na opóźnienia >2h |
| R3 | Fałszywe alarmy ryzyka geopolitycznego | W | Ś | Threshold tuning; human-in-the-loop confirmation dla HIGH/CRITICAL |
| R4 | Zbyt mała liczba aktywnych dostawców = niska jakość rankingu | Ś | W | Minimum 3 dostawców per kategoria przed uruchomieniem rankingu |
| R5 | Embedding drift (stare embeddingi nie odzwierciedlają zmian) | Ś | Ś | Forced refresh co 30 dni; event-driven refresh na każdą zmianę scorecarda |
| R6 | Koncentracja ryzyka: dostawca TIER1 z niskim Z-score | N | K | Automatyczny alert + kwartalny przegląd finansowy TIER1 |
| R7 | Sankcje wykryte po onboardingu | N | K | Dzienny screening OFAC/EU/UK; block w ERP przez event |
| R8 | Manipulacja scorecardem przez nieprawdziwe dane GR | N | S | Reconciliation z ERP invoice matching; anomaly detection na outliers |
| R9 | Awaria zewnętrznego feed'a cenowego (LME/D&B) | Ś | Ś | Cache'owane dane z TTL 7 dni; fallback do ostatniej ceny |
| R10 | Niedostępność pgvector podczas szczytu (semantic search) | N | Ś | Fallback na FTS; graceful degradation w API |
| R11 | GDPR — dane kontaktów dostawców | W | Ś | Retention policy 3 lata po zakończeniu współpracy; right-to-erasure flow |
| R12 | Brak tłumaczenia dokumentów certyfikacyjnych (non-EU) | Ś | Ś | Wymagany upload w EN lub DE; automatyczne sprawdzenie nazwy pliku |
| R13 | Scorecard favoritism (buyer manipulates service score) | N | Ś | Double-validation dla service_score changes >20pts; manager approval |
| R14 | Kafka topic zapis failure podczas scorecard update | N | W | Outbox pattern; at-least-once delivery + idempotent consumers |

**P = Prawdopodobieństwo: N=Niskie, Ś=Średnie, W=Wysokie**
**I = Wpływ: N=Niski, Ś=Średni, S=Silny, K=Krytyczny**

---

## 24. Roadmap

### Faza 1 — MVP (Miesiąc 1–3)

| Sprint | Zakres |
|--------|--------|
| S1 | Core domain: Supplier entity, status machine, CRUD API, PostgreSQL schema |
| S2 | Certifications, Capabilities, Contacts management |
| S3 | Delivery recording (GR events), OTD/OTIF calculation |
| S4 | Quality records, NCR lifecycle (raise → 8D → close) |
| S5 | Price offers CRUD, active price lookup |
| S6 | Scorecard calculator (5-component), rating class assignment |

**Deliverable MVP:** Supplier registry z zarządzaniem certyfikatami, rejestrowaniem dostaw, NCR i podstawowym scorecardem.

---

### Faza 2 — Core Intelligence (Miesiąc 4–6)

| Sprint | Zakres |
|--------|--------|
| S7 | Financial profile entity, Altman Z-Score calculator |
| S8 | D&B API connector, PAYDEX→risk_score mapper |
| S9 | Geopolitical risk: Coface connector, country risk scoring |
| S10 | Risk profile aggregate, RiskScoringEngine, alert generation |
| S11 | Concentration risk analyzer, HHI calculation |
| S12 | Sanctions screening (OFAC/EU/UK daily refresh) |
| S13 | Supplier ranking view per category, tier assignment algorithm |
| S14 | Kafka events (all 15 topics), ERP sync consumers |
| S15 | Redis caching layer, cache invalidation on domain events |
| S16 | RBAC + RLS policies, JWT/mTLS, audit log |

**Deliverable Core Intelligence:** Pełen system oceny ryzyka, rankingu, compliance, integracja z ERP.

---

### Faza 3 — AI Layer (Miesiąc 7–9)

| Sprint | Zakres |
|--------|--------|
| S17 | SupplierEmbeddingService, text-embedding-3-small integration |
| S18 | pgvector HNSW index, embedding upsert pipeline |
| S19 | SemanticSearchEngine, hybrid search (FTS + vector + RRF) |
| S20 | EmbeddingRefreshConsumer, event-driven refresh triggers |
| S21 | SupplierRecommendationEngine (hard filters + multi-criteria scoring) |
| S22 | Diversity bonus, geographic re-ranking |
| S23 | SupplierContextBuilder (cost_calculation / rfq / risk_decision) |
| S24 | SupplierAnomalyDetector (Isolation Forest), anomaly alert pipeline |
| S25 | ML feature store (34 features), feature extraction pipeline |
| S26 | AI endpoints: /search/semantic, /ai/similar-suppliers, /ai/recommend-suppliers |

**Deliverable AI Layer:** Semantic search, AI-powered recommendations, anomaly detection.

---

### Faza 4 — Scale & Excellence (Miesiąc 10–12)

| Sprint | Zakres |
|--------|--------|
| S27 | PostgreSQL read replicas, DatabaseRouter, connection pool tuning |
| S28 | ClickHouse integration for supplier analytics (score history, trend analytics) |
| S29 | HNSW tuning (m=32, ef=128) for 500k+ embeddings |
| S30 | k6 load testing, P95 < 500ms validation, bottleneck resolution |
| S31 | Supplier portal integration (self-service cert upload, contact management) |
| S32 | Procurement Scorecard UI (Grafana dashboard + embedded charts) |
| S33 | ESG module: carbon footprint tracking, labor compliance flags |
| S34 | Supplier development program: corrective action plans, KPI targets |
| S35 | Advanced forecasting: PPM forecast (Prophet), delivery risk prediction (XGBoost) |
| S36 | Multi-tenant support: plant-level supplier approval workflows |

**Deliverable Scale & Excellence:** Enterprise-grade scalability, ESG, supplier development, forecasting.

---

### Długoterminowe inicjatywy (12m+)

| Inicjatywa | Opis |
|-----------|------|
| **Supplier Digital Twin** | Real-time capacity & operational simulation for TIER1 partners |
| **Predictive Switching** | ML model recommending proactive dual-sourcing before disruption |
| **Blockchain Cert Verification** | Tamper-proof certificate registry on distributed ledger |
| **Supplier Carbon Passport** | Scope 3 emission tracking per supplier, aligned with CSRD |
| **Marketplace Integration** | Connect to B2B platforms (Europages, Alibaba, Thomasnet) for new supplier discovery |
| **Autonomous RFQ Scoring** | GPT-4 powered RFQ evaluation with supplier context injection |
