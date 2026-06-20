# Similarity Cost Search Engine (SCSE) — Security, Testing, Scalability, Risks & Roadmap

**Document:** `04-security-testing-scalability-risks-roadmap.md`  
**Module:** Similarity Cost Search Engine (SCSE)  
**Platform:** Industrial Cost Intelligence (ICI)  
**Version:** 1.0.0  
**Status:** Approved  
**Classification:** Internal — Confidential  
**Last Updated:** 2026-06-20  

---

## Table of Contents

- [Section 15: Security](#section-15-security)
- [Section 16: Testing](#section-16-testing)
- [Section 17: Scalability](#section-17-scalability)
- [Section 18: Risks](#section-18-risks)
- [Section 19: Roadmap](#section-19-roadmap)

---

## Section 15: Security

### Overview

The Similarity Cost Search Engine operates at the intersection of procurement intelligence and competitive commercial data. The vector indexes it maintains encode proprietary cost structures, supplier relationships, material specifications, and quote histories — information whose unauthorized disclosure could cause direct financial harm, violate contractual confidentiality obligations, and trigger regulatory consequences under GDPR and SOC 2. Unlike a conventional database search, a vector similarity engine presents a unique attack surface: an adversary who can query the system can perform membership inference attacks, extracting approximate knowledge of indexed data even without direct read access to the underlying payloads.

The threat model for SCSE encompasses four principal adversary classes. Internal adversaries — employees or contractors with legitimate platform access — may attempt to exceed their authorization, for example by a SCSE_VIEWER attempting to infer floor prices through repeated similarity queries. External adversaries exploiting stolen credentials or API tokens present a second threat. Compromised service accounts (SYSTEM_INTEGRATOR tokens) could be used to inject poisoned vectors into the index, corrupting similarity results for all users. Finally, misconfiguration threats — bugs in tenant isolation filters, missing authorization checks on newly introduced endpoints, or improperly scoped JWT claims — constitute a systemic risk inherent to any rapidly evolving service.

The control architecture responds to this threat model through defense-in-depth: role-based access control enforced at the middleware layer, field-level encryption for sensitive pricing payloads, mTLS between internal services, immutable audit logging with SOC 2 retention policies, and continuous security testing integrated into the CI/CD pipeline. No single control is considered sufficient; the assumption is that any individual control may fail, and the remaining controls must be sufficient to detect, contain, and recover from that failure.

---

### 15.1 RBAC Roles

Access to SCSE endpoints is governed by a seven-role hierarchy derived from the ICI platform's central identity model. The roles are designed around the principle of least privilege: each role grants only the permissions necessary for its designated business function, and roles are not composable at runtime — a user holds exactly one SCSE role, assigned by the ICI administrator. This prevents privilege accumulation through role stacking and simplifies permission auditing during SOC 2 reviews.

The role hierarchy is strictly ordered, with each level a superset of the permissions below it, with the exception of SYSTEM_INTEGRATOR, which is a lateral role for machine-to-machine access rather than a user-facing role. SYSTEM_INTEGRATOR tokens are issued to internal platform services (such as the Cost Harmonization Engine and the Master Item Engine) that need to push entity data into the SCSE index. This separation ensures that automated systems cannot perform human-facing search operations, and human users cannot perform bulk index mutations without an explicit privilege grant.

| Role | Description | Permissions |
|------|-------------|------------|
| SCSE_VIEWER | Read-only search access | POST /search (basic), GET /recommend, GET /cache |
| SCSE_ANALYST | Full search + analytics | All search endpoints, GET /analytics/* |
| SCSE_PROCUREMENT | Search + feedback submission | All SCSE_ANALYST + POST /feedback/* |
| SCSE_DATA_STEWARD | Manage training labels | All SCSE_ANALYST + POST /feedback/label, manage labels |
| SCSE_OPS | Operational management | All above + GET /admin/index/versions, trigger reindex |
| SCSE_ADMIN | Full admin | All endpoints including DELETE /index, rollback |
| SYSTEM_INTEGRATOR | API access for internal services | POST /index/entity, POST /index/batch |

The role hierarchy enforces strict separation of duties between operational, analytical, and data governance functions. An SCSE_OPS engineer can trigger a reindex and inspect index versions but cannot delete the index or modify training labels — those actions require SCSE_ADMIN and SCSE_DATA_STEWARD respectively. This ensures that no single individual can both degrade index quality and cover the evidence of doing so within their normal role permissions.

---

### 15.2 JWT Authorization Middleware

SCSE uses RS256-signed JSON Web Tokens issued by the ICI Identity Provider (IdP), an internal Keycloak instance federated with the corporate Active Directory. RS256 asymmetric signing ensures that even if a consuming service's private keys were compromised, those keys cannot forge tokens — only the IdP's signing key can produce valid tokens. Access tokens carry a 15-minute TTL, requiring clients to use refresh tokens for sustained sessions. The short TTL limits the window of exposure for stolen tokens and is balanced by silent token refresh in the ICI frontend, which is transparent to end users.

Authorization middleware is implemented as a FastAPI dependency injection chain: every protected endpoint declares a `required_permission` string, and the `SCSEAuthMiddleware.verify_permission` method resolves whether the caller's role grants that permission before the endpoint handler executes. This architecture ensures that permission checks cannot be bypassed by endpoint developers — forgetting to add an auth dependency results in a startup-time validation error via a custom route registry check, not a silent security gap at runtime.

```python
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer

class SCSEAuthMiddleware:
    ROLE_PERMISSIONS = {
        "SCSE_VIEWER": {"search:read", "recommend:read", "cache:read"},
        "SCSE_ANALYST": {"search:read", "recommend:read", "cache:read", "analytics:read"},
        "SCSE_PROCUREMENT": {"search:read", "recommend:read", "cache:read", "analytics:read", "feedback:write"},
        "SCSE_DATA_STEWARD": {"search:read", "recommend:read", "analytics:read", "feedback:write", "labels:write"},
        "SCSE_OPS": {"search:read", "recommend:read", "analytics:read", "feedback:write", "index:read", "index:trigger"},
        "SCSE_ADMIN": {"*"},  # all permissions
        "SYSTEM_INTEGRATOR": {"index:write"},
    }
    
    async def verify_permission(self, token: str, required_permission: str) -> TokenClaims:
        claims = self.jwt_validator.validate(token)
        role = claims.get("role")
        permissions = self.ROLE_PERMISSIONS.get(role, set())
        if "*" not in permissions and required_permission not in permissions:
            raise HTTPException(status_code=403, detail=f"Role {role} lacks {required_permission}")
        return claims
```

Token expiry is handled by the `jwt_validator.validate` method, which raises a 401 `TokenExpiredError` with a `WWW-Authenticate: Bearer error="invalid_token"` header conforming to RFC 6750. All authentication failures are logged to the audit trail with the source IP address, enabling detection of credential stuffing and brute force patterns. The middleware integrates with the ICI central identity provider through a JWKS endpoint — public keys are cached in memory with a 1-hour TTL and refreshed automatically, ensuring that key rotation by the IdP propagates to SCSE without service restart.

---

### 15.3 Data Privacy Controls

SCSE indexes and returns procurement data that falls into several sensitivity categories under the ICI data classification policy. Floor prices, margin percentages, and NRE (non-recurring engineering) costs are classified as Restricted — they are visible only to roles with a business need for cost analytics (SCSE_ANALYST and above). Supplier contract prices and discount tiers are similarly restricted. General specification data (material grades, process parameters, tolerance classes) is classified as Internal and visible to all authenticated roles. GDPR considerations apply because search query text may contain personal data when users search by procurement officer name, supplier contact names, or contract reference numbers that encode individual identifiers.

The data minimization strategy operates at two layers. At the API response layer, a `SearchResultMasker` nullifies restricted fields based on the caller's role before the response is serialized. At the storage layer, sensitive payload fields in Qdrant are stored in encrypted form using AES-256-GCM with keys managed by AWS KMS, ensuring that even direct Qdrant database access (e.g., by a compromised infrastructure operator) does not expose plaintext pricing data. Decryption occurs exclusively in the Python service layer after role verification.

**Search result masking:**
- `SCSE_VIEWER` role: floor_price_eur, margin_pct, supplier_contract_price masked (NULL in response)
- `SCSE_ANALYST` and above: full price data visible
- GDPR: search query logs anonymized after 90 days (user_id → SHA-256 hash)

```python
class SearchResultMasker:
    MASKED_FIELDS_BY_ROLE = {
        "SCSE_VIEWER": ["floor_price_eur", "margin_pct", "nre_cost_eur"],
        "SCSE_ANALYST": [],  # nothing masked
    }
    
    def mask(self, result: SearchResult, role: str) -> SearchResult:
        masked_fields = self.MASKED_FIELDS_BY_ROLE.get(role, [])
        for field in masked_fields:
            if field in result.payload:
                result.payload[field] = None
        return result
```

**Qdrant payload filtering:**
- Sensitive fields (floor_price_eur, margin_pct) stored encrypted in payload
- AES-256-GCM encryption via AWS KMS
- Decryption only in Python service layer (never in Qdrant)

Envelope encryption is used for key management: each Qdrant collection has a unique data encryption key (DEK) encrypted by a KMS-managed key encryption key (KEK). Key rotation is performed quarterly by the security team; rotation generates new DEKs and triggers a background re-encryption job that reads, decrypts with the old DEK, re-encrypts with the new DEK, and writes back — without requiring a full re-index of the vector collection. This ensures that a compromised DEK has a bounded exposure window without disrupting search availability.

---

### 15.4 Network Security

All SCSE components are deployed within a private VPC with no direct internet ingress. The SCSE API is exposed through an AWS Application Load Balancer with WAF rules, terminating public TLS. Internal service-to-service communication — between the SCSE API and Qdrant, between the embedding worker and the SCSE API, and between Kafka consumers and the SCSE service — uses mutual TLS (mTLS) with certificates issued by the ICI internal certificate authority (HashiCorp Vault PKI). This ensures that even within the VPC, a compromised node cannot impersonate a service without a valid certificate.

Rate limiting is enforced at two tiers: at the API Gateway level (AWS API Gateway with token bucket algorithm) and at the application level within SCSE's FastAPI middleware. The two-tier approach ensures that burst traffic from misbehaving clients is shed before reaching the application tier, while application-level rate limiting enables per-user and per-role customization that is not possible at the gateway tier alone.

- Qdrant cluster: private VPC subnet, no public internet access
- mTLS for service-to-service (SCSE API → Qdrant)
- API Gateway rate limiting: 1000 req/min per user, 10000 req/min per service
- OpenAI API key: AWS Secrets Manager, rotated monthly

WAF rules are configured to block SQL injection, XSS, and known embedding API abuse patterns (e.g., suspiciously long query texts that may be prompt injection attempts against the LLM embedding pipeline). AWS Shield Standard provides baseline DDoS protection at the load balancer level; Shield Advanced is provisioned for production to ensure sub-minute mitigation of volumetric attacks. VPC security groups enforce allow-listing: the SCSE API security group permits inbound on port 8080 from the load balancer only, the Qdrant security group permits inbound on port 6333 exclusively from the SCSE API security group, and no inter-service communication is permitted outside these defined paths.

---

### 15.5 Audit Logging

Comprehensive audit logging is a first-class requirement for SCSE, driven by SOC 2 Type II commitments and the operational need to investigate similarity search anomalies. Every search query, index operation, feedback submission, and administrative action is recorded in an append-only audit event stream. The immutability guarantee is achieved by writing to both the `scse.audit_log` PostgreSQL table (with row-level security preventing deletion by application roles) and publishing to a Kafka topic consumed by the central ICI audit log aggregation service, which writes to an S3 bucket with Object Lock (WORM compliance).

The audit log schema captures the minimum fields necessary for security review without logging the full search result payloads (which would create a secondary sensitive data store in the audit log). IP addresses are encrypted in the log record using AES-256-GCM with a separate audit log encryption key, balancing the need for forensic traceability with privacy requirements. The Kafka-based architecture ensures that even if the PostgreSQL `scse.audit_log` table were somehow modified, the Kafka-sourced S3 records provide an independent audit trail.

```python
class SCSEAuditLogger:
    async def log_search(self, query: SearchQuery, results: SearchResponse, user: TokenClaims) -> None:
        event = {
            "event_type": "SCSE_SEARCH",
            "user_id": user["sub"],
            "role": user["role"],
            "ip_address": self.encrypt(user["ip"]),  # AES-256-GCM
            "entity_type": query.entity_type,
            "search_mode": query.search_mode,
            "result_count": len(results.results),
            "timestamp": datetime.utcnow().isoformat(),
        }
        # Write to scse.audit_log table and publish to Kafka
    
    async def log_index_operation(self, operation: str, entity_id: UUID, user: TokenClaims) -> None:
        # Log SCSE_INDEX_ENTITY, SCSE_DELETE_INDEX, SCSE_TRIGGER_REINDEX
```

The log retention policy distinguishes between audit retention and query log retention. Audit events (all event types except raw query text) are retained for 7 years in S3, satisfying SOC 2 Type II requirements and typical enterprise contract audit provisions. Raw query text — which may contain PII — is retained for 90 days in the PostgreSQL `scse.audit_log` table, after which the `user_id` field is replaced with a SHA-256 hash and the `query_text` field is replaced with a PII-redacted version. Automated Airflow DAGs execute this anonymization sweep nightly, with a reconciliation check to confirm no un-anonymized records older than 90 days remain.

---

### 15.6 Security Controls Table

The following table summarizes the complete security control inventory for SCSE, mapping each control to its implementation mechanism and the applicable security standard or compliance framework. This inventory is reviewed quarterly by the Security Architect and updated in conjunction with SOC 2 evidence collection cycles. Any gap identified during a quarterly review is triaged as a risk item under Section 18 and assigned an owner with a remediation timeline.

| Control | Implementation | Standard |
|---------|---------------|---------|
| Authentication | JWT RS256, 15min TTL | OAuth 2.0 / OIDC |
| Authorization | RBAC, permission model | NIST RBAC |
| Transport encryption | TLS 1.3, mTLS | FIPS 140-2 |
| Data at rest | AES-256-GCM (sensitive fields) | FIPS 140-2 |
| Secret management | AWS Secrets Manager | CIS Benchmark |
| Audit logging | immutable event log | SOC 2 Type II |
| Rate limiting | Token bucket (1000 req/min) | OWASP API Security |
| Input validation | Pydantic v2, max query length 2000 chars | OWASP Top 10 |
| Vector poisoning detection | Cosine drift alert >0.15 | Custom |
| DLP | PII detection in query text (Presidio) | GDPR |

The defense-in-depth posture for SCSE ensures that no single point of failure exposes sensitive procurement data. Authentication failures are caught before authorization is evaluated; authorization failures are caught before data is decrypted; decrypted data is masked before it reaches the serialization layer; and all of these transitions are recorded in an immutable audit log. The combination of preventive controls (RBAC, encryption, mTLS) and detective controls (audit logging, PII detection, vector poisoning alerts) provides layered protection appropriate for a system handling Restricted-classified commercial cost data.

---

## Section 16: Testing

### Overview

Testing a similarity search engine requires a fundamentally different approach from testing a conventional CRUD service. Correctness in SCSE is not a binary pass/fail property — it is a statistical property of retrieval quality, measured against human-labeled ground truth. A search that returns syntactically correct JSON with status 200 may still be functionally incorrect if the returned entities are semantically irrelevant to the query. This distinction drives the testing philosophy: functional correctness (unit and integration tests) is a necessary but insufficient condition for release; recall quality (evaluated against a golden labeled dataset) is an equally binding gate.

The test pyramid for SCSE has four layers. The base consists of unit tests covering pure algorithmic logic: confidence score calculation, MMR reranking, RRF fusion, result masking, and feature extraction. The second layer is integration testing with real infrastructure spun up via Testcontainers, covering the end-to-end flow from search request to Qdrant query to response. The third layer is contract testing with Pact, ensuring that changes to the SCSE API do not silently break consuming services (CHE, SIE, MIE). The fourth layer is quality evaluation: the nightly recall harness, the k6 load test, and chaos experiments that validate behavior under infrastructure failure.

The golden dataset is the cornerstone of quality assurance for SCSE. It consists of human-labeled similarity pairs curated by the procurement and data stewardship teams, with a minimum of 500 labeled pairs per entity type (2500 total). Labels are reviewed quarterly and extended as new entity types or procurement categories are introduced. The golden dataset is versioned in the SCSE repository and each nightly evaluation report records the dataset version against which results were computed, enabling trend analysis across dataset revisions.

---

### 16.1 Test Matrix

| Test Type | Tool | Coverage | Runtime | Trigger |
|-----------|------|---------|---------|---------|
| Unit tests | pytest | >85% | <2min | pre-commit |
| Integration tests | pytest + Testcontainers | service boundaries | <10min | CI |
| Contract tests | Pact | SCSE↔CHE, SCSE↔SIE, SCSE↔MIE | <5min | CI |
| Load tests | k6 | search, indexing | 30min | weekly |
| Recall evaluation | custom harness | precision/recall | 1h | nightly |
| Regression tests | pytest | golden dataset | <15min | CI |
| Chaos tests | Chaos Monkey | Qdrant failure, embedding timeout | 2h | monthly |
| Security tests | Bandit + SAST | vulnerability scan | <5min | CI |

All tests in the matrix are integrated into the CI/CD pipeline via GitHub Actions. The CI pipeline enforces sequential gate logic: unit tests must pass before integration tests begin, integration tests must pass before contract tests run, and no deployment proceeds to staging without all CI gates green. The nightly recall evaluation is a separate Airflow DAG that runs against the staging environment after the nightly deployment; a result of P@1 < 0.75 for any entity type raises a PagerDuty alert to the on-call ML engineer and blocks the next production deployment until the regression is triaged. This ensures that recall quality regressions are caught before they reach production users.

---

### 16.2 Unit Tests

Unit tests cover the core algorithmic components of SCSE in isolation, using mocked dependencies for all I/O. The primary coverage targets are: `ConfidenceCalculator` (all five scoring components and grade thresholds), `maximal_marginal_relevance` (diversity-relevance tradeoff at various lambda values), `reciprocal_rank_fusion` (correct score aggregation across multiple ranked lists), `SearchResultMasker` (field masking by role), and `ProductFeatureExtractor` / `MaterialFeatureExtractor` (correct field serialization and normalization). Qdrant client calls and OpenAI embedding calls are mocked using `AsyncMock` to ensure unit tests complete within the 2-minute budget and do not depend on external services.

The mocking strategy uses a `conftest.py` fixture hierarchy: a `mock_qdrant_client` fixture provides a pre-configured `AsyncMock` of the Qdrant Python client with sensible default return values, and a `mock_openai_client` fixture returns pre-computed embedding vectors from a fixture file rather than calling the real API. This ensures that unit tests are fully deterministic — the same test run on the same code always produces the same result — which is critical for pre-commit hooks where test flakiness would degrade developer experience and erode confidence in the test suite.

```python
import pytest
from unittest.mock import AsyncMock, MagicMock
import numpy as np

class TestConfidenceCalculator:
    def setup_method(self):
        self.calculator = ConfidenceCalculator()
    
    def test_high_confidence_for_exact_match(self):
        query = SimilarityQuery(feature_vector=np.ones(512))
        match = SimilarityCandidate(
            cosine_similarity=0.97,
            feature_vector=np.ones(512),
            payload={
                "name": "Test Material",
                "category": "Steel",
                "price_eur": 1.5,
                "updated_at": datetime.utcnow().isoformat(),
                "status": "ACTIVE",
            }
        )
        confidence = self.calculator.calculate(query, match)
        assert confidence.overall >= 0.85
        assert confidence.grade == ConfidenceGrade.HIGH
    
    def test_low_confidence_for_stale_data(self):
        query = SimilarityQuery(feature_vector=np.ones(512))
        match = SimilarityCandidate(
            cosine_similarity=0.82,
            feature_vector=np.ones(512) * 0.9,
            payload={
                "updated_at": (datetime.utcnow() - timedelta(days=400)).isoformat(),
                "status": "ACTIVE",
            }
        )
        confidence = self.calculator.calculate(query, match)
        assert "old" in " ".join(confidence.warnings).lower()
    
    def test_temporal_relevance_decay(self):
        recent = self.calculator._temporal_relevance_score(datetime.utcnow().isoformat())
        old = self.calculator._temporal_relevance_score(
            (datetime.utcnow() - timedelta(days=365)).isoformat()
        )
        assert recent > 0.90
        assert old < 0.10


class TestMMR:
    def test_diversity_reduces_redundancy(self):
        # Build 5 highly similar candidates and 5 diverse ones
        # MMR with lambda=0.5 should prefer diverse
        similar = [SimilarityCandidate(fused_vector=np.array([1,0,0,...]), semantic_score=0.90)] * 5
        diverse = [SimilarityCandidate(fused_vector=np.random.randn(1024), semantic_score=0.80)] * 5
        candidates = similar + diverse
        result = maximal_marginal_relevance(candidates, lambda_=0.5, k=5)
        # Expect not all from "similar" group
        assert len(set(id(r) for r in result if r in similar)) < 5
    
    def test_mmr_lambda_1_returns_top_relevance(self):
        # lambda=1.0 → pure relevance, should return highest semantic_score
        ...


class TestRRF:
    def test_rrf_fusion_promotes_consistently_ranked(self):
        dense_results = [("A", 0.95), ("B", 0.85), ("C", 0.70)]
        sparse_results = [("A", 15.0), ("C", 12.0), ("B", 8.0)]  # A,C ranked higher in sparse
        fused = reciprocal_rank_fusion([dense_results, sparse_results], k=60)
        # A should be #1 (top in both), C should beat B (higher in sparse)
        assert fused[0][0] == "A"
        assert fused[1][0] == "C"


class TestSearchResultMasker:
    def test_viewer_cannot_see_floor_price(self):
        result = SearchResult(payload={"floor_price_eur": 9.50, "name": "Steel rod"})
        masked = SearchResultMasker().mask(result, role="SCSE_VIEWER")
        assert masked.payload["floor_price_eur"] is None
        assert masked.payload["name"] == "Steel rod"
    
    def test_analyst_sees_all_fields(self):
        result = SearchResult(payload={"floor_price_eur": 9.50})
        unmasked = SearchResultMasker().mask(result, role="SCSE_ANALYST")
        assert unmasked.payload["floor_price_eur"] == 9.50
```

Test fixtures are organized using pytest's scope hierarchy: session-scoped fixtures provide shared database connection pools and mock embedding matrices, while function-scoped fixtures handle per-test state reset. Tests are marked with custom pytest markers (`@pytest.mark.unit`, `@pytest.mark.slow`, `@pytest.mark.ml`) to allow selective execution — the pre-commit hook runs only `@pytest.mark.unit` tests, while the CI pipeline runs all markers. This ensures fast feedback during local development without sacrificing coverage in CI.

---

### 16.3 Integration Tests (Testcontainers)

Testcontainers provides the SCSE integration test suite with real, ephemeral infrastructure: actual PostgreSQL 16 with the pgvector extension, actual Qdrant, Redis, and Kafka instances are started as Docker containers scoped to the test session. This eliminates the class of bugs where mocked behavior diverges from real service behavior — a particularly common failure mode in vector search systems, where Qdrant's filter evaluation semantics, HNSW approximate search behavior, and collection configuration options differ subtly from any mock implementation. Running against real services means integration tests catch real integration failures.

The test stack is defined in `tests/docker/docker-compose.test.yml` and uses deterministic image versions pinned in the same file. Session-scoped fixtures start the stack once per test session and perform teardown (including collection deletion and database schema drop) after all tests complete. Each test class uses function-scoped fixtures to insert and clean up test data, ensuring test isolation — a failure in one test does not corrupt state for subsequent tests. The Qdrant test collection uses a distinct name suffix (`_test`) to prevent accidental interference with any development environment collections.

```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.compose import DockerCompose

@pytest.fixture(scope="session")
def scse_stack():
    with DockerCompose("tests/docker", compose_file_name="docker-compose.test.yml") as compose:
        # Starts: PostgreSQL 16, Qdrant, Redis, Kafka
        compose.wait_for("http://localhost:6333/healthz")  # Qdrant ready
        yield compose

class TestHybridSearch:
    async def test_hybrid_search_finds_similar_material(self, scse_stack, db_pool):
        # 1. Insert test materials into PostgreSQL
        # 2. Generate and upsert embeddings to Qdrant test collection
        # 3. Execute hybrid search
        # 4. Assert top result matches expected material
        
        await insert_test_materials(db_pool, count=100)
        await embed_and_index_all("MATERIAL", collection="scse_materials_test")
        
        results = await scse_api.post("/search/materials", json={
            "query_text": "austenitic stainless steel 316L grade food contact",
            "mode": "HYBRID",
            "top_k": 10,
            "min_similarity": 0.70,
        })
        
        assert results.status_code == 200
        body = results.json()
        assert body["total_count"] >= 3
        assert body["results"][0]["confidence"]["grade"] in ("HIGH", "MEDIUM")
        assert "316" in body["results"][0]["payload"]["name"] or "stainless" in body["results"][0]["payload"]["name"].lower()
    
    async def test_indexing_triggers_qdrant_upsert(self, scse_stack):
        # POST /index/entity → verify point appears in Qdrant
        response = await scse_api.post("/index/entity", json={
            "entity_type": "SUPPLIER",
            "entity_id": str(test_supplier_id),
        })
        assert response.status_code == 202
        await asyncio.sleep(2)  # wait for async indexing
        point = await qdrant_client.retrieve("scse_suppliers_test", [str(test_supplier_id)])
        assert len(point) == 1
    
    async def test_feedback_improves_cache_label(self, scse_stack):
        # Submit feedback → verify training_labels updated
```

Test data isolation is enforced by a combination of unique collection names per test session (using a session UUID suffix injected at fixture time) and database transaction rollback for PostgreSQL operations. The CI environment provisions a dedicated Docker-in-Docker runner with 16GB RAM and 8 vCPUs to ensure integration tests complete within the 10-minute budget even when running all five entity types. Testcontainer startup time (~45 seconds for the full stack) is amortized across the session, contributing less than 8% of total integration test runtime.

---

### 16.4 Recall Evaluation Harness

The nightly recall evaluation harness is the primary quality gate for SCSE's core function: finding the right similar entities. It operates against the production staging environment using the same golden labeled dataset as the CI regression tests but runs the full evaluation (all 2500+ labels across all entity types) rather than the subset used in CI. The harness measures Precision@K for K ∈ {1, 5, 10, 20}, Mean Reciprocal Rank (MRR), and NDCG@10 — a graded relevance metric that rewards surfacing highly relevant results at the top of the ranking more than surfacing them at the bottom.

The golden dataset uses a three-level relevance grading: EXACT (score 3), HIGHLY_SIMILAR (score 2), and SIMILAR (score 1). Labels are assigned by a pool of at least three annotators per entity type, with inter-annotator agreement tracked via Cohen's kappa; pairs with kappa < 0.6 are escalated to a data steward for adjudication. The graded relevance structure enables NDCG@10 to capture the full quality spectrum — a system that returns EXACT matches in positions 1–3 and SIMILAR matches in positions 4–10 scores much higher than one that returns SIMILAR matches throughout, even if both achieve the same Precision@10.

```python
class RecallEvaluationHarness:
    """Nightly evaluation against golden labeled dataset."""
    
    def __init__(self, engine: SCSearchEngine, labels: list[SimilarityLabel]):
        self.engine = engine
        self.labels = labels  # human-labeled pairs with ground truth
    
    async def evaluate(self, entity_type: EntityType, k_values: list[int] = [1, 5, 10, 20]) -> EvalReport:
        precision_scores = {k: [] for k in k_values}
        ndcg_scores = []
        rr_scores = []  # reciprocal rank
        
        for label in self.labels:
            results = await self.engine.search(
                entity_type=entity_type,
                query_entity_id=label.source_entity_id,
                top_k=max(k_values),
            )
            result_ids = [r.entity_id for r in results.results]
            
            # Precision@K
            for k in k_values:
                relevant = sum(1 for id_ in result_ids[:k] if self._is_relevant(id_, label))
                precision_scores[k].append(relevant / k)
            
            # MRR
            for rank, id_ in enumerate(result_ids, 1):
                if self._is_relevant(id_, label):
                    rr_scores.append(1 / rank)
                    break
            else:
                rr_scores.append(0.0)
            
            # NDCG@10
            ndcg_scores.append(self._ndcg_at_k(result_ids, label, k=10))
        
        return EvalReport(
            entity_type=entity_type,
            precision_at_k={k: np.mean(scores) for k, scores in precision_scores.items()},
            mrr=np.mean(rr_scores),
            ndcg_at_10=np.mean(ndcg_scores),
            dataset_size=len(self.labels),
            evaluated_at=datetime.utcnow(),
        )
    
    def _is_relevant(self, entity_id: UUID, label: SimilarityLabel) -> bool:
        return entity_id == label.target_entity_id and label.label in (
            SimilarityLabel.EXACT, SimilarityLabel.NEAR_DUPLICATE, SimilarityLabel.HIGHLY_SIMILAR
        )
    
    def _ndcg_at_k(self, result_ids: list, label: SimilarityLabel, k: int) -> float:
        relevance_scores = {label.target_entity_id: 3, ...}  # graded relevance
        # compute DCG, IDCG, return DCG/IDCG
```

Alerting thresholds are defined as follows: P@1 < 0.75 for any entity type triggers a PagerDuty alert to the ML Lead on-call rotation; P@1 < 0.65 (a severe regression) triggers an automatic production deployment hold via a CI gate status check. NDCG@10 < 0.55 triggers a Slack notification to the ML team channel with a link to the full evaluation report. All evaluation reports are stored in S3 in Parquet format with entity type, dataset version, and evaluation timestamp as partition keys, enabling historical trend queries in Athena for quarterly quality review presentations.

---

### 16.5 k6 Load Test

The k6 load test suite validates that SCSE meets its SLA targets under realistic and peak production load conditions. The test simulates a realistic distribution of user behavior: 80% standard hybrid search queries, 10% recommendation requests, and 10% cache hit scenarios. Three load stages are modeled: a 2-minute ramp-up to 50 virtual users (representing normal business hours), a 5-minute sustained load at 200 virtual users (representing peak procurement hours, 09:00–11:00 CET), and a 2-minute peak at 500 virtual users (representing end-of-quarter RFQ surge scenarios). The SLA thresholds — P95 < 300ms for search, P99 < 1000ms, zero-result rate < 2% — are encoded directly in the k6 options as hard failure thresholds, meaning the load test fails the CI stage if any threshold is breached.

The query sample set is curated to reflect realistic procurement search patterns observed from production query logs (anonymized and generalized per GDPR policy). It includes narrow technical specification queries (material grade + dimension), contextual supplier queries (certification + geography), and cross-reference style queries ("similar to RFQ-ID"). The diversity of query types ensures that the load test exercises all branches of the hybrid search pipeline — dense ANN path, sparse BM25 path, and RRF fusion — rather than artificially testing only the cache-hit path.

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const zeroResultRate = new Rate('zero_result_searches');
const searchLatency = new Trend('search_latency_ms');

export const options = {
  stages: [
    { duration: '2m', target: 50 },    // ramp up
    { duration: '5m', target: 200 },   // sustained load
    { duration: '2m', target: 500 },   // peak
    { duration: '1m', target: 0 },     // ramp down
  ],
  thresholds: {
    'http_req_duration{scenario:search}': ['p(95)<300', 'p(99)<1000'],
    'http_req_duration{scenario:recommend}': ['p(95)<200'],
    'zero_result_searches': ['rate<0.02'],
    'http_req_failed': ['rate<0.005'],
  },
};

const ENTITY_TYPES = ['products', 'materials', 'suppliers', 'processes', 'quotes'];
const QUERY_SAMPLES = [
  "stainless steel 316L sheet 2mm",
  "CNC milling aluminum aerospace tolerance IT7",
  "ISO9001 certified supplier precision machining Germany",
  "quote similar to rfq-2024-0523 price below 50 EUR",
  "austenitic steel food grade chemical resistance",
];

export default function() {
  const entityType = ENTITY_TYPES[Math.floor(Math.random() * ENTITY_TYPES.length)];
  const query = QUERY_SAMPLES[Math.floor(Math.random() * QUERY_SAMPLES.length)];
  
  const start = Date.now();
  const response = http.post(
    `${__ENV.SCSE_BASE_URL}/api/v1/scse/search/${entityType}`,
    JSON.stringify({
      query_text: query,
      mode: 'HYBRID',
      top_k: 10,
      min_similarity: 0.70,
    }),
    {
      headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${__ENV.JWT_TOKEN}` },
      tags: { scenario: 'search' },
    }
  );
  
  const latency = Date.now() - start;
  searchLatency.add(latency);
  
  check(response, {
    'status 200': r => r.status === 200,
    'has results array': r => JSON.parse(r.body).results !== undefined,
    'latency < 500ms': () => latency < 500,
  });
  
  const body = JSON.parse(response.body);
  zeroResultRate.add(body.total_count === 0);
  
  sleep(Math.random() * 0.5 + 0.1);
}
```

Load tests run weekly against the staging environment, which is seeded with a minimum of 500,000 vectors per entity type (2.5M vectors total) to ensure ANN search characteristics match the production index density. The staging Qdrant cluster is provisioned with the same node count and memory configuration as production, preventing the common failure mode of load tests passing on undersized staging infrastructure. k6 results are published to Grafana via the k6 Cloud integration, with a dedicated SCSE Load Test dashboard showing latency percentiles, throughput, error rates, and zero-result rates over time, enabling trend comparison across weekly runs.

---

## Section 17: Scalability

### Overview

Scalability for a vector similarity search engine is qualitatively different from scalability for a traditional relational query system. The dominant cost drivers are memory (vector indexes must fit in RAM for sub-millisecond ANN search), compute (embedding generation is CPU/GPU-bound), and I/O throughput (nightly FAISS batch jobs require sequential reads over hundreds of millions of floats). Horizontal scaling of the search tier is relatively straightforward — stateless API replicas can be added behind a load balancer — but the vector index itself is stateful and requires careful partitioning (sharding in Qdrant terminology) to distribute both memory and search latency across cluster nodes.

SCSE's scalability architecture uses a tiered model with four tiers differentiated by entity count, vector count, target QPS, and latency SLA. Tiers are defined at design time and assigned based on a deployment sizing exercise conducted quarterly as part of capacity planning. Tier transitions (e.g., from Small to Medium as the entity catalog grows) require a planned migration: new Qdrant collections must be created with the correct shard count for the target tier, data must be migrated with zero downtime using Qdrant's collection alias feature (blue-green collection swap), and FAISS index configurations must be updated to match the new collection size.

The choice between Qdrant, FAISS, and pgvector as backend components reflects a deliberate tradeoff analysis. Qdrant provides the best balance of real-time update support, rich payload filtering, and multi-tenant isolation for the online search path. FAISS provides superior throughput for offline batch operations (nightly re-ranking jobs, SCSE Copilot bulk scoring) but lacks real-time update support, making it unsuitable as the primary online index. pgvector (PostgreSQL extension) serves as the hot-standby fallback: it is always available as a side effect of the existing PostgreSQL infrastructure, supports exact nearest-neighbor search (no approximation error), and can serve as a correctness reference during Qdrant upgrade testing — at the cost of significantly higher search latency at scale (>50ms vs. <5ms for Qdrant at 1M vectors).

---

### 17.1 Scalability Tiers

| Tier | Entities | Vector Count | QPS | Latency P95 | Architecture |
|------|----------|-------------|-----|-------------|-------------|
| Small | <100K | <500K | 50 | <150ms | 1 Qdrant node, FAISS in-memory |
| Medium | <1M | <5M | 500 | <300ms | 3 Qdrant cluster, FAISS sharded |
| Large | <10M | <50M | 5000 | <500ms | 6 Qdrant cluster, ClickHouse meta, GPU embed |
| XLarge | >10M | >50M | 10000+ | <1000ms | Multi-region Qdrant, FAISS GPU, async re-rank |

Tier selection is performed during the initial deployment sizing exercise and reviewed quarterly during the capacity planning meeting. The primary upgrade triggers are: entity count exceeding 80% of the current tier's upper bound, sustained QPS exceeding 70% of the tier's rated capacity for more than 2 hours on any business day, or P95 latency exceeding 90% of the SLA threshold in the previous week's load test. Tier downgrades are not performed (to avoid disruptive re-migrations) but are considered during infrastructure cost optimization cycles if entity count remains below 40% of the tier's lower bound for more than one quarter.

---

### 17.2 SCSE Service Architecture

The SCSE service router implements a cache-first, Qdrant-primary, pgvector-fallback pattern for all search requests. Cache lookups (precomputed similarity lists in Redis) serve as the fastest path — sub-5ms — and are prioritized for entity-to-entity similarity requests (POST /similar/:type/:id), which are the most cacheable because the query entity and result set are stable between entity updates. Free-text query searches (POST /search/:type) are less cacheable due to the combinatorial space of query strings, but still benefit from a 15-minute query hash cache that handles common repeated queries (e.g., daily procurement report searches that run the same query repeatedly).

The Qdrant primary path handles all cache misses using ANN search with HNSW indexing. If Qdrant is unavailable (node failure, network partition, or upgrade in progress), the service router falls back to pgvector using the circuit breaker pattern: after three consecutive Qdrant failures within a 10-second window, the circuit opens and all subsequent requests are routed directly to pgvector for 60 seconds before the circuit attempts to close. This ensures that Qdrant availability issues degrade gracefully (higher latency, exact search) rather than causing 503 errors to end users.

```python
class SCSEServiceRouter:
    """Route requests to appropriate backend based on entity type and query size."""
    
    ROUTING_TABLE = {
        # entity_type → (primary_backend, fallback_backend)
        "PRODUCT":  ("qdrant", "pgvector"),  # real-time
        "QUOTE":    ("qdrant", "cache"),      # real-time + cache
        "MATERIAL": ("qdrant", "pgvector"),
        "PROCESS":  ("qdrant", "pgvector"),
        "SUPPLIER": ("qdrant", "cache"),
    }
    
    async def route_search(self, query: SearchQuery) -> SearchResponse:
        # 1. Check similarity cache (precomputed, <5ms)
        if query.mode != SearchMode.SEMANTIC and query.entity_id:
            cached = await self.cache_repo.get(query.entity_type, query.entity_id)
            if cached:
                return SearchResponse(results=cached, source="cache", latency_ms=3)
        
        # 2. Real-time ANN search (Qdrant)
        try:
            return await self.qdrant_engine.search(query)
        except QdrantUnavailable:
            # 3. Fallback to pgvector
            return await self.pgvector_engine.search(query)
```

The circuit breaker is implemented using the `circuitbreaker` Python library with Prometheus metrics instrumentation: `scse_circuit_breaker_state` (CLOSED/OPEN/HALF_OPEN) is exposed as a gauge metric, and `scse_fallback_invocations_total` counts pgvector fallback activations. Health check probes (Kubernetes liveness and readiness) report the circuit breaker state, ensuring that an OPEN circuit is visible in the infrastructure dashboard and that pgvector fallback activations appear in the on-call alerting panel alongside Qdrant availability metrics.

---

### 17.3 Embedding Worker Scaling

The embedding worker pool is a horizontally scalable, queue-based architecture designed to absorb the bursty nature of entity indexing events. Indexing events arrive in three patterns: continuous low-volume (individual entity updates from Kafka consumers, 10–100/minute during normal operations), bulk imports (new supplier catalog ingestion, 10K–100K entities over 2–4 hours), and nightly re-embed cycles (full re-embed when a new embedding model version is deployed, 1M–10M entities over 24–48 hours). The queue-based design means that all three patterns are handled by the same worker pool — the queue provides natural backpressure and prioritization without requiring separate code paths.

Batch windowing is a critical optimization: rather than calling the OpenAI Embeddings API once per entity (incurring HTTP overhead and burning rate limit quota inefficiently), the worker accumulates entities in a batch window (up to 100 texts or 500ms wait, whichever is reached first) before making a single API call. For the `text-embedding-3-large` model, the maximum input is 8191 tokens per text and the API accepts batches of up to 2048 inputs; the 100-text batch size is tuned to stay well within this limit while maximizing API utilization. The token-per-minute rate limiter tracks the rolling token consumption across all concurrent batches and applies backpressure (increasing the batch wait window) when approaching the limit.

```python
class EmbeddingWorker:
    """Horizontally scalable worker for async embedding generation."""
    
    BATCH_CONFIG = {
        "max_batch_size": 100,
        "max_wait_ms": 500,      # batch wait window
        "max_tokens_per_min": 8_000_000,  # text-embedding-3-large limit
        "concurrent_batches": 5,
    }
    
    async def process_queue(self) -> None:
        while True:
            batch = await self.job_queue.dequeue_batch(
                max_size=self.BATCH_CONFIG["max_batch_size"],
                wait_ms=self.BATCH_CONFIG["max_wait_ms"],
            )
            if batch:
                async with self.rate_limiter:
                    embeddings = await self.openai_client.embed_batch(
                        [j.entity_text for j in batch]
                    )
                await asyncio.gather(*[
                    self.qdrant_repo.upsert(job.entity_id, emb) 
                    for job, emb in zip(batch, embeddings)
                ])
```

Failed embedding jobs are moved to a dead letter queue (DLQ) after three consecutive failures with exponential backoff (base 1 second, max 60 seconds, jitter ±20%). The DLQ is monitored by a separate Airflow DAG that retries DLQ items with reduced batch sizes (to handle token-count-related failures) and alerts if DLQ depth exceeds 1000 items. Worker pool depth and batch processing rate are exposed as Prometheus metrics (`scse_embedding_queue_depth`, `scse_embedding_batch_rate`, `scse_embedding_error_rate`) and visualized in the SCSE Operations Grafana dashboard, enabling the on-call engineer to diagnose and respond to embedding pipeline degradation without raw log access.

---

### 17.4 Horizontal Scaling Config (Kubernetes)

The SCSE API deployment runs as a stateless Kubernetes Deployment with a minimum of 3 replicas for high availability (one per availability zone in EU-WEST-1). The HPA (HorizontalPodAutoscaler) scales the API tier from 3 to 20 replicas based on two metrics: CPU utilization (target 70%) and a custom metric `scse_search_queue_depth` (target average value 100) published from the application to the Prometheus adapter. The dual-metric HPA ensures scaling responds to both CPU-bound workloads (dense vector computation during re-ranking) and I/O-bound queue scenarios (burst indexing events) — relying on CPU alone would under-trigger scaling during high-volume async indexing scenarios where CPU is moderate but queue depth is growing.

The embedding worker is deployed as a separate Kubernetes Deployment with dedicated resource requests (4 CPU, 8Gi RAM) reflecting its batch-processing, CPU-intensive workload profile. Separating the API and embedding worker deployments enables independent scaling policies: the API tier scales with search traffic during business hours, while the embedding worker scales with indexing queue depth at any hour. Pod disruption budgets ensure that rolling updates and node maintenance do not reduce below 2 API replicas or 3 embedding workers simultaneously.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: scse-api
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: scse-api
          image: ici/scse-api:latest
          resources:
            requests:
              cpu: "2"
              memory: "4Gi"
            limits:
              cpu: "4"
              memory: "8Gi"
          env:
            - name: QDRANT_URL
              valueFrom:
                secretKeyRef:
                  name: scse-secrets
                  key: qdrant-url
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: scse-api-hpa
spec:
  scaleTargetRef:
    kind: Deployment
    name: scse-api
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: scse_search_queue_depth
        target:
          type: AverageValue
          averageValue: "100"
---
# EmbeddingWorker deployment (separate, CPU-optimized)
apiVersion: apps/v1
kind: Deployment
metadata:
  name: scse-embedding-worker
spec:
  replicas: 5
  template:
    spec:
      containers:
        - name: embedding-worker
          image: ici/scse-embedding-worker:latest
          resources:
            requests: {cpu: "4", memory: "8Gi"}
            limits: {cpu: "8", memory: "16Gi"}
```

Pod disruption budgets are defined as `minAvailable: 2` for the API deployment and `minAvailable: 3` for the embedding worker, ensuring availability during planned node maintenance windows. The rolling update strategy uses `maxSurge: 1, maxUnavailable: 0` for the API deployment, guaranteeing that capacity never drops below the minimum replica count during deployments. A `ResourceQuota` in the `scse` namespace caps total CPU at 200 cores and memory at 400Gi, preventing runaway HPA scaling from consuming cluster capacity needed by other platform services. These quotas are reviewed quarterly as part of the capacity planning process.

---

### 17.5 Caching Strategy

Caching is a first-order scalability concern for SCSE because the most expensive operation — ANN search in Qdrant — is also the most cacheable for the dominant use case of entity-to-entity similarity lookups. When a procurement engineer views a material's detail page and the UI requests its top-10 similar materials, that exact request will be repeated by every other engineer who views the same material page in the next 4 hours. Without caching, each page view generates a Qdrant ANN search; with caching, only the first view per TTL window incurs that cost. At scale (thousands of daily active users, hundreds of frequently viewed entity pages), caching reduces Qdrant QPS by 60–80% for the similar:type:id query pattern.

Redis Cluster is used as the caching backend, configured in a 3-primary, 3-replica topology with consistent hashing for key distribution. All cache keys are namespaced under the `scse:` prefix. Cache invalidation is event-driven for entity-specific keys: when an entity is updated (detected via Kafka consumer), its `scse:similar:{type}:{id}` and `scse:recommend:{type}:{id}` keys are deleted immediately, ensuring stale similarity lists are not served after an entity update. Query result caches use TTL-only invalidation (no event-driven invalidation) because the query-to-result mapping changes with index updates, making precise invalidation impractical.

| Cache Key Pattern | TTL | Invalidation | Backend |
|-------------------|-----|-------------|---------|
| `scse:similar:{type}:{id}` | 4h | Entity update event | Redis |
| `scse:search:{hash(query+filters)}` | 15min | None (TTL only) | Redis |
| `scse:recommend:{type}:{id}` | 1h | Entity update event | Redis |
| `scse:embedding:{entity_type}:{id}` | 24h | Re-embed event | Redis |
| `scse:collection:stats` | 5min | None | Redis |

Cache hit ratio targets are defined per key pattern based on observed access patterns: `scse:similar:{type}:{id}` targets >60% (entity detail pages are viewed repeatedly by different users), `scse:search:{hash}` targets >30% (query diversity limits reuse), and `scse:embedding:{type}:{id}` targets >80% (embeddings change only on re-embed events, typically weekly). The Redis eviction policy is `allkeys-lru`, ensuring that the most recently used cache entries survive under memory pressure. Cache hit ratios are monitored via Prometheus metrics and a Grafana panel; ratios below target trigger a cache sizing review to determine whether Redis memory should be increased or TTL values adjusted.

---

### 17.6 Multi-Region Architecture

SCSE's multi-region architecture is designed to achieve an RTO (Recovery Time Objective) of less than 5 minutes and an RPO (Recovery Point Objective) of less than 60 seconds for the primary EU-WEST-1 region failure scenario. The architecture uses an active-passive model rather than active-active: all write operations (indexing, feedback, label updates) are directed to the primary region, while the replica region provides read-only search and recommendation capacity that can be promoted to primary in a failover event. This model is chosen over active-active because vector index consistency across regions requires complex conflict resolution that is not supported by Qdrant's current replication model.

The replica region (EU-CENTRAL-1) maintains a Qdrant read-only replica synchronized from the primary via Qdrant's built-in replication protocol. Replication lag is monitored as a Prometheus metric; the target is <5 seconds, and an alert fires if lag exceeds 30 seconds. The embedding workers in both regions operate independently, each calling the geographically closest OpenAI API endpoint, with work items partitioned by entity type to avoid duplicate embedding jobs during normal operation and merged in the failover scenario.

- Primary region: EU-WEST-1 (Ireland) — Qdrant cluster, FAISS jobs
- Replica region: EU-CENTRAL-1 (Frankfurt) — read-only Qdrant replica
- Embedding worker: both regions (OpenAI API endpoints closest region)
- Similarity cache: Redis Cluster with cross-region replication (lag <100ms)
- FAISS indexes: synced via S3 cross-region replication

Failover is triggered manually by the Platform Lead or automatically by the health check monitor after 3 consecutive failed health checks against the primary SCSE API. DNS-based routing uses AWS Route 53 with a health check-evaluated weighted routing policy: the primary region has weight 100 and the replica has weight 0 during normal operation; failover sets primary weight to 0 and replica weight to 100 using the Route 53 API, propagating to clients within the configured 60-second DNS TTL. The Qdrant read replica is promoted to primary using the Qdrant collection re-initialization procedure, and embedding workers in the replica region switch to accepting write-path work items from Kafka. Failover runbooks are maintained in the ICI operations wiki and tested quarterly with a simulated regional outage drill.

---

## Section 18: Risks

### Overview

Risk management for SCSE follows the ICI platform risk framework, which uses a 3×3 probability-impact matrix to produce a normalized risk score (LOW / MEDIUM / HIGH / CRITICAL). Probability is assessed on a three-point scale: Low (unlikely in the next 12 months based on historical incident rates), Medium (possible within 12 months given current trends), and High (likely within 12 months without mitigation). Impact is assessed on a three-point scale: Medium (degraded user experience, <4h recovery), High (significant outage or data quality incident, 4h–24h recovery), and Critical (data breach, regulatory consequence, or multi-day outage).

Risk scores are reviewed quarterly by the Risk Owner (typically the ML Lead or Platform Lead as assigned in the Risk Summary Table) and the Risk Review Board, consisting of the Architecture Lead, Security Architect, and CTO. Each risk is assessed for mitigation effectiveness — whether the described mitigation has been implemented (not merely planned) — and contingency readiness — whether the contingency plan has been tested within the last six months. Risks with HIGH or CRITICAL scores that lack a tested contingency plan are escalated as Priority 1 action items in the quarterly platform risk report.

---

### R1: Embedding Model Deprecation

OpenAI's deprecation of the `text-embedding-3-large` API would invalidate all indexed vectors simultaneously, since embeddings from different model versions occupy incompatible vector spaces — cosine similarity between a query embedding from the new model and a stored embedding from the old model is not meaningful. This is a high-impact event because it requires a full re-index of all five entity type collections, estimated at 48 hours for 10 million entities at current embedding throughput. The business impact during re-indexing is degraded similarity quality (falling back to pgvector exact search or cached precomputed lists), not a complete service outage.

The primary mitigation is interface abstraction: the `EmbeddingProvider` protocol ensures that substituting a different embedding model requires changes only to the provider implementation, not to the SCSE feature extraction or Qdrant indexing logic. A secondary `text-embedding-3-small` provider is maintained in the codebase and tested quarterly; its 1536-dimensional output requires a collection rebuild (different vector dimensionality), but the 3-day rebuild estimate is acceptable as a non-emergency fallback. The self-hosted Sentence Transformers backup is the emergency option for scenarios where the OpenAI API is entirely unavailable.

- **Risk ID:** R1
- **Category:** Technical / Vendor Dependency
- **Probability:** Medium | **Impact:** Critical | **Risk Score:** HIGH
- **Description:** OpenAI deprecates text-embedding-3-large API, invalidating all indexed vectors (incompatible embedding space requires full re-indexing of all collections).
- **Mitigation:** Abstract embedding interface behind `EmbeddingProvider` protocol; maintain fallback to text-embedding-3-small (3072d → 1536d, requires collection rebuild); quarterly model evaluation report; self-hosted Sentence Transformers (all-mpnet-base-v2) as emergency backup with degraded quality.
- **Contingency:** Emergency re-index pipeline tested quarterly; estimated re-index time for 10M vectors: 48h at current throughput.

---

### R2: Qdrant Cluster Split-Brain

A Qdrant cluster split-brain — where a network partition causes cluster nodes to form two independent quorums — is a low-probability but high-impact infrastructure failure mode. Under split-brain conditions, write operations accepted by the minority partition would be lost when the partition heals and the minority partition's state is rolled back to match the majority. This could result in recently indexed entities being missing from search results without any error signals to end users, creating a silent data consistency issue.

Raft consensus protocol with quorum writes (majority of nodes must confirm before acknowledging a write) prevents split-brain data loss: writes to a minority partition are rejected rather than accepted, and the pgvector fallback activates to serve reads while the Qdrant cluster heals. The 30-second failover SLA is achievable because the circuit breaker monitors Qdrant health on a 5-second interval and triggers pgvector routing after three consecutive failures.

- **Risk ID:** R2
- **Category:** Infrastructure / Availability
- **Probability:** Low | **Impact:** High | **Risk Score:** MEDIUM
- **Mitigation:** Raft consensus protocol with quorum writes (majority nodes must confirm); pgvector hot-standby fallback activated within 30s; weekly automated failover drills; Qdrant version pinned with tested upgrade path.

---

### R3: Embedding Drift (Model Update)

Embedding drift is a silent quality degradation risk distinct from model deprecation (R1): the OpenAI API may produce slightly different embeddings for the same input text after a backend model update, even if the model version identifier is unchanged. If these new embeddings are indexed alongside old embeddings in the same Qdrant collection, the collection becomes a mix of two incompatible vector distributions, causing some search queries (those whose query embeddings are from the new distribution) to retrieve poor results while other queries (using old-distribution query embeddings) continue to work correctly. This inconsistency is particularly hard to detect because aggregate recall metrics may not degrade significantly until a majority of queries use the new distribution.

The version stamp approach — recording `model_version` in the Qdrant payload for every embedded entity — enables post-hoc identification of all entities embedded with a specific model version, facilitating targeted re-embedding of stale vectors. The cosine drift monitor samples 1000 random entity pairs nightly and computes the distribution of cosine similarities between their stored embeddings; a shift in this distribution (average drift > 0.15 from baseline) triggers an alert before user-visible quality degradation occurs.

- **Risk ID:** R3
- **Category:** ML / Data Quality
- **Probability:** Medium | **Impact:** High | **Risk Score:** HIGH
- **Description:** New OpenAI model version produces embeddings in incompatible vector space — cosine similarity between old and new vectors becomes meaningless, degrading search quality silently.
- **Mitigation:** Version stamp all embeddings at ingestion time (`model_version` field in Qdrant payload); dual-write period on model upgrades (both old and new embeddings); cosine drift monitor alerts at average drift >0.15 across random sample of 1000 pairs.

---

### R4: Cold Start (Empty Index)

The cold start problem affects every fresh deployment of SCSE: with an empty Qdrant collection, all similarity searches return zero results, making the service appear broken even though it is technically healthy. For a procurement similarity search engine, zero results are indistinguishable from a service failure from the user's perspective and undermine trust in the platform from the moment of go-live.

The seed script approach — pre-loading three years of historical procurement data before accepting production traffic — ensures that the index is never empty when users first encounter the system. The deployment gate (minimum 1000 entities per entity type) is enforced as a Kubernetes init container that queries Qdrant collection stats and exits non-zero if the threshold is not met, preventing the main SCSE API container from starting until the index is adequately populated.

- **Risk ID:** R4
- **Category:** Operational
- **Probability:** Low | **Impact:** Medium | **Risk Score:** LOW
- **Mitigation:** Seed script with 3 years of historical procurement data; cache pre-warm job runs before prod traffic shift; minimum threshold: 1000 entities per entity type enforced by deployment gate.

---

### R5: OpenAI Rate Limiting

OpenAI token-per-minute (TPM) rate limits are a practical constraint for high-volume indexing operations. At 10,000 tokens per entity (average for a fully populated product entity with detailed specification text), embedding 100K entities requires 1 billion tokens — at the `text-embedding-3-large` limit of 8 million tokens per minute, this represents approximately 2 hours of sustained API utilization at full capacity. Any concurrent demand from other platform services using the same OpenAI API key, or any temporary rate limit reduction by OpenAI, can cause queue backlog and delayed availability for newly created or updated entities.

The multi-key strategy (separate OpenAI API keys for normal indexing and burst capacity) doubles the effective TPM budget and provides a clear separation of quota pools between planned indexing operations and emergency re-embed scenarios. The local fallback encoder (Sentence Transformers) is specifically designed for the scenario where OpenAI is rate-limited during a bulk import that must complete within a business deadline — quality is reduced but availability is maintained.

- **Risk ID:** R5
- **Category:** Technical / Vendor Dependency
- **Probability:** High | **Impact:** Medium | **Risk Score:** HIGH
- **Description:** High-volume indexing events (bulk import of 100K+ entities) hit OpenAI token-per-minute limits, causing embedding queue backlog and delayed search availability for new entities.
- **Mitigation:** Exponential backoff with jitter (base 1s, max 60s); queue-based batch processing (100 texts/batch); secondary OpenAI API key for burst capacity; local fallback encoder (Sentence Transformers) for non-critical indexing.

---

### R6: Vector Poisoning Attack

Vector poisoning is a novel attack vector specific to embedding-based search systems. By crafting a document whose text is engineered to produce an embedding vector positioned at the centroid of a cluster of legitimate high-value entities, an attacker can cause the poisoned entity to appear as a top similarity match for all queries in that cluster. In a procurement context, this could be used to systematically surface a fraudulent supplier as a "highly similar" alternative to every trusted supplier in the catalog.

The embedding norm check provides a lightweight first-line defense: legitimate embeddings from OpenAI's text-embedding-3-large model have L2 norms tightly clustered around 1.0 (unit vectors), so an engineered embedding with an abnormal norm is immediately detectable. Anomaly detection on the cosine similarity distribution (z-score alerting for new entities that appear as near-duplicates of an abnormally large fraction of the existing index) catches more sophisticated attacks where the poisoned vector has a legitimate norm but is positioned to maximize coverage.

- **Risk ID:** R6
- **Category:** Security
- **Probability:** Low | **Impact:** High | **Risk Score:** MEDIUM
- **Description:** An attacker with SYSTEM_INTEGRATOR credentials submits a maliciously crafted entity whose embedding vector is positioned to appear as a near-duplicate of many legitimate entities, manipulating recommendation results for all users.
- **Mitigation:** Input text validation (Pydantic v2, 2000 char limit, injection patterns blocked); embedding norm check (reject vectors with L2 norm outside [0.8, 1.2]); anomaly detection on cosine similarities (z-score alert); RBAC tightly controls index:write to SYSTEM_INTEGRATOR only; all indexing operations audited.

---

### R7: PII in Search Queries

Procurement team members may inadvertently include personal data in search queries when searching for quotes associated with specific procurement officers, when referencing supplier contacts by name, or when copying reference numbers from contracts that include personal identifiers. Under GDPR Article 5(1)(e), personal data must not be retained for longer than necessary for the purpose for which it was processed; storing search queries containing personal data in audit logs for 7 years would violate this principle.

The Microsoft Presidio integration provides automated PII detection before query text reaches the audit log. Presidio's Named Entity Recognition (NER) models detect personal names, email addresses, phone numbers, and national ID patterns, replacing each detected span with a typed placeholder (`[REDACTED_PERSON]`, `[REDACTED_EMAIL]`). The search query itself is not modified — PII detection runs only on the copy destined for the audit log — ensuring that searching by person name continues to work as a functional feature while log retention compliance is maintained.

- **Risk ID:** R7
- **Category:** Privacy / Compliance
- **Probability:** Medium | **Impact:** Medium | **Risk Score:** MEDIUM
- **Description:** Users inadvertently include personal data (names, email addresses, contract IDs with personal identifiers) in free-text search queries, violating GDPR data minimization principles if logged without treatment.
- **Mitigation:** Microsoft Presidio PII detection runs on every query before logging; detected PII replaced with `[REDACTED_<entity_type>]` tokens; search still executes on original text but logs store redacted version; 90-day query log retention with SHA-256 anonymization of user_id.

---

### R8: Search Quality Degradation

Search quality degradation is a particularly insidious risk because it manifests gradually (as training data distribution shifts, as new entity types with different characteristics are added, or as the embedding model behavior subtly changes) rather than as a discrete failure event. Without continuous monitoring, a gradual decline in P@1 from 0.82 to 0.68 over six months would go unnoticed until procurement teams begin reporting anecdotally that "the similarity search doesn't work as well as it used to."

The nightly evaluation harness (Section 16.4) converts this continuous monitoring gap into a quantitative signal with defined alerting thresholds. Weekly quality review in the engineering standup provides a human review layer on top of automated alerting, ensuring that threshold-crossing events are not dismissed as transient anomalies without investigation.

- **Risk ID:** R8
- **Category:** ML / Quality
- **Probability:** Medium | **Impact:** Medium | **Risk Score:** MEDIUM
- **Mitigation:** Nightly RecallEvaluationHarness on 500+ labeled pairs per entity type; Prometheus alert `scse_precision_at_1 < 0.70` pages on-call; A/B testing framework for ranking changes; weekly quality review in engineering standup.

---

### R9: FAISS OOM (Out of Memory)

FAISS IndexFlatIP stores all vectors in RAM as contiguous float32 arrays, making its memory consumption predictable but inflexible. At Large tier (50M vectors × 1024 dimensions × 4 bytes = 200GB per collection), a naive FAISS configuration would require 1TB of RAM for all five entity type collections simultaneously — far exceeding the cost envelope of any standard cloud instance. The OOM failure mode is particularly risky because FAISS OOM errors during a nightly batch job can leave the FAISS index in a partially rebuilt state, serving stale or inconsistent results from the previous night's index snapshot.

IVF-PQ (Inverted File Index with Product Quantization) reduces memory consumption by approximately 32× at the cost of a small recall penalty (<1% at comparable HNSW search parameters). For the Large and XLarge tiers, IVF-PQ is mandatory and configured in the FAISS `IndexIVFPQ` class with `nlist=4096` centroids and `M=64` PQ sub-quantizers, producing 64-byte codes per vector versus 4096 bytes for float32. The OOM guard catches Python `MemoryError` exceptions during the nightly job and rolls back to serving the previous FAISS snapshot, logging the failure for next-business-day triage.

- **Risk ID:** R9
- **Category:** Infrastructure / Performance
- **Probability:** Medium | **Impact:** Medium | **Risk Score:** MEDIUM
- **Description:** Full nightly re-index of >1M 1024-dimensional vectors into FAISS IndexFlatIP requires >4GB RAM per collection × 5 collections = 20GB+, exceeding available memory on standard nodes.
- **Mitigation:** IVF-PQ quantization reduces memory ~32×; chunked processing (500K vectors per shard); dedicated FAISS node with 512GB RAM for Large/XLarge tiers; OOM guard with graceful degradation to previous FAISS snapshot.

---

### R10: Qdrant Storage Exhaustion

Qdrant vector storage exhaustion is a capacity planning risk rather than an acute failure risk — it develops over months as entity catalogs grow — but its impact is high because it can cause Qdrant write operations to fail, preventing new entity indexing and blocking the feedback loop for index freshness. At XLarge tier with INT8 quantization (reducing 4 bytes/float to 1 byte/float), 50M vectors × 1024 dimensions × 1 byte = 50GB per collection × 5 collections = 250GB, which is manageable on dedicated Qdrant nodes. The risk applies to scenarios of unexpected entity count growth (e.g., a major supplier acquisition adding 5M product catalog entries overnight) or inadequate quantization adoption.

The S3 cold tier for archived collections enables a tiering strategy: collections for entity types with lower query frequency (e.g., PROCESS entities queried primarily during FAISS batch jobs rather than real-time search) can be moved off Qdrant onto S3-backed read-only snapshots, freeing Qdrant storage capacity for actively queried collections. The 80% storage alert threshold provides at least 4–6 weeks of lead time for capacity expansion under typical growth rates.

- **Risk ID:** R10
- **Category:** Infrastructure / Capacity
- **Probability:** Low | **Impact:** High | **Risk Score:** MEDIUM
- **Description:** At XLarge tier, 50M vectors × 1024 dimensions × 4 bytes per float32 = ~200GB per collection × 5 entity type collections = ~1TB raw vector storage, plus payload overhead.
- **Mitigation:** INT8 scalar quantization (4× storage reduction, <1% recall loss); on-disk payload storage for cold vectors (accessed <1/week); S3 cold tier for archived collections; storage alert at 80% capacity; quarterly capacity planning review.

---

### R11: Query Latency Spikes

Qdrant's background segment compaction is necessary for maintaining ANN search quality over time (merging small segments improves HNSW graph connectivity), but the compaction process is I/O-intensive and competes with concurrent search operations for disk bandwidth and CPU caches. During active indexing periods — such as when the Kafka consumer is processing a large backlog of entity updates — compaction and indexing can co-occur, causing P99 latency to spike 2–5× above normal for search queries that must access recently compacted segments.

The node pool separation strategy — dedicated Qdrant nodes for indexing traffic (receiving writes from the SCSE API) and separate read-only replica nodes serving all search requests — eliminates the compaction interference risk for the search path. Read replicas do not perform compaction; they receive compacted segments from the primary after compaction completes. The `indexing_threshold` tuning defers triggering compaction on the primary nodes until the off-peak maintenance window (02:00–04:00 UTC), when search traffic is minimal.

- **Risk ID:** R11
- **Category:** Performance
- **Probability:** Medium | **Impact:** Medium | **Risk Score:** MEDIUM
- **Description:** Qdrant background segment compaction during high indexing load causes P99 latency spikes (up to 2–5× normal) as compaction threads compete with search threads for I/O.
- **Mitigation:** Separate Qdrant node pools for indexing and search traffic; read-only replicas serve all search requests during compaction windows; `indexing_threshold` tuned to defer compaction to off-peak hours (02:00–04:00 UTC); pre-alerting on compaction queue depth.

---

### R12: Training Label Bias

Procurement team bias in training labels poses a long-term ML quality risk that compounds over time: if early feedback labels disproportionately mark familiar European steel suppliers as "highly similar" to each other while marking functionally equivalent Asian suppliers as "not similar," the fine-tuned encoder will learn to embed supplier origin as a similarity feature, embedding a geographic bias into the similarity space. Over multiple retraining cycles, this bias strengthens, progressively reducing the diversity of supplier recommendations and potentially violating procurement diversity policies.

The inter-annotator agreement requirement (Cohen's kappa > 0.7 across a minimum of three annotators per entity pair) is the primary control, ensuring that no single annotator's bias can dominate the training signal. The bias metrics in monthly evaluation reports — tracking the supplier country distribution and price range distribution of top similarity results — provide an observable signal that enables the Data Steward to identify and remediate systematic bias before it significantly affects procurement outcomes.

- **Risk ID:** R12
- **Category:** ML / Ethics
- **Probability:** Medium | **Impact:** Medium | **Risk Score:** MEDIUM
- **Description:** Procurement team feedback (SCSE_PROCUREMENT role) may be systematically biased toward familiar suppliers or materials, causing the fine-tuned encoder to amplify existing procurement patterns rather than surface genuinely similar alternatives.
- **Mitigation:** Blind evaluation labels from diverse labeler pool (minimum 3 annotators per pair); inter-annotator agreement tracking (Cohen's kappa > 0.7 required); bias metrics in monthly evaluation report (supplier country distribution, price range distribution of top results); data steward review of label distribution quarterly.

---

### R13: Cross-Entity Type Confusion

Cross-entity type confusion occurs when the embedding model places entities from different entity types (e.g., MATERIAL "Stainless Steel 316L" and PRODUCT "Stainless Steel Rod 316L Ø10mm 500mm") in closely adjacent regions of the vector space, because their text representations are semantically similar. While this adjacency is semantically correct from a pure language model perspective, it is incorrect from a procurement similarity perspective: a search for similar materials should not surface products, and vice versa.

The strict collection isolation approach — one Qdrant collection per entity type, never shared — ensures that entity type boundaries are enforced at the index level, not just in application logic. Applying the entity type filter at the middleware layer (rather than relying on application code to add it) provides a second enforcement layer that cannot be bypassed by application-level bugs.

- **Risk ID:** R13
- **Category:** Technical / Data Quality
- **Probability:** Low | **Impact:** Medium | **Risk Score:** LOW
- **Description:** If fused vectors from different entity types (e.g., MATERIAL and PRODUCT) happen to land in similar regions of the embedding space due to overlapping feature text, recommendations may incorrectly suggest materials when products are expected.
- **Mitigation:** Strict separate Qdrant collections per entity type (no shared collection); `entity_type` filter always applied at Qdrant query level (enforced at middleware, not application logic); cross-entity similarity only via explicit CrossEntityRanker with confirmed entity type mapping; integration test asserting entity type isolation.

---

### R14: Multi-Tenant Data Leakage

Multi-tenant data leakage is the highest-severity risk on the SCSE risk register. A procurement cost intelligence platform serving multiple business units or subsidiaries necessarily stores sensitive competitive information about each tenant's supplier relationships and cost structures. A single missing `tenant_id` filter in a Qdrant query — trivially possible if a developer forgets to apply the filter in a new endpoint or code path — would expose one tenant's similarity results to another tenant's queries without any visible error.

The middleware-layer enforcement of `tenant_id` filtering is the primary control: the `TenantIsolationMiddleware` intercepts all outgoing Qdrant queries and injects the `tenant_id` filter from the authenticated user's JWT claims before the query reaches Qdrant. This middleware runs as a Qdrant client wrapper, meaning it is structurally impossible for application code to make an un-filtered Qdrant query without explicitly bypassing the wrapper — an action that would be immediately visible in code review. The cross-tenant isolation integration tests (run on every CI build, not just nightly) ensure that any code change that weakens tenant isolation is caught before merge.

- **Risk ID:** R14
- **Category:** Security / Compliance
- **Probability:** Low | **Impact:** Critical | **Risk Score:** HIGH
- **Description:** A misconfiguration in Qdrant payload filtering conditions (e.g., missing `tenant_id` filter due to a code bug) could expose similarity results from one tenant's procurement data to another tenant's users, constituting a critical data breach under GDPR.
- **Mitigation:** `tenant_id` filter validated and injected at middleware layer (not application code) — cannot be bypassed; integration test suite includes cross-tenant isolation assertions (run on every CI build); Qdrant collection-per-tenant option available for highest-sensitivity deployments; quarterly security audit with penetration testing of multi-tenant filter logic.

---

### Risk Summary Table

| Risk ID | Description | Probability | Impact | Score | Owner |
|---------|-------------|------------|--------|-------|-------|
| R1 | Embedding Model Deprecation | Medium | Critical | HIGH | ML Lead |
| R2 | Qdrant Cluster Split-Brain | Low | High | MEDIUM | Platform |
| R3 | Embedding Drift | Medium | High | HIGH | ML Lead |
| R4 | Cold Start | Low | Medium | LOW | DevOps |
| R5 | OpenAI Rate Limiting | High | Medium | HIGH | ML Lead |
| R6 | Vector Poisoning | Low | High | MEDIUM | Security |
| R7 | PII in Queries | Medium | Medium | MEDIUM | Privacy |
| R8 | Search Quality Degradation | Medium | Medium | MEDIUM | ML Lead |
| R9 | FAISS OOM | Medium | Medium | MEDIUM | Platform |
| R10 | Qdrant Storage Exhaustion | Low | High | MEDIUM | Platform |
| R11 | Query Latency Spikes | Medium | Medium | MEDIUM | Platform |
| R12 | Training Label Bias | Medium | Medium | MEDIUM | ML Lead |
| R13 | Cross-Entity Confusion | Low | Medium | LOW | Backend |
| R14 | Multi-Tenant Leakage | Low | Critical | HIGH | Security |

---

## Section 19: Roadmap

### Overview

The SCSE delivery roadmap is organized into four sequential phases spanning 64 weeks (32 two-week sprints), progressing from foundational infrastructure through full entity coverage, custom ML intelligence, and production-scale hardening. The phase structure reflects a deliberate value delivery strategy: each phase produces a deployable, usable increment of the system, allowing procurement stakeholders to begin deriving value from Phase 1 (basic product and material similarity search) while Phase 2 and beyond extend coverage and quality. This incremental approach also reduces risk by ensuring that the architectural foundations are validated under real production load before the more complex ML components are layered on top.

The 2-week sprint cadence provides the governance rhythm for the roadmap: each sprint has defined deliverables and exit criteria that must be demonstrated to the product owner before the sprint is closed. Exit criteria are written as observable, testable statements (not aspirational goals) — for example, "Hybrid search P@5 >= 0.65 on 100-label test set" is measurable and binary, while "Implement hybrid search" is not. The exit criteria also serve as the acceptance criteria for sprint reviews, reducing the risk of incomplete work carrying forward across sprint boundaries.

Business value milestones map to phase completion: Phase 1 completion enables the first internal user pilot (procurement team for a single business unit, product and material search only); Phase 2 completion enables general availability across all entity types and the full procurement workflow; Phase 3 completion delivers the custom ML encoder and cross-entity intelligence that differentiate SCSE from commodity vector search offerings; Phase 4 completion delivers the production hardening, multi-region resilience, and security posture required for enterprise-wide rollout including regulated subsidiaries.

---

### Phase 1: Foundation (Sprints 1–8, Weeks 1–16)

Phase 1 establishes the core technical foundation of SCSE: the PostgreSQL schema, the first two entity type feature extractors, the Qdrant cluster, the OpenAI embedding pipeline, and a basic hybrid search implementation for products and materials. The goal of Phase 1 is to prove the end-to-end architecture — from raw entity data in PostgreSQL through embedding generation, Qdrant indexing, and similarity search — with two entity types before investing in the breadth (all five entity types) that Phase 2 delivers. A working Phase 1 system can be handed to a small internal user group (5–10 procurement engineers) for pilot evaluation before the platform-wide rollout decision.

The most technically critical sprint in Phase 1 is S5 (hybrid search), where the RRF fusion of dense Qdrant ANN results and sparse BM25 keyword results is implemented and calibrated. The exit criterion (P@5 >= 0.65 on 100-label test set) is deliberately set below the long-term production target (P@5 >= 0.80) to reflect the small training dataset and untuned configuration of an early-phase system. This creates space for iterative improvement in Phases 2 and 3 without treating Phase 1 as a failure if it meets its defined exit criteria.

| Sprint | Duration | Deliverable | Exit Criteria |
|--------|----------|-------------|---------------|
| S1 | Weeks 1–2 | Repository setup, PostgreSQL schema (scse schema), pgvector extension, HNSW index config | Schema migration passes, pgvector queries return results |
| S2 | Weeks 3–4 | Feature extractors: ProductFeatureExtractor, MaterialFeatureExtractor + text embedding templates | Unit tests >85%, feature vectors generated for test dataset |
| S3 | Weeks 5–6 | Qdrant cluster deployment, collection creation (products, materials), basic upsert + search | Qdrant healthcheck passes, 1000 test vectors searchable |
| S4 | Weeks 7–8 | EmbeddingPipeline integration (text-embedding-3-large) + OpenAI rate limiter + retry logic | 10K entities embedded in <2h, rate limit errors <1% |
| S5 | Weeks 9–10 | Basic hybrid search: Qdrant dense + BM25 sparse + RRF fusion | Hybrid search P@5 >= 0.65 on 100-label test set |
| S6 | Weeks 11–12 | FAISS IndexBuilder + BatchSimilarityJob + Airflow DAG (nightly) | Nightly job completes in <4h for 100K entities |
| S7 | Weeks 13–14 | REST API (FastAPI): /search/products, /search/materials, /index/entity | API latency P95 <300ms, integration tests pass |
| S8 | Weeks 15–16 | JWT auth, RBAC middleware (7 roles), basic audit logging to PostgreSQL | Auth unit tests pass, all endpoints require JWT |

---

### Phase 2: Full Entity Coverage (Sprints 9–16, Weeks 17–32)

Phase 2 extends SCSE to all five entity types, implements the caching and re-ranking infrastructure that enables the production performance SLAs, and establishes the feedback loop that will power the custom ML encoder in Phase 3. Expanding from two entity types to five introduces new feature extraction complexity — quote entities have fundamentally different feature schemas from material entities — and requires careful Qdrant collection configuration decisions (shard count, replication factor) that will be difficult to change post-seeding without a full collection rebuild.

The feedback loop (sprints S15–S16) is strategically placed at the end of Phase 2 rather than Phase 3 because the quality of Phase 3's custom encoder is directly proportional to the quantity and quality of training labels collected in Phase 2. Starting label collection at Week 29 (S15) with a full user base (all five entity types, all procurement teams) provides approximately 20 weeks of label collection before the encoder training sprint (S17), which the ML team estimates will yield 2000–5000 high-quality labeled pairs — sufficient for initial encoder training with transfer learning from the OpenAI base model.

| Sprint | Duration | Deliverable | Exit Criteria |
|--------|----------|-------------|---------------|
| S9 | Weeks 17–18 | QuoteFeatureExtractor, ProcessFeatureExtractor, SupplierFeatureExtractor | Extractors produce valid vectors, unit tests >85% |
| S10 | Weeks 19–20 | Qdrant collections: quotes, processes, suppliers + seeding | All 5 collections populated, Qdrant healthy |
| S11 | Weeks 21–22 | /search/quotes, /search/processes, /search/suppliers endpoints | End-to-end tests pass for all 5 entity types |
| S12 | Weeks 23–24 | Re-ranker: MMR (diversity) + BusinessRulesRanker (preferred suppliers) | MMR unit tests pass, re-rank adds <50ms |
| S13 | Weeks 25–26 | ConfidenceCalculator (5 components: semantic, structural, temporal, popularity, label) | Confidence grades calibrated on 200-label set |
| S14 | Weeks 27–28 | Similarity cache (Redis) + precomputed table in PostgreSQL + cache warming | Cache hit ratio >50% for similar:type:id keys |
| S15 | Weeks 29–30 | Feedback API: POST /feedback/relevance, POST /feedback/label | Feedback stored in training_labels, audit logged |
| S16 | Weeks 31–32 | Kafka consumers: entity.updated → auto re-embed + re-index | Consumer lag <5min, index fresh within 5min of update |

---

### Phase 3: Intelligence (Sprints 17–24, Weeks 33–48)

Phase 3 is the ML differentiation phase: it delivers the custom `StructuredFeatureEncoder` that fuses structured procurement metadata with OpenAI text embeddings to produce domain-specific 1024-dimensional vectors that outperform pure text embedding for procurement similarity tasks. The encoder is implemented in PyTorch with an MLP architecture trained on the labeled pairs collected during Phase 2, using `text-embedding-3-large` vectors as a frozen backbone and learning a 512-dimensional structured feature projection that is concatenated with a projected text embedding before the final 1024-dimensional fusion layer.

Phase 3 also establishes the production ML operations infrastructure: MLflow experiment tracking (S23) ensures that every encoder training run is reproducible and comparable, enabling the ML team to conduct controlled ablation studies on feature engineering changes. Confidence calibration (S24) applies Platt scaling to convert raw confidence scores into calibrated probabilities — a critical step for enterprise adoption, where stakeholders need to be able to interpret "92% similar" as a meaningful probability estimate rather than an arbitrary relative score.

| Sprint | Duration | Deliverable | Exit Criteria |
|--------|----------|-------------|---------------|
| S17 | Weeks 33–34 | StructuredFeatureEncoder MLP (PyTorch, 512d output) + training pipeline | Encoder improves P@5 by >5% on held-out set |
| S18 | Weeks 35–36 | FusionAttentionLayer: text 3072d + structured 512d → 1024d fused vector | Fusion vectors stored in Qdrant, recall improvement measured |
| S19 | Weeks 37–38 | RecallEvaluationHarness + Airflow nightly eval DAG + Prometheus quality metrics | Nightly report available, P@1 alert wired up |
| S20 | Weeks 39–40 | CrossEntityRanker: product ↔ material, quote ↔ process cross-type search | Cross-entity search tested with 50 labeled pairs |
| S21 | Weeks 41–42 | /recommend/* endpoints using Qdrant recommend API + collaborative filtering | Recommendations tested, latency P95 <200ms |
| S22 | Weeks 43–44 | QueryExpander: domain synonym dictionary (500+ procurement terms) | Query expansion improves recall by >8% on synonym test set |
| S23 | Weeks 45–46 | MLflow integration: encoder versioning, experiment tracking, model registry | All encoder experiments tracked, rollback tested |
| S24 | Weeks 47–48 | Confidence calibration (Platt scaling) + monthly recalibration DAG | Calibration ECE < 0.05 on held-out validation set |

---

### Phase 4: Scale & Production (Sprints 25–32, Weeks 49–64)

Phase 4 transforms SCSE from a capable platform service into a production-hardened, enterprise-grade infrastructure component. The scaling work (S25–S27) delivers the Kubernetes HPA configuration, multi-region Qdrant deployment, and GPU-accelerated FAISS re-indexing that are prerequisites for the XLarge tier. The intelligence work (S28–S29) adds feature drift detection and A/B testing capabilities that enable ongoing model improvement in production without manual rollback risk. The security hardening work (S30–S31) delivers the final compliance controls — Presidio PII detection and third-party penetration testing — required for SOC 2 Type II audit readiness.

Sprint S32 (Weeks 63–64) is deliberately forward-looking: rather than delivering production features, it initiates the long-term initiative planning for the features that will define SCSE v2.0. Architecture Decision Records (ADRs) for multimodal search and real-time collaborative filtering are approved in sprint review, and spike implementations (proof-of-concept code branches) are merged to the main repository for team familiarization. This ensures that the post-Phase 4 velocity to v2.0 feature delivery is maximized by early architectural investment.

| Sprint | Duration | Deliverable | Exit Criteria |
|--------|----------|-------------|---------------|
| S25 | Weeks 49–50 | Kubernetes HPA for SCSE API (3→20 pods) + EmbeddingWorker (5→30 pods) | HPA scales under synthetic load, no dropped requests |
| S26 | Weeks 51–52 | Multi-region Qdrant (EU-WEST-1 primary + EU-CENTRAL-1 read replica) | Replica lag <5s, failover tested |
| S27 | Weeks 53–54 | FAISS GPU support for nightly batch re-indexing (NVIDIA A100) | Re-index time for 10M vectors: <8h |
| S28 | Weeks 55–56 | Feature drift detection (PSI monitoring) + auto-retrain trigger via Airflow | PSI alert fires on simulated drift injection |
| S29 | Weeks 57–58 | A/B testing framework for ranking algorithm experiments | First ranking A/B test live with 10% traffic split |
| S30 | Weeks 59–60 | PII detection middleware (Presidio) on all query text + audit trail | PII redaction tested with 50 PII-containing queries |
| S31 | Weeks 61–62 | Security hardening: vector poisoning detection, penetration test by third party | Pentest report: no critical findings; poisoning detection active |
| S32 | Weeks 63–64 | Long-term initiative kickoff: multimodal search (CAD), real-time CF planning | Architecture ADR approved, spike implementations merged |

---

### Long-term Initiatives (Post v1.0)

Beyond Phase 4, the SCSE roadmap envisions a series of strategic initiatives that extend the platform's capability from procurement cost similarity into the broader domain of industrial intelligence. These initiatives are categorized as long-term because they each require foundational research, significant infrastructure investment, or dependency on capabilities not yet available in the ICI platform. They are documented here to communicate strategic direction to stakeholders and to ensure that architectural decisions in Phases 1–4 do not inadvertently foreclose long-term options. Each initiative is assigned a Product Owner responsible for maintaining the initiative's backlog and stakeholder alignment.

The long-term initiatives represent a progression from reactive similarity search (finding what already exists in the catalog) to proactive procurement intelligence (predicting what should be in the catalog, surfacing non-obvious savings opportunities, and enabling conversational procurement workflows). The collective impact of LT-1 through LT-6, if delivered, would transform SCSE from a search utility into a core procurement AI assistant — the foundation of the ICI platform's long-term competitive differentiation.

---

**LT-1: Multimodal Search (CAD Drawing Similarity)**

Index CAD drawings and technical specifications using the ViT-B/32 visual encoder, enabling similarity search based on geometric and visual features rather than text description alone. Match engineering components by technical drawing similarity rather than by textual metadata, enabling procurement teams to find identical or substitute components across supplier catalogues from a drawing scan rather than a part number search. This capability addresses a major gap in the current SCSE: two components that are physically identical but described using different terminology (e.g., "hexagonal cap screw M6×20 ISO 4762" vs. "socket head cap screw 6mm×20mm DIN 912") are not detected as similar by text-based search, but are immediately identified as similar by visual encoder comparison of their technical drawings.

---

**LT-2: Real-time Collaborative Filtering**

Implement "engineers who quoted this component also quoted..." recommendation patterns based on co-occurrence analysis in RFQ (Request for Quotation) history, using a collaborative filtering model trained on the ICI platform's historical quote co-occurrence matrix. Deploy via the Qdrant collaborative filtering API (which natively supports co-occurrence-based recommendations without explicit similarity computation) and a user interaction matrix updated daily from Kafka quote event streams. This initiative enables procurement teams to discover non-obvious substitutions and bundle opportunities — for example, components that are frequently quoted together may benefit from consolidated supplier negotiations, and components that frequently substitute for each other during RFQ negotiation represent latent cost savings opportunities.

---

**LT-3: SCSE Copilot (Natural Language Search)**

Deliver a conversational search interface enabling natural language procurement queries such as "find me a supplier like Acme GmbH but in Poland with better on-time delivery" or "show me materials similar to what we used in project ALPHA-2023 but 30% cheaper." Parse procurement officer intent using Claude claude-sonnet-4-6 as the LLM backbone, translating natural language into structured SCSE query parameters (entity type, filter conditions, similarity constraints, sort order) that are then executed against the existing SCSE search API. This initiative dramatically lowers the technical barrier to using SCSE for procurement officers who are expert in their domain but not in query formulation — the largest addressable user group in the ICI platform.

---

**LT-4: Cost Impact Ranking**

Weight SCSE similarity results by predicted cost savings relative to the current quote, integrating real-time cost calculation from the Cost Calculation Engine (CHE) to produce a composite "similarity × expected saving" score that ranks results by procurement value rather than pure semantic similarity. Present procurement teams with alternatives sorted by expected impact — showing the most cost-impactful alternatives first rather than the most semantically similar — while preserving the similarity score as a secondary display metric to maintain trust and explainability. This initiative directly addresses the gap between "technically similar" and "commercially valuable" in similarity search results, making SCSE recommendations immediately actionable in procurement negotiations.

---

**LT-5: Federated Search**

Enable similarity search across multiple subsidiary databases without centralizing sensitive procurement cost data in the ICI platform, using privacy-preserving federated ANN search protocols based on secure aggregation of distributed similarity scores. Each subsidiary maintains its own Qdrant cluster with its proprietary pricing data; the federated search layer aggregates similarity rankings from each subsidiary's local search without any subsidiary's raw vectors or payloads leaving its local cluster. This initiative enables group-level procurement intelligence — identifying when two subsidiaries are paying different prices for essentially identical components and flagging consolidation opportunities — without violating subsidiary data sovereignty agreements or the contractual confidentiality provisions that govern inter-subsidiary data sharing.

---

**LT-6: Explainability Layer**

Deliver SHAP (SHapley Additive exPlanations) value analysis on `StructuredFeatureEncoder` inputs to explain why two procurement entities are determined to be similar, producing human-readable explanations of the similarity drivers for each search result. Surface these explanations in the SCSE API response as a `similarity_explanation` field: for example, "These materials are 92% similar because: material_class (40% contribution), tensile_strength (25%), surface_finish (20%), temperature_rating (15%)." This explainability capability enables data stewards to audit and validate similarity recommendations, procurement teams to understand and trust the basis for SCSE suggestions, and compliance officers to demonstrate to auditors that procurement decisions are based on explainable, non-discriminatory criteria rather than opaque ML model outputs.

---

## Document Control

| Field | Value |
|-------|-------|
| Document ID | SCSE-DOC-04 |
| Version | 1.0.0 |
| Created | 2026-06-20 |
| Last Updated | 2026-06-20 |
| Author | Architecture & ML Team |
| Reviewer | Security Architect, Platform Lead |
| Approved By | CTO |
| Classification | Internal — Confidential |
| Next Review | 2026-12-20 |

---

*This document is part of the Industrial Cost Intelligence (ICI) platform architecture documentation suite. For related documents see: 01-overview-architecture.md, 02-data-model-api.md, 03-algorithms-performance.md.*
