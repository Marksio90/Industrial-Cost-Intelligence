# RFQ Agent — SQL Schema, API, Event System, Prompt Templates

## 11. SQL Schema (PostgreSQL 16)

### 11.1 Schemat i typy wyliczeniowe

```sql
CREATE SCHEMA IF NOT EXISTS rfqa;

CREATE TYPE rfqa.rfq_state AS ENUM (
    'DRAFT', 'SUPPLIER_DISCOVERY', 'AWAITING_HITL', 'RFQ_GENERATION',
    'EMAIL_DISPATCH', 'AWAITING_RESPONSES', 'SCRAPING_PORTALS',
    'RESPONSE_PARSING', 'OFFER_NORMALIZATION', 'OFFER_COMPARISON',
    'RECOMMENDATION', 'PRICE_DB_UPDATE', 'COMPLETED', 'CANCELLED', 'FAILED'
);

CREATE TYPE rfqa.decision_outcome AS ENUM (
    'AUTO_SELECT', 'RECOMMEND_HITL', 'REJECT_ALL', 'INSUFFICIENT'
);

CREATE TYPE rfqa.hitl_decision AS ENUM (
    'APPROVE', 'REJECT', 'MODIFY', 'DELEGATE', 'TIMEOUT'
);

CREATE TYPE rfqa.hitl_status AS ENUM (
    'PENDING', 'RESOLVED', 'TIMEOUT', 'CANCELLED'
);

CREATE TYPE rfqa.risk_level AS ENUM ('LOW', 'MEDIUM', 'HIGH', 'BLOCK');

CREATE TYPE rfqa.email_status AS ENUM (
    'QUEUED', 'SENT', 'DELIVERED', 'BOUNCED', 'FAILED', 'REPLIED'
);

CREATE TYPE rfqa.response_channel AS ENUM (
    'EMAIL', 'PORTAL', 'API_EDI', 'MANUAL'
);
```

### 11.2 Tabele główne

