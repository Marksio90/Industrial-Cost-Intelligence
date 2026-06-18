# Material Intelligence Engine — Security, Testing, Scalability, Risks, Roadmap

## 21. Security

### Model uprawnień (RBAC)

```
Role: MATERIAL_VIEWER
  - GET /materials/**
  - GET /search/**
  - GET /taxonomy/**

Role: MATERIAL_EDITOR
  - All VIEWER permissions
  - POST /materials
  - PUT /materials/{id}
  - PATCH /materials/{id}
  - PUT /materials/{id}/properties
  - PUT /materials/{id}/mechanical-properties

Role: PRICE_MANAGER
  - All VIEWER permissions
  - POST /materials/{id}/prices
  - DELETE /materials/{id}/prices/{price_id}

Role: SUBSTITUTION_MANAGER
  - All VIEWER permissions
  - POST /materials/{id}/substitutions
  - PUT /materials/{id}/substitutions/{id}
  - DELETE /materials/{id}/substitutions/{id}

Role: MATERIAL_ADMIN
  - All permissions
  - DELETE /materials/{id} (deactivation)
  - POST /taxonomy
  - PUT /taxonomy/{id}
  - Admin endpoints

Role: SYSTEM_INTEGRATOR (ERP/AI/RFQ)
  - GET /materials/**
  - GET /search/**
  - POST /search/semantic
  - POST /materials/{id}/cost
  - GET /materials/{id}/compatibility/{process_code}
  - Rate limit: 1000 req/min
```

### Implementacja bezpieczeństwa

| Warstwa | Mechanizm |
|---------|-----------|
| Autentykacja | JWT (RS256) + API Key dla integracji systemowych |
| Autoryzacja | RBAC na poziomie endpoint (Spring Security / FastAPI depends) |
| Transport | TLS 1.3 mandatory, HSTS |
| Input validation | Request schema validation (Pydantic / Jakarta Bean Validation) |
| SQL injection | Parameterized queries ONLY — nie ma dynamic SQL z user input |
| Rate limiting | 100 req/min (viewer), 50 req/min (write), 1000 req/min (system) |
| Audit log | Każdy CREATE/UPDATE/DELETE logowany z user_id, IP, timestamp |
| Data masking | Supplier price records: internal_cost_eur masked for VIEWER role |
| Secret management | Database credentials via HashiCorp Vault / K8s Secrets |
| Dependency scanning | Snyk/Dependabot w CI pipeline |

### Ochrona danych

```python
# Pola wrażliwe w API — maskowane w odpowiedziach dla niskich ról
SENSITIVE_FIELDS = {
    'MATERIAL_VIEWER': [
        'internal_notes',          # Ukryte dla viewerów
        'supplier_unit_price',     # Ceny od dostawców — tylko dla PRICE_MANAGER+
        'certification_cost_eur_kg',  # Koszty wewnętrzne
    ]
}

# Row-level security — supplier prices visible only to users in supplier's access group
# Implemented via PostgreSQL RLS policies:

CREATE POLICY supplier_price_access ON supplier_price_records
    USING (
        current_user_has_role('PRICE_MANAGER')
        OR
        current_user_has_supplier_access(supplier_id)
    );
```

### Compliance

| Wymaganie | Implementacja |
|-----------|--------------|
| GDPR | Audit log retencja 3 lata, dane osobowe ograniczone do created_by (UUID), prawo do eksportu |
| REACH/RoHS | Flagi regulatory_flags w encji Material, eksport do ERP |
| Separacja środowisk | DEV/STAGING/PROD osobne credentiale, osobne Kafka clusters |
| Backup | Codzienne snapshoty PostgreSQL (Point-in-time recovery 30 dni) |
| Penetration testing | Roczny pentest + SAST w CI (SonarQube) |

---

## 22. Test Strategy

### Piramida testów

