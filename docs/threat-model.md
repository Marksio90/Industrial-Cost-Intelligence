# ICI Threat Model — STRIDE Analysis

**System:** Industrial Cost Intelligence Platform  
**Version:** 1.0  
**Date:** 2026-06-21  
**Classification:** Internal / Security Sensitive

---

## 1. Scope and System Description

The ICI platform is a multi-tenant SaaS application that processes sensitive industrial cost data, interacts with external suppliers via email, and exposes an AI-powered REST API. The production deployment runs on AWS EKS (Kubernetes) with the following trust boundaries:

```
Internet
  │
  ▼
[AWS ALB / nginx Ingress]  ← TLS termination
  │
  ├── /api/v1/*  →  [Backend Service]  →  [PostgreSQL]
  │                       │             →  [Redis]
  │                       │             →  [Qdrant]
  │                       │             →  [ML Inference Service]
  │                       │             →  [RFQ Agent Service]  →  SMTP (Internet)
  │
  └── /grafana/*  →  [Grafana]  ←  [Prometheus]  ←  all services
```

**Assets:**
- Cost records (trade secrets — commercially sensitive)
- Supplier relationships and pricing data
- ML models and training data
- JWT tokens and session state
- Encryption keys (KEK ring)
- SMTP credentials
- Customer PII (names, emails, company details)

---

## 2. Trust Boundaries

| Boundary | Description |
|----------|-------------|
| TB-1 | Internet ↔ nginx/ALB ingress |
| TB-2 | nginx ↔ backend pods (cluster-internal) |
| TB-3 | Backend ↔ PostgreSQL/Redis/Qdrant |
| TB-4 | Backend ↔ ML Inference / RFQ Agent |
| TB-5 | RFQ Agent ↔ external SMTP server |
| TB-6 | EKS pods ↔ AWS Secrets Manager (IRSA) |
| TB-7 | Tenant A data ↔ Tenant B data (logical boundary) |

---

## 3. Data Flow Diagram (DFD Level 1)

```
[User Browser] ──HTTPS──▶ [nginx Ingress (TB-1)]
                                │
                    ┌──────────▼──────────┐
                    │   Backend FastAPI    │
                    │  (auth + RBAC/ABAC) │
                    └──────┬──────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
     [PostgreSQL]      [Redis]          [Qdrant]
     (cost data,       (sessions,       (vector
      audit log)        cache,           embeddings)
                        queues)
          │
          ▼
    [ML Inference] ──── LightGBM/XGBoost models
          │
    [RFQ Agent] ──SMTP(TB-5)──▶ [Supplier Email]
          │
    [Anthropic API] (external)
```

---

## 4. STRIDE Analysis

### 4.1 Spoofing

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| S-01 | JWT token forgery | Backend auth | RS256 signing; short expiry (15 min); JTI revocation set in Redis | Low |
| S-02 | Refresh token theft and replay | Auth endpoints | Token family rotation; reuse of revoked token invalidates entire family | Low |
| S-03 | OAuth2 CSRF / state forgery | OAuth2 flow | PKCE S256 + cryptographic state parameter; state stored server-side | Low |
| S-04 | Impersonation of internal services | Inter-pod comms | NetworkPolicy restricts pod-to-pod traffic; mTLS at service mesh layer (roadmap) | Medium |
| S-05 | Supplier email spoofing in RFQ replies | RFQ Agent parser | DKIM/SPF validation on incoming email; parser validates sender domain against supplier record | Medium |
| S-06 | Fake supplier quotes injected via email | RFQ Agent | Quote tied to RFQ session ID; supplier authenticated by expected email address; anomaly scoring | Medium |