```sql
-- RFQ request master record
CREATE TABLE rfqa.rfq_cycles (
    rfq_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_name        VARCHAR(255) NOT NULL,
    material_code       VARCHAR(100) NOT NULL,
    material_description TEXT,
    quantity            NUMERIC(14,4) NOT NULL,
    unit                VARCHAR(50) NOT NULL DEFAULT 'pcs',
    required_delivery   DATE NOT NULL,
    target_price_eur    NUMERIC(14,4),
    budget_limit_eur    NUMERIC(16,4),
    preferred_location  CHAR(2),
    required_certs      TEXT[],
    quote_deadline      DATE NOT NULL,
    special_requirements TEXT,
    state               rfqa.rfq_state NOT NULL DEFAULT 'DRAFT',
    decision_outcome    rfqa.decision_outcome,
    winner_supplier_id  UUID,
    winner_price_eur    NUMERIC(14,4),
    savings_eur         NUMERIC(14,4),
    savings_pct         NUMERIC(8,4),
    auto_approved       BOOLEAN DEFAULT FALSE,
    n_suppliers_contacted INTEGER DEFAULT 0,
    n_offers_received   INTEGER DEFAULT 0,
    requestor_id        UUID,
    agent_model         VARCHAR(100),
    total_tokens_used   INTEGER DEFAULT 0,
    total_iterations    INTEGER DEFAULT 0,
    calculation_ms      INTEGER,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);

-- Supplier profiles (internal DB enriched from SIE)
CREATE TABLE rfqa.supplier_profiles (
    supplier_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id         VARCHAR(200),              -- SIE supplier_id
    name                VARCHAR(255) NOT NULL,
    website             VARCHAR(500),
    primary_email       VARCHAR(255),
    contact_email       VARCHAR(255),
    contact_name        VARCHAR(255),
    country             CHAR(2),
    region              VARCHAR(100),
    capabilities        TEXT[],
    certifications      TEXT[],
    overall_score       NUMERIC(5,4) DEFAULT 0.70,
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    spam_cooldown_until TIMESTAMPTZ,
    blacklisted         BOOLEAN DEFAULT FALSE,
    blacklist_reason    TEXT,
    preferred           BOOLEAN DEFAULT FALSE,
    last_contact_at     TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Capabilities lookup
CREATE TABLE rfqa.supplier_capabilities (
    capability_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id    UUID NOT NULL REFERENCES rfqa.supplier_profiles(supplier_id) ON DELETE CASCADE,
    material_code  VARCHAR(100) NOT NULL,
    process_types  TEXT[],
    min_qty        NUMERIC(12,4),
    max_qty        NUMERIC(12,4),
    locations      TEXT[],
    verified_at    TIMESTAMPTZ,
    UNIQUE (supplier_id, material_code)
);

-- Email log
CREATE TABLE rfqa.email_log (
    email_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id              UUID NOT NULL REFERENCES rfqa.rfq_cycles(rfq_id),
    supplier_id         UUID REFERENCES rfqa.supplier_profiles(supplier_id),
    to_address          VARCHAR(255) NOT NULL,
    subject             VARCHAR(500),
    material_code       VARCHAR(100),
    language            CHAR(5) DEFAULT 'en',
    status              rfqa.email_status NOT NULL DEFAULT 'QUEUED',
    external_message_id VARCHAR(500),
    error_message       TEXT,
    open_count          INTEGER DEFAULT 0,
    reply_received      BOOLEAN DEFAULT FALSE,
    replied_at          TIMESTAMPTZ,
    sent_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Raw supplier responses
CREATE TABLE rfqa.supplier_responses (
    response_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id              UUID NOT NULL REFERENCES rfqa.rfq_cycles(rfq_id),
    supplier_id         UUID REFERENCES rfqa.supplier_profiles(supplier_id),
    channel             rfqa.response_channel NOT NULL,
    raw_content         TEXT NOT NULL,
    content_hash        CHAR(64),
    parsed_at           TIMESTAMPTZ,
    parse_confidence    NUMERIC(5,4),
    parse_errors        TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Parsed + normalized offers
CREATE TABLE rfqa.normalized_offers (
    offer_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id              UUID NOT NULL REFERENCES rfqa.rfq_cycles(rfq_id),
    response_id         UUID REFERENCES rfqa.supplier_responses(response_id),
    supplier_id         UUID REFERENCES rfqa.supplier_profiles(supplier_id),
    supplier_name       VARCHAR(255),
    -- Normalized financials
    unit_price_eur      NUMERIC(14,4) NOT NULL,
    total_price_eur     NUMERIC(16,4),
    currency_original   CHAR(3),
    fx_rate_used        NUMERIC(12,6),
    incoterms_original  VARCHAR(10),
    incoterms_adj_eur   NUMERIC(12,4),
    payment_terms_std   NUMERIC(8,4),
    -- Delivery
    delivery_days       INTEGER,
    delivery_date       DATE,
    -- Scoring
    composite_score     NUMERIC(6,4),
    price_score         NUMERIC(6,4),
    delivery_score      NUMERIC(6,4),
    quality_score       NUMERIC(6,4),
    risk_score          NUMERIC(6,4),
    rank                INTEGER,
    -- Compliance
    certifications      TEXT[],
    missing_certs       TEXT[],
    risk_flags          TEXT[],
    -- Metadata
    validity_until      DATE,
    parse_confidence    NUMERIC(5,4),
    normalization_notes TEXT[],
    is_winner           BOOLEAN DEFAULT FALSE,
    auto_eligible       BOOLEAN DEFAULT FALSE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Agent execution traces
CREATE TABLE rfqa.agent_traces (
    trace_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id          UUID REFERENCES rfqa.rfq_cycles(rfq_id),
    step            INTEGER NOT NULL,
    thought         TEXT,
    action          TEXT,
    tool_name       VARCHAR(100),
    tool_input      JSONB,
    observation     TEXT,
    tokens_used     INTEGER DEFAULT 0,
    latency_ms      INTEGER DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- HITL requests and responses
CREATE TABLE rfqa.hitl_requests (
    request_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id          UUID NOT NULL REFERENCES rfqa.rfq_cycles(rfq_id),
    request_type    VARCHAR(100) NOT NULL,
    title           VARCHAR(500),
    summary         TEXT,
    payload         JSONB NOT NULL DEFAULT '{}',
    assigned_to     VARCHAR(255),
    deadline        TIMESTAMPTZ NOT NULL,
    priority        INTEGER DEFAULT 2,
    status          rfqa.hitl_status NOT NULL DEFAULT 'PENDING',
    decision        rfqa.hitl_decision,
    reviewer_id     VARCHAR(255),
    reviewer_notes  TEXT,
    modified_data   JSONB,
    responded_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Audit log (append-only, immutable)
CREATE TABLE rfqa.audit_log (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rfq_id          UUID,
    event_type      VARCHAR(100) NOT NULL,
    actor           VARCHAR(255) NOT NULL,
    payload         JSONB NOT NULL DEFAULT '{}',
    risk_level      rfqa.risk_level DEFAULT 'LOW',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Market price index (updated after each RFQ)
CREATE TABLE rfqa.market_price_index (
    index_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    material_code   VARCHAR(100) NOT NULL,
    supplier_id     UUID,
    unit_price_eur  NUMERIC(14,4) NOT NULL,
    quantity        NUMERIC(14,4),
    currency_original CHAR(3),
    incoterms       VARCHAR(10),
    location        CHAR(2),
    is_winner       BOOLEAN DEFAULT FALSE,
    rfq_id          UUID,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (material_code, supplier_id)
);

-- Past transactions (for relationship scoring)
CREATE TABLE rfqa.past_transactions (
    transaction_id  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     UUID NOT NULL REFERENCES rfqa.supplier_profiles(supplier_id),
    rfq_id          UUID REFERENCES rfqa.rfq_cycles(rfq_id),
    material_code   VARCHAR(100),
    quantity        NUMERIC(14,4),
    unit_price_eur  NUMERIC(14,4),
    total_eur       NUMERIC(16,4),
    quality_rating  NUMERIC(4,3),          -- 0–1
    delivery_on_time BOOLEAN,
    transaction_date DATE NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supplier blacklist
CREATE TABLE rfqa.supplier_blacklist (
    blacklist_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id     UUID REFERENCES rfqa.supplier_profiles(supplier_id),
    name_lower      VARCHAR(255),              -- for name-based matching of unknown suppliers
    domain          VARCHAR(255),
    reason          TEXT NOT NULL,
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    added_by        VARCHAR(255),
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- FX rate cache
CREATE TABLE rfqa.fx_rates (
    currency    CHAR(3) PRIMARY KEY,
    rate        NUMERIC(14,8) NOT NULL,        -- units per 1 EUR
    fetched_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Portal credentials (encrypted)
CREATE TABLE rfqa.portal_credentials (
    portal_name  VARCHAR(100) PRIMARY KEY,
    credentials  JSONB NOT NULL,               -- stored encrypted via pgcrypto
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Transactional outbox
CREATE TABLE rfqa.outbox (
    outbox_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    aggregate_type  VARCHAR(100) NOT NULL,
    aggregate_id    UUID NOT NULL,
    event_type      VARCHAR(100) NOT NULL,
    topic           VARCHAR(200) NOT NULL,
    payload         JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at    TIMESTAMPTZ,
    retry_count     INTEGER DEFAULT 0
);
```