```
                    /\
                   /E2E\         5% — Testy end-to-end (Postman/Newman, 20 scenariuszy)
                  /──────\
                 /Integra-\      25% — Testy integracyjne (Testcontainers, real PostgreSQL)
                /  tion    \
               /────────────\
              /    Unit      \   70% — Testy jednostkowe (pytest/JUnit, in-memory)
             /────────────────\
```

### Testy jednostkowe (coverage ≥ 85%)

```python
# Przykłady testów jednostkowych

class TestSubstitutionScorer:
    def test_direct_substitution_s355_replaces_s235(self):
        s235 = MaterialFixtures.s235()
        s355 = MaterialFixtures.s355()
        scorer = SubstitutionScorer()
        result = scorer.score(s235, s355, SubstitutionContext(processes=['LASER_FIBER']))
        assert result.go_nogo == 'GO'
        assert result.total >= 70
        assert result.breakdown['mechanical_compatibility'] >= 80

    def test_forbidden_process_returns_nogo(self):
        abs_mat = MaterialFixtures.abs()
        pa6_mat = MaterialFixtures.pa6()
        # PA6 in MIG welding context — both FORBIDDEN
        scorer = SubstitutionScorer()
        result = scorer.score(abs_mat, pa6_mat,
                              SubstitutionContext(processes=['MIG_MAG']))
        assert result.go_nogo == 'NOGO'

    def test_lower_rm_candidate_returns_zero_mechanical_score(self):
        src = MaterialFixtures.s355()
        weak = MaterialFixtures.s235()  # Lower Rm
        scorer = SubstitutionScorer()
        result = scorer.score(src, weak, SubstitutionContext())
        assert result.breakdown['mechanical_compatibility'] == 0.0
        assert result.go_nogo == 'NOGO'


class TestPriceValidator:
    def test_within_bounds_ok(self):
        validator = PriceValidator()
        result = validator.validate(
            NormalizedPrice(1.20, date.today()),
            NormalizedPrice(1.15, date.today() - timedelta(days=1)),
            MaterialFixtures.s355()
        )
        assert result.status == 'OK'

    def test_exceeds_daily_limit_warning(self):
        validator = PriceValidator()
        result = validator.validate(
            NormalizedPrice(1.50, date.today()),  # +30% — exceeds 8%
            NormalizedPrice(1.15, date.today() - timedelta(days=1)),
            MaterialFixtures.s355()
        )
        assert result.status == 'WARNING'
        assert result.requires_manual_review == True


class TestDensityResolver:
    def test_returns_correct_density_for_s235(self):
        resolver = DensityResolver()
        density = resolver.get_density(MATERIAL_S235_ID)
        assert density == pytest.approx(7850, abs=5)

    def test_raises_exception_for_missing_density(self):
        resolver = DensityResolver()
        with pytest.raises(MaterialDataException):
            resolver.get_density(uuid4())  # Non-existent material
```

### Testy integracyjne (Testcontainers)

```python
@pytest.fixture(scope="session")
def postgres_db():
    with PostgreSqlContainer("postgres:16-alpine") as pg:
        engine = create_engine(pg.get_connection_url())
        run_migrations(engine)
        yield engine

class TestMaterialRepository:
    def test_create_and_retrieve_material(self, postgres_db):
        repo = MaterialRepository(postgres_db)
        material = CreateMaterialRequest(
            material_code="MET-S235-TEST",
            material_name="S235JR Test",
            material_class="METAL",
            category_id=METAL_CATEGORY_ID
        )
        created = repo.create(material)
        retrieved = repo.get_by_id(created.material_id)

        assert retrieved.material_code == "MET-S235-TEST"
        assert retrieved.status == "DRAFT"
        assert retrieved.version == 1

    def test_optimistic_locking_raises_on_stale_version(self, postgres_db):
        repo = MaterialRepository(postgres_db)
        material = repo.get_by_code("MET-S355-HR-6MM")
        # Simulate concurrent modification
        repo.update(material.material_id,
                    UpdateRequest(material_name="Updated", version=material.version))
        # Second update with same (now stale) version
        with pytest.raises(VersionConflictError):
            repo.update(material.material_id,
                        UpdateRequest(material_name="Conflict", version=material.version))

    def test_search_returns_relevant_results(self, postgres_db):
        service = MaterialSearchService(postgres_db)
        results = service.search("stainless 304 sheet")
        codes = [r.material_code for r in results]
        assert any("304" in c for c in codes)
        assert results[0].relevance_score > results[-1].relevance_score
```