### 4.2 Tampering

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| T-01 | Modification of cost records | PostgreSQL | Row-level audit with chain hash; AuditAction.RECORD_UPDATE logged; immutable audit_events table | Low |
| T-02 | Audit log manipulation | audit_events table | SHA-256 chain hash per tenant; Redis stream as secondary record; PostgreSQL write-only audit role | Low |
| T-03 | ML model substitution | ML model storage | Model registry with hash verification at load time; signed artifacts in S3 | Medium |
| T-04 | Config injection via environment | K8s Secrets | Secrets sourced from AWS Secrets Manager via ESO; immutable ConfigMaps; IRSA limits SM access | Low |
| T-05 | Database record tampering via SQL injection | Backend API | Parameterised SQLAlchemy queries; SQLInjectionGuard heuristic middleware (belt-and-braces) | Low |
| T-06 | Encrypted field replay (cut-and-paste) | EncryptedString | Fresh random DEK + random IV per encrypt() call; AES-GCM authentication tag covers ciphertext | Low |

### 4.3 Repudiation

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| R-01 | User denies performing data export | Backend | AuditAction.BULK_EXPORT logged with actor_id, actor_ip (pseudonymised), timestamp, chain hash | Low |
| R-02 | User denies RFQ execution or quote approval | RFQ Agent | AuditAction.RFQ_EXECUTE + QUOTE_APPROVED with actor context and chain hash | Low |
| R-03 | Admin denies role or config change | Admin endpoints | AuditAction.ROLE_CHANGE + TENANT_CONFIG_CHANGE; mandatory @audited decorator on admin routes | Low |
| R-04 | Service denies emitting an ML inference | ML Service | AuditAction.ML_MODEL_DEPLOYED + structured log with model_version, trace_id | Medium |

### 4.4 Information Disclosure

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| I-01 | Cost data cross-tenant leak | Backend API | tenant_id filter on every query; ABAC deny_cross_tenant_access policy; RLS roadmap | Low |
| I-02 | Encryption key exposure | KeyStore | Keys loaded from AWS Secrets Manager at runtime; never logged; not in environment vars in prod; in-memory only | Low |
| I-03 | PII in logs | structlog | `_SENSITIVE_KEYS` redaction processor; IP one-way hashed (SHA-256) before audit storage | Low |
| I-04 | JWT payload readable by client | JWT | JWTs are signed (not encrypted); payload contains no sensitive fields beyond role names | Low |
| I-05 | Internal error details leaked to client | FastAPI | Exception handlers return generic messages; full stack trace only in structured logs (internal) | Low |
| I-06 | Secrets in pod environment | K8s / ESO | External Secrets Operator projects SM secrets as K8s Secret volumes; no secrets in ConfigMaps | Low |
| I-07 | Inference data sent to Anthropic API | LLM calls | Only synthetic/derived cost descriptions sent; no raw PII or full supplier names in prompts (roadmap: prompt scrubber) | Medium |
| I-08 | Qdrant index exposes embedding patterns | Qdrant | Network policy limits access to backend pods only; no public Qdrant endpoint; API key required | Low |
| I-09 | Timing attack on token comparison | Auth | `hmac.compare_digest` used in token hash comparison | Low |
| I-10 | MLflow artifact access | MLflow admin ingress | IP allowlist on MLflow ingress; basic auth; separate NetworkPolicy | Low |