### 11.3 Indeksy

```sql
-- rfq_cycles
CREATE INDEX idx_rc_state         ON rfqa.rfq_cycles (state);
CREATE INDEX idx_rc_material      ON rfqa.rfq_cycles (material_code);
CREATE INDEX idx_rc_created_at    ON rfqa.rfq_cycles (created_at DESC);
CREATE INDEX idx_rc_requestor     ON rfqa.rfq_cycles (requestor_id);

-- supplier_profiles
CREATE INDEX idx_sp_country       ON rfqa.supplier_profiles (country);
CREATE INDEX idx_sp_active        ON rfqa.supplier_profiles (active, spam_cooldown_until);
CREATE UNIQUE INDEX idx_sp_ext_id ON rfqa.supplier_profiles (external_id) WHERE external_id IS NOT NULL;

-- email_log
CREATE INDEX idx_el_rfq_id        ON rfqa.email_log (rfq_id);
CREATE INDEX idx_el_supplier_id   ON rfqa.email_log (supplier_id, sent_at DESC);
CREATE INDEX idx_el_material_sent ON rfqa.email_log (material_code, sent_at DESC);

-- normalized_offers
CREATE INDEX idx_no_rfq_id        ON rfqa.normalized_offers (rfq_id);
CREATE INDEX idx_no_supplier_id   ON rfqa.normalized_offers (supplier_id);
CREATE INDEX idx_no_composite     ON rfqa.normalized_offers (rfq_id, composite_score DESC);

-- agent_traces
CREATE INDEX idx_at_rfq_step      ON rfqa.agent_traces (rfq_id, step);

-- hitl_requests
CREATE INDEX idx_hr_rfq_id        ON rfqa.hitl_requests (rfq_id);
CREATE INDEX idx_hr_pending       ON rfqa.hitl_requests (status, deadline) WHERE status = 'PENDING';
CREATE INDEX idx_hr_assigned      ON rfqa.hitl_requests (assigned_to, status);

-- audit_log
CREATE INDEX idx_al_rfq_id        ON rfqa.audit_log (rfq_id);
CREATE INDEX idx_al_event_type    ON rfqa.audit_log (event_type, created_at DESC);

-- market_price_index
CREATE INDEX idx_mpi_material     ON rfqa.market_price_index (material_code, recorded_at DESC);

-- outbox
CREATE INDEX idx_ob_unpublished   ON rfqa.outbox (created_at) WHERE published_at IS NULL;
```

### 11.4 Funkcje składowane