### Testy kontraktu (Consumer-Driven Contract Testing)

```python
# Pact test — verifies MIE API contract with Cost Calculator
class TestMIEContractForCostCalculator:
    """
    Ensures MIE API responses match what Cost Calculator expects.
    Pact broker: https://pact.internal.company.com
    """

    def test_get_material_with_cost_fields(self, pact):
        pact.given("material S355 exists and is active")
            .upon_receiving("GET material for cost calculation")
            .with_request(method="GET", path="/v1/materials/cost-calc-test-id",
                          query={"include": "properties,cost_coefficients"})
            .will_respond_with(
                status=200,
                body={
                    "material_id": "cost-calc-test-id",
                    "material_code": like("MET-S355"),
                    "properties": {
                        "density_kg_m3": like(7850),
                        "unit_of_measure": like("KG"),
                    },
                    "cost_coefficients": {
                        "scrap_rate_pct": like(10.0),
                        "yield_rate_pct": like(90.0),
                    }
                }
            )
```

### Testy wydajnościowe (k6)

```javascript
// k6 load test: Material search under load
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    stages: [
        { duration: '30s', target: 20 },
        { duration: '1m',  target: 50 },
        { duration: '30s', target: 0 },
    ],
    thresholds: {
        http_req_duration: ['p(95)<500'],   // P95 < 500ms
        http_req_failed:   ['rate<0.01'],    // Error rate < 1%
    },
};

export default function () {
    const queries = [
        '/v1/materials/search?q=S355&material_class=METAL',
        '/v1/materials/search?q=ABS+granulat&material_class=POLYMER',
        '/v1/materials/search?q=MDF+18mm',
    ];
    const url = queries[Math.floor(Math.random() * queries.length)];
    const res = http.get(`https://api-staging.company.com${url}`, {
        headers: { Authorization: `Bearer ${__ENV.TEST_TOKEN}` }
    });
    check(res, { 'status 200': (r) => r.status === 200 });
    sleep(1);
}
```

### Test matrix

| Rodzaj testu | Narzędzie | Cel | CI trigger |
|--------------|-----------|-----|-----------|
| Unit | pytest / JUnit | ≥85% coverage | Każdy PR |
| Integration | Testcontainers | Kluczowe scenariusze DB | Każdy PR |
| API | Postman / Newman | Smoke + regression | Każdy deploy staging |
| Contract | Pact | MIE ↔ Cost Calc, ERP, RFQ | Każdy PR |
| Load | k6 | P95 < 500ms, error < 1% | Pre-release |
| Security | OWASP ZAP + SonarQube | OWASP Top10 | Pre-release |
| Mutation | mutmut / PIT | Jakość testów jednostkowych | Weekly |

---

## 23. Scalability

### Architektura skalowania

```
                    ┌──────────────────────────────────┐
                    │      Load Balancer (L7)           │
                    │      Nginx / AWS ALB              │
                    └─────────────┬────────────────────┘
                                  │
              ┌───────────────────┼──────────────────────┐
              │                   │                      │
        ┌─────▼──────┐    ┌──────▼──────┐    ┌─────────▼──────┐
        │ MIE API    │    │ MIE API     │    │ MIE API        │
        │ Instance 1 │    │ Instance 2  │    │ Instance N     │
        │ (stateless)│    │ (stateless) │    │ (stateless)    │
        └─────┬──────┘    └──────┬──────┘    └─────────┬──────┘
              └──────────────────┼───────────────────────┘
                                  │
              ┌───────────────────┼──────────────────────┐
              │                                          │
        ┌─────▼──────┐                          ┌───────▼──────┐
        │ PostgreSQL  │                          │ Redis Cache  │
        │  Primary   │◄──── Streaming Repl ────►│ (read-cache) │
        │ + pgvector  │                          │              │
        └─────┬──────┘                          └──────────────┘
              │
        ┌─────▼──────┐
        │ PostgreSQL  │
        │  Replicas  │ (N read replicas for heavy queries)
        │ (read-only) │
        └────────────┘