### 4.5 Denial of Service

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| D-01 | API flood from single IP | nginx + backend | Per-path rate limiting: /auth 10/60s, /rfq 30/60s, /api 120/60s; Redis sliding window; nginx rate_limit zones | Low |
| D-02 | Brute-force login | /api/v1/auth/* | 10 req/60s rate limit; AuditAction.BRUTE_FORCE_DETECTED; account lockout after 10 failures | Low |
| D-03 | Large payload upload | Backend | Content-Length guard (50 MB); RequestValidationMiddleware rejects before body read | Low |
| D-04 | Long URL / query string abuse | Backend | Max URL length 8192; RequestValidationMiddleware rejects oversized queries | Low |
| D-05 | Worker queue exhaustion | ARQ worker | KEDA ScaledObject autoscales on queue depth; ResourceQuota caps total pod count | Medium |
| D-06 | Postgres connection exhaustion | Backend | PgBouncer connection pooling (roadmap); SQLAlchemy pool_overflow monitored; alert fires at >80% | Medium |
| D-07 | RFQ email flood to suppliers | RFQ Agent | Rate limit: AuditAction.RATE_LIMIT_EXCEEDED; per-tenant RFQ execution limit enforced by ABAC policy | Low |
| D-08 | Qdrant memory exhaustion via bulk insert | Qdrant | Request rate limiting; payload size validation; Qdrant runs with resource limits (2 CPU / 4Gi) | Medium |

### 4.6 Elevation of Privilege

| ID | Threat | Component | Countermeasure | Residual Risk |
|----|--------|-----------|----------------|---------------|
| E-01 | Horizontal privilege escalation (tenant switch) | Backend RBAC/ABAC | `can_access_tenant()` check; ABAC deny_cross_tenant_access; tenant_id extracted from verified JWT only | Low |
| E-02 | Vertical privilege escalation (role upgrade) | Admin endpoints | ROLE_CHANGE requires MANAGE_USERS permission; logged; no self-grant allowed | Low |
| E-03 | JWT role claim tampering | JWT | Roles embedded in signed JWT; cannot be modified without private key; `require_role()` validates decoded payload | Low |
| E-04 | SSRF via RFQ email webhook | RFQ Agent | Allow-list of resolvable supplier domains; no arbitrary URL fetch from user input | Medium |
| E-05 | Container escape to node | K8s pods | seccompProfile: RuntimeDefault; capabilities.drop: [ALL]; readOnlyRootFilesystem; runAsNonRoot | Low |
| E-06 | Kubernetes RBAC escalation | Service accounts | All ServiceAccounts have `automountServiceAccountToken: false`; minimal ClusterRole for Prometheus only | Low |
| E-07 | ABAC policy bypass via metadata manipulation | ABAC engine | PolicyContext built server-side from verified JWT and request; no client-controlled fields accepted | Low |
| E-08 | ML model deployment by non-authorised user | ML endpoints | `require_permission(Permission.ML_DEPLOY_MODEL)` gate; ABAC deny_ml_deploy_outside_eu policy | Low |

---

## 5. Attack Scenarios

### Scenario A — Compromised Analyst Account

1. Attacker obtains analyst credentials (phishing).
2. Analyst role has `COST_READ` but not `COST_EXPORT`.
3. Attacker attempts bulk export → ABAC `deny_cross_tenant_access` + RBAC `require_permission(BULK_EXPORT)` blocks attempt.
4. AuditAction.PERMISSION_DENIED logged with chain hash.
5. IncidentDetector fires `permission_denied_spike` rule → PagerDuty alert.

**Residual risk:** Low — blast radius limited to analyst's own tenant and read operations.

### Scenario B — Malicious Supplier Email Injection

1. Supplier sends crafted quote email with SQL payload in the price field.
2. RFQ parser normalises numeric fields; text fields go through sanitiser.
3. Even if raw text reaches DB, SQLAlchemy parameterised queries prevent injection.
4. SQLInjectionGuard middleware catches pattern in query params if exposed via URL.
5. Quote is flagged with anomaly_score > 0.8 → requires HITL approval.

**Residual risk:** Low — multiple independent layers of defence.

### Scenario C — Encryption Key Exfiltration

1. Attacker gains code execution in a backend pod.
2. Keys are in process memory (Python dict), not in env vars or files.
3. Attacker must read `/proc/<pid>/mem` — blocked by seccompProfile (ptrace denied).
4. AWS Secrets Manager logs API call to CloudTrail → anomaly detection.
5. On key rotation, `rotate_and_reload()` invalidates cache; all field DEKs can be re-wrapped without re-reading data.

**Residual risk:** Medium — in-memory key extraction requires container escape (mitigated by seccomp/capabilities).

### Scenario D — Token Refresh Token Theft

1. Attacker intercepts refresh token (e.g., XSS, network intercept).
2. Attacker calls `/auth/refresh` with stolen token.
3. If original user's client also tries to refresh, reuse detected → entire family revoked.
4. AuditAction.BRUTE_FORCE_DETECTED emitted; user forced to re-authenticate.
5. Access token lifetime is 15 minutes, limiting window of use.

**Residual risk:** Low — family invalidation limits damage from single token theft.

---

## 6. Mitigations Summary Matrix

| Layer | Control | Implementation |
|-------|---------|----------------|
| Network | TLS 1.3 enforced | nginx + cert-manager (HSTS max-age=2y) |
| Network | Ingress rate limiting | nginx `limit_req_zone` + RateLimitMiddleware |
| Network | Pod-to-pod isolation | NetworkPolicy default-deny-all |
| Auth | JWT RS256 + short expiry | `JWTService` (15 min access / 7 day refresh) |
| Auth | Refresh token rotation | Token family pattern; reuse = family revocation |
| Auth | OAuth2 PKCE | S256 code challenge; state parameter |
| AuthZ | RBAC hierarchy | 6 roles; additive inheritance; `@require_permission` |
| AuthZ | ABAC deny-overrides | 9 built-in policies; deny wins over permit |
| Data | Field encryption at rest | AES-256-GCM; fresh DEK per record; envelope pattern |
| Data | Key rotation | `rotate_dek()` re-wraps DEK without re-reading plaintext |
| Data | PII pseudonymisation | SHA-256 IP hash in audit log |
| API | Security headers | HSTS, CSP, X-Frame-Options, COEP/COOP/CORP |
| API | Input validation | Path traversal, SQLi, XSS pattern matching |
| API | Body size limit | 50 MB Content-Length guard |
| Secrets | Runtime secret loading | AWS Secrets Manager via ESO + IRSA; 5-min cache |
| Audit | Tamper-evident log | SHA-256 chain hash per tenant; dual-sink (Redis + PG) |
| Infra | Non-root containers | UID=1001; `runAsNonRoot: true` |
| Infra | Read-only root FS | `readOnlyRootFilesystem: true` |
| Infra | Minimal capabilities | `capabilities.drop: [ALL]` |
| Infra | Seccomp | `seccompProfile: RuntimeDefault` (blocks ptrace) |
| Ops | Secret rotation | ESO refreshInterval 5m; `rotate_and_reload()` |
| Ops | Audit retention | 2-year retention; pg_cron monthly cleanup |
| ML | Model integrity | Hash verification at load; signed S3 artifacts |
| ML | Inference access | NetworkPolicy; require_permission(ML_RUN_INFERENCE) |

---

## 7. Residual Risks and Roadmap

| Risk | Priority | Roadmap Item |
|------|----------|-------------|
| mTLS between pods not enforced | Medium | Istio/Linkerd service mesh |
| PgBouncer not deployed (connection exhaustion) | Medium | Add PgBouncer sidecar in DB tier |
| Anthropic API receives derived cost descriptions | Medium | Prompt scrubber / data minimisation layer |
| SSRF via supplier domain resolution | Medium | DNS-based allow-list validator in RFQ agent |
| In-memory key extraction via proc | Medium | AWS KMS envelope encryption (keys never in memory) |
| Row-Level Security not yet enforced in PG | Low | PostgreSQL RLS per tenant_id |
| Qdrant lacks built-in RBAC | Low | Proxy with JWT validation in front of Qdrant |
| MLflow has no tenant isolation | Low | Scoped MLflow experiments per tenant |

---

## 8. Compliance Touchpoints

| Requirement | Coverage |
|-------------|---------|
| GDPR Art. 25 (Privacy by Design) | IP pseudonymisation; data minimisation in prompts; 2-year audit retention limit |
| GDPR Art. 32 (Technical measures) | AES-256-GCM at rest; TLS 1.3 in transit; access controls |
| ISO 27001 A.9 (Access control) | RBAC + ABAC; least-privilege service accounts; JTI revocation |
| ISO 27001 A.12.4 (Logging) | Tamper-evident audit log; structlog + PostgreSQL + Redis stream |
| SOC 2 Type II (Availability) | HPA + KEDA autoscaling; health probes; PDB; rate limiting |
| SOC 2 Type II (Confidentiality) | Field encryption; secrets manager; no secrets in logs |
| NIS2 (Critical infrastructure logging) | Chain-hash audit trail; incident detection; PagerDuty integration |