```sql
-- Auto-update updated_at
CREATE OR REPLACE FUNCTION rfqa.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_rc_updated_at BEFORE UPDATE ON rfqa.rfq_cycles
    FOR EACH ROW EXECUTE FUNCTION rfqa.set_updated_at();
CREATE TRIGGER trg_sp_updated_at BEFORE UPDATE ON rfqa.supplier_profiles
    FOR EACH ROW EXECUTE FUNCTION rfqa.set_updated_at();

-- Claim HITL request for review
CREATE OR REPLACE FUNCTION rfqa.claim_hitl_request(
    p_reviewer_id VARCHAR, p_roles TEXT[]
)
RETURNS rfqa.hitl_requests LANGUAGE plpgsql AS $$
DECLARE v_req rfqa.hitl_requests;
BEGIN
    SELECT * INTO v_req FROM rfqa.hitl_requests
    WHERE status = 'PENDING'
      AND deadline > now()
      AND (assigned_to = p_reviewer_id OR assigned_to = ANY(p_roles))
    ORDER BY priority, deadline
    LIMIT 1 FOR UPDATE SKIP LOCKED;
    RETURN v_req;
END;
$$;

-- Market price stats for material
CREATE OR REPLACE FUNCTION rfqa.get_market_stats(
    p_material_code VARCHAR,
    p_location CHAR(2) DEFAULT NULL,
    p_days INTEGER DEFAULT 90
)
RETURNS TABLE (
    p10 NUMERIC, p25 NUMERIC, p50 NUMERIC,
    p75 NUMERIC, p90 NUMERIC, n INTEGER
) LANGUAGE sql AS $$
    SELECT
        PERCENTILE_CONT(0.10) WITHIN GROUP (ORDER BY unit_price_eur) AS p10,
        PERCENTILE_CONT(0.25) WITHIN GROUP (ORDER BY unit_price_eur) AS p25,
        PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY unit_price_eur) AS p50,
        PERCENTILE_CONT(0.75) WITHIN GROUP (ORDER BY unit_price_eur) AS p75,
        PERCENTILE_CONT(0.90) WITHIN GROUP (ORDER BY unit_price_eur) AS p90,
        COUNT(*)::INTEGER AS n
    FROM rfqa.market_price_index
    WHERE material_code = p_material_code
      AND (p_location IS NULL OR location = p_location)
      AND recorded_at > now() - (p_days || ' days')::INTERVAL;
$$;

-- Supplier spam status
CREATE OR REPLACE FUNCTION rfqa.can_contact_supplier(
    p_supplier_id UUID, p_material_code VARCHAR
) RETURNS BOOLEAN LANGUAGE sql AS $$
    SELECT NOT EXISTS (
        SELECT 1 FROM rfqa.email_log
        WHERE supplier_id = p_supplier_id
          AND material_code = p_material_code
          AND sent_at > now() - INTERVAL '30 days'
    ) AND NOT EXISTS (
        SELECT 1 FROM rfqa.supplier_profiles
        WHERE supplier_id = p_supplier_id
          AND (blacklisted = TRUE
               OR (spam_cooldown_until IS NOT NULL AND spam_cooldown_until > now()))
    );
$$;
```

### 11.5 Widoki

```sql
-- RFQ summary dashboard
CREATE VIEW rfqa.v_rfq_summary AS
SELECT
    rc.rfq_id, rc.material_code, rc.product_name, rc.state,
    rc.target_price_eur, rc.winner_price_eur, rc.savings_eur, rc.savings_pct,
    rc.n_suppliers_contacted, rc.n_offers_received,
    rc.auto_approved, rc.decision_outcome,
    rc.total_tokens_used, rc.total_iterations,
    COUNT(hr.request_id) FILTER (WHERE hr.status = 'PENDING') AS open_hitl_requests,
    rc.created_at, rc.completed_at,
    EXTRACT(EPOCH FROM (rc.completed_at - rc.created_at))/3600 AS duration_hours
FROM rfqa.rfq_cycles rc
LEFT JOIN rfqa.hitl_requests hr ON rc.rfq_id = hr.rfq_id
GROUP BY rc.rfq_id;

-- Supplier performance
CREATE VIEW rfqa.v_supplier_performance AS
SELECT
    sp.supplier_id, sp.name, sp.country, sp.overall_score,
    COUNT(DISTINCT no.rfq_id) AS rfq_count,
    COUNT(no.offer_id) FILTER (WHERE no.is_winner) AS wins,
    AVG(no.unit_price_eur) AS avg_price_eur,
    AVG(no.composite_score) AS avg_composite_score,
    AVG(pt.quality_rating) AS avg_quality_rating,
    AVG(CASE WHEN pt.delivery_on_time THEN 1.0 ELSE 0.0 END) AS on_time_rate
FROM rfqa.supplier_profiles sp
LEFT JOIN rfqa.normalized_offers no ON sp.supplier_id = no.supplier_id
LEFT JOIN rfqa.past_transactions pt ON sp.supplier_id = pt.supplier_id
GROUP BY sp.supplier_id, sp.name, sp.country, sp.overall_score;

-- Pending HITL queue
CREATE VIEW rfqa.v_hitl_queue AS
SELECT
    hr.request_id, hr.rfq_id, hr.request_type, hr.title,
    hr.priority, hr.deadline, hr.assigned_to, hr.status,
    rc.material_code, rc.product_name,
    EXTRACT(EPOCH FROM (hr.deadline - now()))/3600 AS hours_until_deadline
FROM rfqa.hitl_requests hr
JOIN rfqa.rfq_cycles rc ON hr.rfq_id = rc.rfq_id
WHERE hr.status = 'PENDING'
ORDER BY hr.priority, hr.deadline;
```

---

## 12. API (OpenAPI 3.1)