```

### Strategia cacheowania (Redis)

| Dane | TTL | Klucz | Invalidacja |
|------|-----|-------|-------------|
| Material detail + properties | 1h | `mie:mat:{id}:full` | MaterialUpdated event |
| Current price per material | 15 min | `mie:price:{id}:current` | PriceUpdated event |
| Taxonomy tree | 6h | `mie:taxonomy:tree` | TaxonomyUpdated event |
| Search results | 5 min | `mie:search:{hash(query+filters)}` | Auto-expire |
| Process compatibility | 4h | `mie:compat:{mat_id}` | CompatibilityUpdated event |
| Material cost coefficients | 1h | `mie:coeff:{id}` | MaterialUpdated event |

```python
class MaterialCacheService:
    def get_or_compute_material(self, material_id: str) -> MaterialDetail:
        key = f"mie:mat:{material_id}:full"
        cached = self.redis.get(key)
        if cached:
            return MaterialDetail.parse_raw(cached)

        material = self.db_repo.get_full(material_id)
        self.redis.setex(key, 3600, material.json())
        return material
```

### Poziomy skalowania

| Wolumen | Instancje API | DB config | Cache |
|---------|---------------|-----------|-------|
| ≤ 10k materiałów, ≤ 100 req/s | 2× API (2 vCPU) | 1 Primary (4 vCPU) | Redis 2GB |
| ≤ 100k materiałów, ≤ 1000 req/s | 4× API (4 vCPU) | 1P + 2 Replicas (8 vCPU) | Redis 8GB |
| ≤ 1M materiałów, ≤ 10k req/s | 8× API (8 vCPU) + auto-scale | 1P + 4R + PgBouncer | Redis Cluster 32GB |

### Partycjonowanie danych

```sql
-- Audit log partitioned by year (already in schema)
-- Price records partitioned by material_class for large datasets:

CREATE TABLE market_price_records_metal PARTITION OF market_price_records_partitioned
    FOR VALUES IN ('METAL');
CREATE TABLE market_price_records_polymer PARTITION OF market_price_records_partitioned
    FOR VALUES IN ('POLYMER', 'COMPOSITE');
CREATE TABLE market_price_records_other PARTITION OF market_price_records_partitioned
    FOR VALUES IN ('WOOD', 'PACKAGING', 'SPECIAL');
```

### HNSW index tuning dla dużych zbiorów embeddingów

```sql
-- For 1M+ materials, adjust HNSW parameters:
CREATE INDEX idx_embeddings_vector_hnsw ON material_embeddings
    USING hnsw (vector vector_cosine_ops)
    WITH (m = 32, ef_construction = 128);  -- Higher recall, more memory