```yaml
openapi: "3.1.0"
info:
  title: RFQ Agent API
  version: "1.0.0"
  description: >
    API dla autonomicznego agenta RFQ platformy Industrial Cost Intelligence.
    Zarządza cyklem zapytań ofertowych, dostawcami, ofertami i decyzjami HITL.

servers:
  - url: https://api.industrial-cost.internal/v1/rfqa

security:
  - BearerAuth: []

tags:
  - name: RFQ           # Zarządzanie cyklami RFQ
  - name: Suppliers     # Profile dostawców
  - name: Offers        # Oferty i normalizacja
  - name: HITL          # Human-in-the-loop
  - name: Analytics     # Raporty i benchmarki
  - name: Admin         # Konfiguracja, blacklist

paths:
  /rfq:
    post:
      tags: [RFQ]
      summary: Uruchom nowy cykl RFQ
      operationId: createRFQ
      x-required-role: RFQA_USER
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/RFQRequest'
      responses:
        "202":
          description: RFQ przyjęte, agent uruchomiony asynchronicznie
          content:
            application/json:
              schema:
                type: object
                properties:
                  rfq_id: {type: string, format: uuid}
                  state:  {type: string}
                  message: {type: string}

    get:
      tags: [RFQ]
      summary: Lista cykli RFQ (z filtrowaniem i paginacją)
      operationId: listRFQs
      x-required-role: RFQA_VIEWER
      parameters:
        - name: state
          in: query
          schema:
            type: string
        - name: material_code
          in: query
          schema:
            type: string
        - name: from_date
          in: query
          schema:
            type: string
            format: date
        - name: page
          in: query
          schema:
            type: integer
            default: 1
        - name: page_size
          in: query
          schema:
            type: integer
            default: 20
            maximum: 100
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RFQListResponse'

  /rfq/{rfqId}:
    get:
      tags: [RFQ]
      summary: Pobierz status i wyniki cyklu RFQ
      operationId: getRFQ
      x-required-role: RFQA_VIEWER
      parameters:
        - name: rfqId
          in: path
          required: true
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/RFQDetailResponse'

  /rfq/{rfqId}/cancel:
    post:
      tags: [RFQ]
      summary: Anuluj aktywny cykl RFQ
      operationId: cancelRFQ
      x-required-role: RFQA_USER
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  rfq_id: {type: string}
                  state:  {type: string, enum: [CANCELLED]}

  /rfq/{rfqId}/offers:
    get:
      tags: [Offers]
      summary: Pobierz wszystkie oferty dla cyklu RFQ (z rankingiem)
      operationId: getRFQOffers
      x-required-role: RFQA_VIEWER
      parameters:
        - name: rfqId
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: include_rejected
          in: query
          schema: {type: boolean, default: false}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  rfq_id:
                    type: string
                  offers:
                    type: array
                    items:
                      $ref: '#/components/schemas/NormalizedOfferResponse'
                  comparison_report:
                    $ref: '#/components/schemas/ComparisonReport'

  /rfq/{rfqId}/traces:
    get:
      tags: [RFQ]
      summary: Agent execution traces (ReAct steps)
      operationId: getRFQTraces
      x-required-role: RFQA_ANALYST
      parameters:
        - name: rfqId
          in: path
          required: true
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  traces:
                    type: array
                    items:
                      $ref: '#/components/schemas/AgentTrace'

  /suppliers:
    get:
      tags: [Suppliers]
      summary: Lista dostawców (z filtrowaniem po materiale, lokalizacji, certach)
      operationId: listSuppliers
      x-required-role: RFQA_VIEWER
      parameters:
        - name: material_code
          in: query
          schema: {type: string}
        - name: country
          in: query
          schema: {type: string}
        - name: certification
          in: query
          schema: {type: string}
        - name: active_only
          in: query
          schema: {type: boolean, default: true}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SupplierListResponse'

    post:
      tags: [Suppliers]
      summary: Dodaj nowego dostawcę ręcznie
      operationId: createSupplier
      x-required-role: RFQA_PROCUREMENT
      requestBody:
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/SupplierCreateRequest'
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/SupplierProfile'

  /suppliers/{supplierId}/blacklist:
    post:
      tags: [Suppliers]
      summary: Dodaj dostawcę do blacklisty
      operationId: blacklistSupplier
      x-required-role: RFQA_PROCUREMENT
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [reason]
              properties:
                reason:  {type: string}
                expires_at: {type: string, format: date-time}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  supplier_id: {type: string}
                  blacklisted: {type: boolean}

  /hitl/queue:
    get:
      tags: [HITL]
      summary: Pobierz kolejkę oczekujących decyzji HITL (dla zalogowanego użytkownika)
      operationId: getHITLQueue
      x-required-role: RFQA_REVIEWER
      parameters:
        - name: priority
          in: query
          schema:
            type: integer
        - name: request_type
          in: query
          schema:
            type: string
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  queue:
                    type: array
                    items:
                      $ref: '#/components/schemas/HITLQueueItem'
                  total_pending: {type: integer}

  /hitl/{requestId}/decide:
    post:
      tags: [HITL]
      summary: Wyślij decyzję HITL (approve / reject / modify)
      operationId: submitHITLDecision
      x-required-role: RFQA_REVIEWER
      parameters:
        - name: requestId
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [decision]
              properties:
                decision:
                  type: string
                  enum: [APPROVE, REJECT, MODIFY, DELEGATE]
                notes:
                  type: string
                  maxLength: 2000
                modified_data:
                  type: object
                  description: Required when decision=MODIFY
                delegate_to:
                  type: string
                  description: Required when decision=DELEGATE
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  request_id: {type: string}
                  decision:   {type: string}
                  agent_resumed: {type: boolean}

  /analytics/market-prices/{materialCode}:
    get:
      tags: [Analytics]
      summary: Statystyki cen rynkowych (P10–P90) z historii RFQ
      operationId: getMarketPrices
      x-required-role: RFQA_ANALYST
      parameters:
        - name: materialCode
          in: path
          required: true
          schema: {type: string}
        - name: location
          in: query
          schema: {type: string}
        - name: days
          in: query
          schema: {type: integer, default: 90}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  material_code: {type: string}
                  p10: {type: number}
                  p25: {type: number}
                  p50: {type: number}
                  p75: {type: number}
                  p90: {type: number}
                  n:   {type: integer}

  /analytics/supplier-performance:
    get:
      tags: [Analytics]
      summary: Ranking dostawców z metrykami wydajności
      operationId: getSupplierPerformance
      x-required-role: RFQA_ANALYST
      parameters:
        - name: material_code
          in: query
          schema: {type: string}
        - name: location
          in: query
          schema: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  suppliers:
                    type: array
                    items:
                      $ref: '#/components/schemas/SupplierPerformance'

  /analytics/agent-stats:
    get:
      tags: [Analytics]
      summary: Statystyki agenta (tokeny, iteracje, czas, oszczędności)
      operationId: getAgentStats
      x-required-role: RFQA_ANALYST
      parameters:
        - name: days
          in: query
          schema: {type: integer, default: 30}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/AgentStatsResponse'

  /admin/blacklist:
    get:
      tags: [Admin]
      summary: Lista zablokowanych dostawców
      operationId: getBlacklist
      x-required-role: RFQA_ADMIN
      responses:
        "200":
          content:
            application/json:
              schema:
                type: object
                properties:
                  entries:
                    type: array
                    items:
                      $ref: '#/components/schemas/BlacklistEntry'

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    RFQRequest:
      type: object
      required: [product_name, material_code, quantity, unit, required_delivery, quote_deadline]
      properties:
        product_name:
          type: string
          maxLength: 255
        material_code:
          type: string
        material_description:
          type: string
        quantity:
          type: number
          minimum: 0.001
        unit:
          type: string
          default: pcs
        required_delivery:
          type: string
          format: date
        target_price_eur:
          type: number
        budget_limit_eur:
          type: number
        preferred_location:
          type: string
          pattern: '^[A-Z]{2}$'
        required_certifications:
          type: array
          items: {type: string}
        quote_deadline:
          type: string
          format: date
        special_requirements:
          type: string
        options:
          type: object
          properties:
            max_suppliers:
              type: integer
              default: 10
            include_web_scraping:
              type: boolean
              default: true
            language:
              type: string
              default: en
```

### 12.1 Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| RFQA_VIEWER | GET /rfq, GET /rfq/{id}, GET /suppliers, GET /analytics |
| RFQA_USER | RFQA_VIEWER + POST /rfq, POST /rfq/{id}/cancel |
| RFQA_ANALYST | RFQA_USER + traces, offers, market-prices, agent-stats |
| RFQA_REVIEWER | RFQA_ANALYST + GET /hitl/queue, POST /hitl/{id}/decide |
| RFQA_PROCUREMENT | RFQA_REVIEWER + POST /suppliers, blacklist management |
| RFQA_OPS | Wszystko + portal credentials, agent config |
| RFQA_ADMIN | Pełny dostęp + DELETE + blacklist admin |

---

## 13. Event System

### 13.1 Topiki Kafka

| Temat | Partycje | RF | Retencja | Opis |
|-------|----------|----|----------|------|
| `rfqa.rfq.created` | 6 | 3 | 30d | Nowy cykl RFQ uruchomiony |
| `rfqa.rfq.state.changed` | 6 | 3 | 30d | Zmiana stanu cyklu |
| `rfqa.rfq.completed` | 6 | 3 | 365d | Zakończony cykl z wynikami |
| `rfqa.email.sent` | 12 | 3 | 7d | Email wysłany do dostawcy |
| `rfqa.email.replied` | 12 | 3 | 30d | Odpowiedź otrzymana od dostawcy |
| `rfqa.offer.normalized` | 6 | 3 | 30d | Oferta znormalizowana |
| `rfqa.hitl.requested` | 4 | 3 | 30d | Żądanie decyzji HITL |
| `rfqa.hitl.decided` | 4 | 3 | 90d | Decyzja HITL podjęta |
| `rfqa.price.updated` | 6 | 3 | 365d | Aktualizacja indeksu cen rynkowych |
| `rfqa.supplier.blacklisted` | 2 | 3 | 365d | Dostawca zablokowany |