-- Set ef_search for query time tradeoff:
SET hnsw.ef_search = 100;  -- Higher = better recall, slower
```

---

## 24. Risks

### Rejestr ryzyk

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|--------------------|-------|-----------|
| R01 | Niedostępność zewnętrznych źródeł cen (LME, Platts) | Średnie | Wysoki | Fallback na ostatnią znana cenę + alert; manual entry |
| R02 | Niskiej jakości dane historyczne (stare/brakujące ceny) | Wysokie | Średni | Import historii z ERP przy onboardingu; graceful degradation |
| R03 | Nieprawidłowe wartości gęstości → błędne kalkulacje kosztów | Niskie | Krytyczny | Walidacja zakresów per klasa; testy regresji kalkulacji |
| R04 | Niekompletna taksonomia — materiały nieprzypisane | Średnie | Średni | Kategoria "Uncategorized" jako fallback; proces re-kategoryzacji |
| R05 | Przestarzałe embeddingi po masowej aktualizacji danych | Średnie | Niski (AI) | Monitoring freshness; batch re-generation job (nocny) |
| R06 | Model ML prognozy cen z wysokim błędem MAPE | Wysokie | Średni | MAPE widoczne w API; fallback na trend liniowy |
| R07 | Single-source dependency materiałów krytycznych | Wysokie | Wysoki | Risk dashboard; alerty supply risk; diversification recommendations |
| R08 | Naruszenie kluczy ERP przy synchronizacji | Niskie | Wysoki | Transakcyjna sync z rollback; event sourcing dla auditability |
| R09 | Konflikt norm (EN vs ASTM) przy substytucjach | Średnie | Średni | Explicit equivalence_type; engineering_approval_required flag |
| R10 | Wzrost rozmiaru tabeli price_records (miliony wierszy) | Pewne (czas) | Niski | Partycjonowanie, archiwizacja danych > 3 lata do cold storage |
| R11 | API rate limiting blokujące integracje systemowe | Niskie | Wysoki | Osobne limity dla service accounts; retry z backoff |
| R12 | Korupcja HNSW index pgvector | Niskie | Średni | Cykliczny VACUUM + REINDEX; monitoring rozmiarów indeksów |

---

## 25. Roadmap

### Faza 1 — MVP (miesiące 1–3)

**Cel: uruchomienie podstawowego katalogu materiałów z cenami**

| Sprint | Deliverable |
|--------|-------------|
| S1 | Schema bazy danych, enums, tabele core (materials, properties, standards) |
| S2 | REST API CRUD materiałów + walidacja + audit log |
| S3 | Taksonomia materiałów — 3 poziomy (MET, POL, WOD, PKG, CMP, SPC) |
| S4 | Import danych startowych: 150+ materiałów (S235, S355, DC01, DX51, 304, 316, Al6082, ABS, PC, PA6, PA66, POM, PE, PP, MDF, HDF) |
| S5 | Market price layer — ręczne wprowadzanie + tabela PriceSource |
| S6 | Podstawowe API wyszukiwania (pełnotekstowe) |
| S7 | Endpoint `/cost` — kalkulacja kosztu materiału |
| S8 | Testy + stabilizacja |

**Wyjście MVP:** 150 materiałów, API CRUD, wyszukiwanie, kalkulacja kosztu.

---

### Faza 2 — Core Intelligence (miesiące 4–6)

**Cel: silnik substytucji, kompatybilność, automatyczne ceny**

| Sprint | Deliverable |
|--------|-------------|
| S9 | Substitution engine — baza + scoring + API |
| S10 | Process compatibility matrix — 50+ kombinacji materiał-proces |
| S11 | Konektor LME (Cu, Al, Zn) — automatyczne pobieranie cen |
| S12 | Konektor S&P Platts (stale EU) |
| S13 | Price validator + anomaly detection + alerty |
| S14 | Supplier material mapping — API + import |
| S15 | Supply risk assessor |
| S16 | Kafka event publishing (material.*, price.*) |
| S17 | Integracja z Cost Calculation Engine |

**Wyjście Fazy 2:** Automatyczne ceny, zamienniki, Kafka events.

---

### Faza 3 — AI & Advanced (miesiące 7–9)

**Cel: wyszukiwanie semantyczne, prognozowanie cen, pełna integracja ERP**

| Sprint | Deliverable |
|--------|-------------|
| S18 | Embedding generation pipeline (text-embedding-3-small) |
| S19 | pgvector HNSW index + semantic search API |
| S20 | Price forecast engine (Prophet dla metali) |
| S21 | Konektor ICIS Polymers (ABS, PC, PA, PP, PE) |
| S22 | RAG context builder dla agentów AI |
| S23 | ERP sync (SAP Material Master / Oracle Item) |
| S24 | Monitoring dashboards (Grafana) + alerting |
| S25 | Polymer-specific properties (MFI, shrinkage, chemical resistance) |
| S26 | Security hardening + pentest |

**Wyjście Fazy 3:** Wyszukiwanie semantyczne, prognozy, ERP sync.

---

### Faza 4 — Scale & Enrich (miesiące 10–12)

**Cel: pełne pokrycie danych, skalowalność produkcyjna, moduły zaawansowane**

| Sprint | Deliverable |
|--------|-------------|
| S27 | Rozbudowa katalogu do 500+ materiałów |
| S28 | Normy cross-reference (pełna tabela ISO/DIN/EN/ASTM) |
| S29 | Wood & packaging properties (pełny model) |
| S30 | Composite materials — GF/CF properties + process rules |
| S31 | Redis caching layer (full) |
| S32 | Read replicas PostgreSQL + load balancing |
| S33 | Regulatory compliance module (REACH, RoHS, SVHC) |
| S34 | Material lifecycle management (obsolescence workflow) |
| S35 | Import API (bulk CSV/Excel dla masterdata) |
| S36 | Performance testing + tuning (1M price records, 500+ materials) |

**Wyjście Fazy 4:** System produkcyjny, skalowalny, zgodny z regulacjami.

---

### Długoterminowe (rok 2+)

| Inicjatywa | Opis |
|------------|------|
| Multi-tenant | Obsługa wielu firm w jednej instancji (schema isolation per tenant) |
| Material co-pilot | LLM agent z dostępem do MIE — rekomendacje materiałowe w języku naturalnym |
| Supplier price integration | Automatyczne pobieranie cen z portali dostawców (web scraping + EDI) |
| Digital Material Passport | Zgodność z EU Digital Product Passport (DPP) regulation |
| Carbon footprint layer | Emisje CO₂ per materiał + procesy (scope 3 emissions) |
| Materials marketplace | Integracja z giełdami nadwyżek materiałowych (B2B marketplace) |
| Predictive substitution | ML model przewidujący najlepszy zamiennik na podstawie historii zakupów |

---

## Podsumowanie architektury

```
┌─────────────────────────────────────────────────────────────────┐
│               MATERIAL INTELLIGENCE ENGINE                       │
│                                                                   │
│  REST API (OpenAPI 3.1)          Kafka Events                    │
│  ├── /materials (CRUD)           ├── mie.material.*              │
│  ├── /materials/{id}/prices      ├── mie.price.*                 │
│  ├── /materials/{id}/cost        └── mie.supplier_mapping.*      │
│  ├── /materials/{id}/substitutions                               │
│  ├── /materials/{id}/compatibility                               │
│  ├── /search (full-text)                                         │
│  └── /search/semantic (vector)                                   │
│                                                                   │
│  Domain Model                    AI Layer                        │
│  ├── MaterialAggregate           ├── Embeddings (pgvector)       │
│  ├── TaxonomyAggregate           ├── Semantic Search (HNSW)      │
│  ├── PriceAggregate              └── RAG Context Builder         │
│  ├── SubstitutionAggregate                                       │
│  ├── CompatibilityAggregate      Market Price Layer              │
│  └── SupplierMaterialAggregate   ├── LME Connector               │
│                                  ├── Platts Connector            │
│  PostgreSQL 16 + pgvector        ├── ICIS Connector              │
│  ├── 15 core tables              ├── Price Validator             │
│  ├── Partitioned audit log       ├── Anomaly Detection           │
│  ├── HNSW vector index           └── Forecast Engine (Prophet)   │
│  └── Full-text search (GIN)                                      │
│                                                                   │
│  Redis (cache)    Prometheus/Grafana    RBAC (JWT + API Key)     │
└─────────────────────────────────────────────────────────────────┘
```