### 13.2 Schematy Avro

```json
// rfqa.rfq.completed
{
  "namespace": "com.ici.rfqa",
  "type": "record",
  "name": "RFQCompleted",
  "fields": [
    {"name": "event_id",         "type": "string"},
    {"name": "occurred_at",      "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "schema_version",   "type": "int", "default": 1},
    {"name": "rfq_id",           "type": "string"},
    {"name": "material_code",    "type": "string"},
    {"name": "product_name",     "type": "string"},
    {"name": "target_price_eur", "type": "double"},
    {"name": "winner_price_eur", "type": ["null", "double"], "default": null},
    {"name": "savings_eur",      "type": ["null", "double"], "default": null},
    {"name": "savings_pct",      "type": ["null", "double"], "default": null},
    {"name": "n_suppliers",      "type": "int"},
    {"name": "n_offers",         "type": "int"},
    {"name": "auto_approved",    "type": "boolean"},
    {"name": "decision_outcome", "type": "string"},
    {"name": "total_tokens",     "type": "int"},
    {"name": "duration_minutes", "type": "double"}
  ]
}

// rfqa.price.updated
{
  "namespace": "com.ici.rfqa",
  "type": "record",
  "name": "MarketPriceUpdated",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "occurred_at",     "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "material_code",   "type": "string"},
    {"name": "supplier_id",     "type": ["null", "string"], "default": null},
    {"name": "unit_price_eur",  "type": "double"},
    {"name": "quantity",        "type": "double"},
    {"name": "location",        "type": ["null", "string"], "default": null},
    {"name": "rfq_id",          "type": "string"},
    {"name": "is_winner",       "type": "boolean"}
  ]
}

// rfqa.hitl.requested
{
  "namespace": "com.ici.rfqa",
  "type": "record",
  "name": "HITLRequested",
  "fields": [
    {"name": "event_id",      "type": "string"},
    {"name": "occurred_at",   "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "request_id",    "type": "string"},
    {"name": "rfq_id",        "type": "string"},
    {"name": "request_type",  "type": "string"},
    {"name": "title",         "type": "string"},
    {"name": "assigned_to",   "type": "string"},
    {"name": "deadline",      "type": {"type": "long", "logicalType": "timestamp-millis"}},
    {"name": "priority",      "type": "int"}
  ]
}
```

### 13.3 Konsumenci zewnętrzni

| Temat wejściowy | Producent | Akcja w RFQA |
|----------------|-----------|--------------|
| `che.quote.recorded` | CHE | Aktualizacja market_price_index z potwierdzonej transakcji |
| `sie.supplier.updated` | SIE | Aktualizacja supplier_profiles.overall_score |
| `sie.supplier.certified` | SIE | Aktualizacja supplier_profiles.certifications |
| `mie.material.updated` | MIE | Aktualizacja cen docelowych w cyklach DRAFT |
| `email.webhook.replied` | Email Gateway | Trigger response_parsing dla nowych emaili |

---

## 14. Prompt Templates

### 14.1 Szablon emaila RFQ — angielski

```python
RFQ_EMAIL_PROMPT_TEMPLATE = """Generate a professional Request for Quotation (RFQ) email in {language}.

Supplier: {supplier_name}
Product: {product_name}
Material: {material_code} — {material_description}
Quantity: {quantity} {unit}
Required delivery: {required_delivery}
Required certifications: {certifications}
Special requirements: {special_requirements}
Quote deadline: {quote_deadline}

The email should:
1. Be professional and concise (under 300 words)
2. Clearly state all requirements
3. Request: unit price, total price, delivery time, payment terms, incoterms, certifications
4. Ask for quote in EUR (or state the currency they will quote in)
5. Include a quote reference number: RFQ-{product_name[:8].upper().replace(' ', '-')}-{quote_deadline}
6. Mention the deadline clearly
7. NOT mention our target/budget price
8. Include a professional closing with contact info placeholder

Output format EXACTLY as:
SUBJECT: <email subject line>
HTML_BODY:
<complete HTML email body>
TEXT_BODY:
<plain text version>"""
```

### 14.2 Szablon emaila follow-up

```python
FOLLOW_UP_EMAIL_PROMPT_TEMPLATE = """Generate a polite follow-up email in {language}.

Context:
- We sent an RFQ to {supplier_name} on {original_sent_date}
- For: {product_name} ({material_code}), qty {quantity} {unit}
- Original quote deadline: {quote_deadline} (now passed/approaching)
- We have NOT received a response yet

Generate a brief, professional follow-up email that:
1. Reminds them of our original request
2. Asks if they are able to quote
3. Extends the deadline to {new_deadline} if relevant
4. Is friendly and not pushy
5. Is under 150 words

Output format EXACTLY as:
SUBJECT: <subject line>
HTML_BODY:
<HTML body>
TEXT_BODY:
<plain text>"""
```

### 14.3 Szablon emaila negocjacyjny

```python
NEGOTIATION_EMAIL_PROMPT_TEMPLATE = """Generate a price negotiation email in {language}.

Context:
- Supplier: {supplier_name}
- Their quoted price: {quoted_price_eur} EUR/{unit}
- Our target price: {target_price_eur} EUR/{unit} (do NOT reveal unless instructed)
- Market median price: {market_median_eur} EUR/{unit}
- Competitor offer: {competitor_price_eur} EUR/{unit} (from {n_competitors} competing quotes)
- Quantity: {quantity} {unit}
- Leverage points: {leverage_points}

Generate a professional negotiation email that:
1. Thanks them for the quote
2. Acknowledges quality aspects positively
3. Indicates we have competing offers without being specific
4. Requests a revised price or best final offer
5. Sets a clear response deadline: {negotiation_deadline}
6. Maintains a collaborative, long-term partnership tone
7. Is under 200 words

IMPORTANT: Do NOT reveal our exact target price or competitor details.

Output format EXACTLY as:
SUBJECT: <subject>
HTML_BODY:
<HTML>
TEXT_BODY:
<text>"""
```

### 14.4 Szablon parsowania odpowiedzi

```python
PARSE_RESPONSE_SYSTEM_PROMPT = """You are an expert procurement data extraction specialist.
Your task: extract structured data from supplier emails or portal responses.

Rules:
- Extract ONLY data explicitly stated in the text
- Use null for any field not found — never fabricate or infer missing values
- Convert all amounts to the stated currency (do NOT convert currencies)
- If multiple prices are mentioned, extract the unit price for the stated quantity
- Flag anomalies in risk_flags: unusually low price (<30% of typical), vague delivery,
  missing certifications, prepayment requirements, very short validity
- parse_confidence: 0.9+ if all key fields found clearly, 0.5-0.8 if some ambiguity,
  <0.5 if major fields missing or contradictory"""
```

### 14.5 Szablon analizy dostawców (discovery)

```python
SUPPLIER_ANALYSIS_PROMPT = """Analyze the following supplier information and provide a qualification assessment.

Supplier data:
Name: {name}
Website: {website}
Claimed capabilities: {capabilities}
Claimed certifications: {certifications}
Country: {country}
Source: {source}

Context:
Required material: {material_code}
Required certifications: {required_certs}
Required location: {required_location}

Provide your assessment as JSON:
{{
  "qualification_score": float 0-1,
  "recommended": boolean,
  "capability_match": float 0-1,
  "location_match": boolean,
  "cert_match": float 0-1 (fraction of required certs claimed),
  "red_flags": [list of concerns],
  "green_flags": [list of positives],
  "recommendation": "string — one sentence summary"
}}

Be conservative. If website/contact info seems suspicious, flag it.
Return ONLY the JSON object."""
```

### 14.6 Szablon rekomendacji końcowej

```python
FINAL_RECOMMENDATION_PROMPT = """You are a senior procurement analyst.
Prepare a final recommendation memo for the following RFQ:

RFQ: {rfq_id}
Product: {product_name} | Material: {material_code}
Quantity: {quantity} {unit} | Target: {target_price_eur} EUR/unit

Offers received ({n_offers} total):
{offers_table}

Market context:
- Market median: {market_median_eur} EUR/unit
- Market P10: {market_p10_eur} EUR/unit
- Market P90: {market_p90_eur} EUR/unit

Write a concise recommendation (under 400 words) covering:
1. RECOMMENDATION: winner + rationale (price, quality, delivery, risk)
2. SAVINGS: vs target and vs market median
3. RISK FACTORS: any concerns about the recommended supplier
4. ALTERNATIVES: runner-up if primary falls through
5. NEXT STEPS: what procurement should do to close this

Format as a professional memo with clear sections."""
```

### 14.7 Szablony wielojęzyczne

| Język | Kod | Użycie |
|-------|-----|--------|
| English | `en` | Default — international suppliers |
| German | `de` | DACH region (DE, AT, CH) |
| Polish | `pl` | Polish suppliers (PL) |
| Czech | `cs` | Czech/Slovak suppliers |
| Chinese (Simplified) | `zh` | Chinese suppliers (CN) |
| Spanish | `es` | MX, ES suppliers |
| Turkish | `tr` | TR suppliers |
| Romanian | `ro` | RO suppliers |

Language is auto-detected from `supplier.country` mapping in `LANGUAGE_BY_COUNTRY`:

```python
LANGUAGE_BY_COUNTRY: dict[str, str] = {
    "DE": "de", "AT": "de", "CH": "de",
    "PL": "pl",
    "CZ": "cs", "SK": "cs",
    "CN": "zh", "TW": "zh",
    "MX": "es", "ES": "es",
    "TR": "tr",
    "RO": "ro",
}
DEFAULT_LANGUAGE = "en"
```
