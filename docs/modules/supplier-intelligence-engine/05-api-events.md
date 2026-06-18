# Supplier Intelligence Engine — API & Events

## 14. REST API — OpenAPI 3.1

```yaml
openapi: "3.1.0"
info:
  title: Supplier Intelligence Engine API
  version: "1.0.0"
  description: |
    Central supplier assessment and intelligence API for the
    Industrial Cost Intelligence platform.

servers:
  - url: https://api.ici.internal/sie/v1
    description: Internal production

security:
  - BearerAuth: []

tags:
  - name: Suppliers
  - name: Scorecards
  - name: Quality
  - name: Delivery
  - name: Pricing
  - name: Risk
  - name: Financial
  - name: Search
  - name: AI

paths:
  # ──────────────────────────────────────────
  # SUPPLIERS
  # ──────────────────────────────────────────
  /suppliers:
    get:
      tags: [Suppliers]
      summary: List suppliers
      parameters:
        - name: status
          in: query
          schema: {type: string, enum: [PENDING,UNDER_REVIEW,APPROVED,CONDITIONAL,SUSPENDED,DEACTIVATED]}
        - name: tier
          in: query
          schema: {type: string, enum: [TIER1,TIER2,TIER3,SPOT]}
        - name: country
          in: query
          schema: {type: string}
        - name: rating_class
          in: query
          schema: {type: string, enum: [A,B,C,D,E,F]}
        - name: category_id
          in: query
          schema: {type: string, format: uuid}
        - name: q
          in: query
          description: Full-text search query
          schema: {type: string}
        - name: page
          in: query
          schema: {type: integer, default: 1}
        - name: page_size
          in: query
          schema: {type: integer, default: 20, maximum: 100}
      responses:
        "200":
          description: Supplier list
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierListResponse"

    post:
      tags: [Suppliers]
      summary: Register new supplier
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SupplierCreateRequest"
      responses:
        "201":
          description: Supplier registered
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierResponse"

  /suppliers/{supplier_id}:
    get:
      tags: [Suppliers]
      summary: Get supplier details
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: include
          in: query
          description: Comma-separated list of relations to embed
          schema:
            type: array
            items:
              type: string
              enum: [scorecard,certifications,capabilities,contacts,risk,financial]
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierDetailResponse"

    patch:
      tags: [Suppliers]
      summary: Update supplier
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SupplierUpdateRequest"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierResponse"

  /suppliers/{supplier_id}/status:
    patch:
      tags: [Suppliers]
      summary: Change supplier status (approve / suspend / deactivate)
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [status, reason]
              properties:
                status:
                  type: string
                  enum: [APPROVED,CONDITIONAL,SUSPENDED,DEACTIVATED,BLACKLISTED]
                reason:
                  type: string
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierResponse"

  /suppliers/{supplier_id}/certifications:
    get:
      tags: [Suppliers]
      summary: List supplier certifications
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: active_only
          in: query
          schema: {type: boolean, default: true}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/CertificationResponse"

    post:
      tags: [Suppliers]
      summary: Add certification
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/CertificationCreateRequest"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/CertificationResponse"

  # ──────────────────────────────────────────
  # SCORECARDS
  # ──────────────────────────────────────────
  /suppliers/{supplier_id}/scorecards:
    get:
      tags: [Scorecards]
      summary: Get scorecard history
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: limit
          in: query
          schema: {type: integer, default: 8}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/ScorecardResponse"

  /suppliers/{supplier_id}/scorecards/recalculate:
    post:
      tags: [Scorecards]
      summary: Trigger scorecard recalculation
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                period_start: {type: string, format: date}
                period_end:   {type: string, format: date}
      responses:
        "202":
          description: Recalculation triggered asynchronously
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/AsyncJobResponse"

  /scorecards/ranking:
    get:
      tags: [Scorecards]
      summary: Supplier ranking by category
      parameters:
        - name: category_id
          in: query
          required: true
          schema: {type: string, format: uuid}
        - name: top_n
          in: query
          schema: {type: integer, default: 20}
        - name: min_score
          in: query
          schema: {type: number, default: 0}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/RankingEntry"

  # ──────────────────────────────────────────
  # QUALITY
  # ──────────────────────────────────────────
  /suppliers/{supplier_id}/quality/metrics:
    get:
      tags: [Quality]
      summary: Get quality KPIs
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: periods
          in: query
          schema: {type: integer, default: 8}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/QualityMetricsResponse"

  /suppliers/{supplier_id}/ncr:
    get:
      tags: [Quality]
      summary: List NCRs
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: status
          in: query
          schema: {type: string}
        - name: severity
          in: query
          schema: {type: string, enum: [MINOR,MAJOR,CRITICAL]}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/NCRResponse"

    post:
      tags: [Quality]
      summary: Raise new NCR
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/NCRCreateRequest"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/NCRResponse"

  # ──────────────────────────────────────────
  # DELIVERY
  # ──────────────────────────────────────────
  /suppliers/{supplier_id}/delivery/kpis:
    get:
      tags: [Delivery]
      summary: Delivery performance KPIs
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: from_date
          in: query
          schema: {type: string, format: date}
        - name: to_date
          in: query
          schema: {type: string, format: date}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DeliveryKPIResponse"

  /suppliers/{supplier_id}/delivery/records:
    post:
      tags: [Delivery]
      summary: Record delivery (GR confirmation)
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/DeliveryRecordRequest"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DeliveryRecordResponse"

  # ──────────────────────────────────────────
  # PRICING
  # ──────────────────────────────────────────
  /suppliers/{supplier_id}/prices:
    get:
      tags: [Pricing]
      summary: List active price offers
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: material_id
          in: query
          schema: {type: string, format: uuid}
        - name: active_only
          in: query
          schema: {type: boolean, default: true}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/PriceOfferResponse"

    post:
      tags: [Pricing]
      summary: Submit price offer
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/PriceOfferRequest"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/PriceOfferResponse"

  /prices/benchmark:
    get:
      tags: [Pricing]
      summary: Get market benchmark price for a material
      parameters:
        - name: material_id
          in: query
          required: true
          schema: {type: string, format: uuid}
        - name: date
          in: query
          schema: {type: string, format: date}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/BenchmarkPriceResponse"

  # ──────────────────────────────────────────
  # RISK
  # ──────────────────────────────────────────
  /suppliers/{supplier_id}/risk:
    get:
      tags: [Risk]
      summary: Get risk profile
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/RiskProfileResponse"

  /suppliers/{supplier_id}/risk/alerts:
    get:
      tags: [Risk]
      summary: List active risk alerts
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
        - name: severity
          in: query
          schema: {type: string, enum: [LOW,MEDIUM,HIGH,CRITICAL]}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/RiskAlertResponse"

  /risk/concentration:
    get:
      tags: [Risk]
      summary: Concentration risk analysis by category
      parameters:
        - name: category_id
          in: query
          required: true
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ConcentrationRiskResponse"

  # ──────────────────────────────────────────
  # SEARCH
  # ──────────────────────────────────────────
  /search/suppliers:
    get:
      tags: [Search]
      summary: Full-text supplier search
      parameters:
        - name: q
          in: query
          required: true
          schema: {type: string}
        - name: filters
          in: query
          schema: {type: object}
          style: deepObject
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierListResponse"

  /search/semantic:
    post:
      tags: [Search, AI]
      summary: Semantic supplier search via embeddings
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SemanticSearchRequest"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SemanticSearchResponse"

  /ai/similar-suppliers:
    post:
      tags: [AI]
      summary: Find suppliers similar to a given supplier
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [supplier_id]
              properties:
                supplier_id:
                  type: string
                  format: uuid
                top_k:
                  type: integer
                  default: 10
                min_similarity:
                  type: number
                  default: 0.75
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SimilarSuppliersResponse"

  /ai/recommend-suppliers:
    post:
      tags: [AI]
      summary: Recommend suppliers for a material/process requirement
      requestBody:
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SupplierRecommendationRequest"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierRecommendationResponse"

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    SupplierCreateRequest:
      type: object
      required: [legal_name, supplier_type, country_code]
      properties:
        legal_name:       {type: string}
        trade_name:       {type: string}
        supplier_type:    {type: string, enum: [MANUFACTURER,WHOLESALER,DISTRIBUTOR,SUBCONTRACTOR,TRADER,AGENT,LESSOR]}
        country_code:     {type: string, minLength: 2, maxLength: 2}
        vat_number:       {type: string}
        duns_number:      {type: string}
        website:          {type: string, format: uri}
        primary_category_id: {type: string, format: uuid}

    SupplierUpdateRequest:
      type: object
      properties:
        legal_name:       {type: string}
        trade_name:       {type: string}
        strategic_tier:   {type: string, enum: [TIER1,TIER2,TIER3,SPOT]}
        primary_category_id: {type: string, format: uuid}
        website:          {type: string}

    SupplierResponse:
      type: object
      properties:
        supplier_id:      {type: string, format: uuid}
        legal_name:       {type: string}
        trade_name:       {type: string}
        supplier_type:    {type: string}
        status:           {type: string}
        strategic_tier:   {type: string}
        country_code:     {type: string}
        composite_score:  {type: number}
        rating_class:     {type: string}
        last_scored_at:   {type: string, format: date-time}
        created_at:       {type: string, format: date-time}
        updated_at:       {type: string, format: date-time}
        version:          {type: integer}

    SupplierDetailResponse:
      allOf:
        - $ref: "#/components/schemas/SupplierResponse"
        - type: object
          properties:
            certifications:
              type: array
              items: {$ref: "#/components/schemas/CertificationResponse"}
            capabilities:
              type: array
              items: {type: object}
            contacts:
              type: array
              items: {type: object}
            scorecard: {$ref: "#/components/schemas/ScorecardResponse"}
            risk:      {$ref: "#/components/schemas/RiskProfileResponse"}

    SupplierListResponse:
      type: object
      properties:
        items:
          type: array
          items: {$ref: "#/components/schemas/SupplierResponse"}
        total:      {type: integer}
        page:       {type: integer}
        page_size:  {type: integer}
        pages:      {type: integer}

    ScorecardResponse:
      type: object
      properties:
        scorecard_id:     {type: string, format: uuid}
        period_start:     {type: string, format: date}
        period_end:       {type: string, format: date}
        quality_score:    {type: number}
        delivery_score:   {type: number}
        price_score:      {type: number}
        service_score:    {type: number}
        risk_score:       {type: number}
        composite_score:  {type: number}
        rating_class:     {type: string}
        trend_direction:  {type: string}
        delta_vs_prior:   {type: number}

    RankingEntry:
      type: object
      properties:
        supplier_id:      {type: string, format: uuid}
        legal_name:       {type: string}
        country_code:     {type: string}
        composite_score:  {type: number}
        rating_class:     {type: string}
        rank:             {type: integer}
        percentile:       {type: number}

    QualityMetricsResponse:
      type: object
      properties:
        supplier_id:        {type: string, format: uuid}
        periods:
          type: array
          items:
            type: object
            properties:
              period_year:   {type: integer}
              period_quarter: {type: integer}
              ppm:           {type: number}
              ncr_count:     {type: integer}
              quality_score: {type: number}
        trend_ppm:         {type: string}
        open_ncr_count:    {type: integer}
        critical_ncr_count: {type: integer}

    NCRCreateRequest:
      type: object
      required: [ncr_number, title, severity]
      properties:
        ncr_number:         {type: string}
        title:              {type: string}
        description:        {type: string}
        severity:           {type: string, enum: [MINOR,MAJOR,CRITICAL]}
        defect_type:        {type: string}
        quantity_affected:  {type: integer}
        material_id:        {type: string, format: uuid}
        purchase_order_id:  {type: string, format: uuid}
        target_closure_date: {type: string, format: date}

    NCRResponse:
      type: object
      properties:
        ncr_id:          {type: string, format: uuid}
        ncr_number:      {type: string}
        title:           {type: string}
        severity:        {type: string}
        status:          {type: string}
        raised_at:       {type: string, format: date-time}
        d8_closed_at:    {type: string, format: date-time}

    DeliveryKPIResponse:
      type: object
      properties:
        supplier_id:          {type: string, format: uuid}
        period_from:          {type: string, format: date}
        period_to:            {type: string, format: date}
        total_deliveries:     {type: integer}
        otd_pct:              {type: number}
        otif_pct:             {type: number}
        avg_delay_days:       {type: number}
        delivery_score:       {type: number}

    DeliveryRecordRequest:
      type: object
      required: [purchase_order_id, confirmed_delivery, actual_delivery, ordered_qty, delivered_qty]
      properties:
        purchase_order_id:    {type: string, format: uuid}
        material_id:          {type: string, format: uuid}
        confirmed_delivery:   {type: string, format: date}
        requested_delivery:   {type: string, format: date}
        actual_delivery:      {type: string, format: date}
        ordered_qty:          {type: number}
        delivered_qty:        {type: number}
        accepted_qty:         {type: number}

    DeliveryRecordResponse:
      allOf:
        - $ref: "#/components/schemas/DeliveryRecordRequest"
        - type: object
          properties:
            delivery_id: {type: string, format: uuid}
            delay_days:  {type: integer}
            is_on_time:  {type: boolean}
            is_in_full:  {type: boolean}

    PriceOfferRequest:
      type: object
      required: [material_id, price_per_unit, currency, valid_from, source]
      properties:
        material_id:          {type: string, format: uuid}
        price_per_unit:       {type: number}
        currency:             {type: string}
        price_unit:           {type: string}
        moq_qty:              {type: number}
        valid_from:           {type: string, format: date}
        valid_until:          {type: string, format: date}
        source:               {type: string}
        tooling_cost_eur:     {type: number}
        index_reference:      {type: string}

    PriceOfferResponse:
      allOf:
        - $ref: "#/components/schemas/PriceOfferRequest"
        - type: object
          properties:
            offer_id:            {type: string, format: uuid}
            vs_benchmark_pct:    {type: number}
            is_active:           {type: boolean}
            created_at:          {type: string, format: date-time}

    BenchmarkPriceResponse:
      type: object
      properties:
        material_id:    {type: string, format: uuid}
        date:           {type: string, format: date}
        benchmark_eur:  {type: number}
        source:         {type: string}
        currency:       {type: string}

    RiskProfileResponse:
      type: object
      properties:
        supplier_id:           {type: string, format: uuid}
        overall_risk_score:    {type: number}
        risk_class:            {type: string}
        financial_score:       {type: number}
        geopolitical_score:    {type: number}
        supply_chain_score:    {type: number}
        operational_score:     {type: number}
        compliance_score:      {type: number}
        last_assessed:         {type: string, format: date}
        active_alert_count:    {type: integer}

    RiskAlertResponse:
      type: object
      properties:
        alert_id:           {type: string, format: uuid}
        risk_category:      {type: string}
        alert_type:         {type: string}
        severity:           {type: string}
        title:              {type: string}
        description:        {type: string}
        recommended_action: {type: string}
        status:             {type: string}
        triggered_at:       {type: string, format: date-time}

    ConcentrationRiskResponse:
      type: object
      properties:
        category_id:              {type: string, format: uuid}
        herfindahl_index:         {type: number}
        top_supplier_spend_pct:   {type: number}
        single_source_item_count: {type: integer}
        risk_level:               {type: string}
        suppliers:
          type: array
          items:
            type: object
            properties:
              supplier_id:  {type: string, format: uuid}
              legal_name:   {type: string}
              spend_pct:    {type: number}

    SemanticSearchRequest:
      type: object
      required: [query]
      properties:
        query:         {type: string}
        top_k:         {type: integer, default: 10}
        min_similarity: {type: number, default: 0.70}
        filters:
          type: object
          properties:
            country:     {type: string}
            status:      {type: string}
            tier:        {type: string}
            category_id: {type: string, format: uuid}

    SemanticSearchResponse:
      type: object
      properties:
        query:  {type: string}
        items:
          type: array
          items:
            allOf:
              - $ref: "#/components/schemas/SupplierResponse"
              - type: object
                properties:
                  similarity: {type: number}

    SimilarSuppliersResponse:
      type: object
      properties:
        source_supplier_id: {type: string, format: uuid}
        similar_suppliers:
          type: array
          items:
            allOf:
              - $ref: "#/components/schemas/SupplierResponse"
              - type: object
                properties:
                  similarity_score: {type: number}
                  similarity_dimensions: {type: object}

    SupplierRecommendationRequest:
      type: object
      required: [material_id]
      properties:
        material_id:    {type: string, format: uuid}
        process_class:  {type: string}
        required_qty:   {type: number}
        required_certs:
          type: array
          items: {type: string}
        country_preference: {type: string}
        top_k:          {type: integer, default: 5}

    SupplierRecommendationResponse:
      type: object
      properties:
        material_id: {type: string, format: uuid}
        recommendations:
          type: array
          items:
            type: object
            properties:
              supplier_id:        {type: string, format: uuid}
              legal_name:         {type: string}
              composite_score:    {type: number}
              recommendation_score: {type: number}
              rationale:          {type: string}
              strengths:
                type: array
                items: {type: string}
              risks:
                type: array
                items: {type: string}

    CertificationCreateRequest:
      type: object
      required: [cert_type, valid_from]
      properties:
        cert_type:    {type: string}
        cert_number:  {type: string}
        issuing_body: {type: string}
        scope:        {type: string}
        valid_from:   {type: string, format: date}
        valid_until:  {type: string, format: date}
        document_url: {type: string, format: uri}

    CertificationResponse:
      allOf:
        - $ref: "#/components/schemas/CertificationCreateRequest"
        - type: object
          properties:
            certification_id: {type: string, format: uuid}
            is_verified:      {type: boolean}
            verified_at:      {type: string, format: date-time}

    AsyncJobResponse:
      type: object
      properties:
        job_id:     {type: string, format: uuid}
        status:     {type: string, enum: [QUEUED,RUNNING,COMPLETED,FAILED]}
        created_at: {type: string, format: date-time}
```

---

## 15. Kafka Events

### Topic Configuration

| Topic | Partitions | Retention | Key | Consumers |
|-------|-----------|-----------|-----|-----------|
| `sie.supplier.registered` | 6 | 30d | supplier_id | Search Index, ERP Sync, Embedding Service |
| `sie.supplier.status.changed` | 6 | 30d | supplier_id | Procurement, RFQ Engine, ERP |
| `sie.scorecard.updated` | 12 | 90d | supplier_id | RFQ Engine, Cost Calc, Dashboard |
| `sie.quality.ncr.raised` | 6 | 90d | supplier_id | Quality Dashboard, Scorecard Calc |
| `sie.quality.ncr.closed` | 6 | 90d | supplier_id | Scorecard Calc |
| `sie.delivery.recorded` | 12 | 90d | supplier_id | Performance Calc, Scorecard Calc |
| `sie.price.offer.received` | 12 | 180d | supplier_id | MIE, Cost Calc, Benchmarking |
| `sie.price.expired` | 6 | 30d | supplier_id | Procurement Alert |
| `sie.risk.alert.raised` | 6 | 90d | supplier_id | Procurement Director, Dashboard |
| `sie.risk.alert.resolved` | 6 | 30d | supplier_id | Dashboard |
| `sie.financial.signal.received` | 4 | 180d | supplier_id | Risk Engine |
| `sie.lead_time.changed` | 6 | 30d | supplier_id | Scheduler, MRP, Cost Calc |
| `sie.embedding.refreshed` | 4 | 7d | supplier_id | Vector Search Index |
| `sie.certification.expiring` | 4 | 30d | supplier_id | Procurement Alert |
| `sie.concentration.risk.detected` | 4 | 30d | category_id | Procurement Director |

### Avro Schemas

```json
{
  "namespace": "com.ici.sie.events",
  "name": "SupplierRegisteredEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "event_type",      "type": {"type": "enum", "name": "EventType",
      "symbols": ["SUPPLIER_REGISTERED"]}},
    {"name": "occurred_at",     "type": "long", "logicalType": "timestamp-millis"},
    {"name": "supplier_id",     "type": "string"},
    {"name": "legal_name",      "type": "string"},
    {"name": "supplier_type",   "type": "string"},
    {"name": "country_code",    "type": "string"},
    {"name": "erp_vendor_id",   "type": ["null", "string"], "default": null},
    {"name": "duns_number",     "type": ["null", "string"], "default": null},
    {"name": "registered_by",   "type": ["null", "string"], "default": null},
    {"name": "correlation_id",  "type": ["null", "string"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.sie.events",
  "name": "ScorecardUpdatedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",          "type": "string"},
    {"name": "occurred_at",       "type": "long", "logicalType": "timestamp-millis"},
    {"name": "supplier_id",       "type": "string"},
    {"name": "scorecard_id",      "type": "string"},
    {"name": "period_start",      "type": {"type": "int", "logicalType": "date"}},
    {"name": "period_end",        "type": {"type": "int", "logicalType": "date"}},
    {"name": "composite_score",   "type": "float"},
    {"name": "rating_class",      "type": "string"},
    {"name": "quality_score",     "type": "float"},
    {"name": "delivery_score",    "type": "float"},
    {"name": "price_score",       "type": "float"},
    {"name": "service_score",     "type": "float"},
    {"name": "risk_score",        "type": "float"},
    {"name": "prior_composite",   "type": ["null", "float"], "default": null},
    {"name": "trend_direction",   "type": ["null", "string"], "default": null},
    {"name": "category_id",       "type": ["null", "string"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.sie.events",
  "name": "NCRRaisedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",         "type": "string"},
    {"name": "occurred_at",      "type": "long", "logicalType": "timestamp-millis"},
    {"name": "ncr_id",           "type": "string"},
    {"name": "ncr_number",       "type": "string"},
    {"name": "supplier_id",      "type": "string"},
    {"name": "severity",         "type": "string"},
    {"name": "title",            "type": "string"},
    {"name": "material_id",      "type": ["null", "string"], "default": null},
    {"name": "purchase_order_id","type": ["null", "string"], "default": null},
    {"name": "financial_impact_eur", "type": ["null", "double"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.sie.events",
  "name": "RiskAlertRaisedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",          "type": "string"},
    {"name": "occurred_at",       "type": "long", "logicalType": "timestamp-millis"},
    {"name": "alert_id",          "type": "string"},
    {"name": "supplier_id",       "type": "string"},
    {"name": "risk_category",     "type": "string"},
    {"name": "alert_type",        "type": "string"},
    {"name": "severity",          "type": {"type": "enum", "name": "RiskSeverity",
      "symbols": ["LOW","MEDIUM","HIGH","CRITICAL"]}},
    {"name": "title",             "type": "string"},
    {"name": "recommended_action","type": "string"},
    {"name": "triggered_at",      "type": "long", "logicalType": "timestamp-millis"}
  ]
}
```

```json
{
  "namespace": "com.ici.sie.events",
  "name": "PriceOfferReceivedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "occurred_at",     "type": "long", "logicalType": "timestamp-millis"},
    {"name": "offer_id",        "type": "string"},
    {"name": "supplier_id",     "type": "string"},
    {"name": "material_id",     "type": "string"},
    {"name": "price_per_unit",  "type": "double"},
    {"name": "currency",        "type": "string"},
    {"name": "price_unit",      "type": "string"},
    {"name": "valid_from",      "type": {"type": "int", "logicalType": "date"}},
    {"name": "valid_until",     "type": ["null", {"type": "int", "logicalType": "date"}], "default": null},
    {"name": "source",          "type": "string"},
    {"name": "vs_benchmark_pct","type": ["null", "float"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.sie.events",
  "name": "DeliveryRecordedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",          "type": "string"},
    {"name": "occurred_at",       "type": "long", "logicalType": "timestamp-millis"},
    {"name": "delivery_id",       "type": "string"},
    {"name": "supplier_id",       "type": "string"},
    {"name": "purchase_order_id", "type": "string"},
    {"name": "material_id",       "type": ["null", "string"], "default": null},
    {"name": "actual_delivery",   "type": {"type": "int", "logicalType": "date"}},
    {"name": "delay_days",        "type": "int"},
    {"name": "is_on_time",        "type": "boolean"},
    {"name": "is_in_full",        "type": "boolean"},
    {"name": "line_value_eur",    "type": ["null", "double"], "default": null}
  ]
}
```

### Event Consumer Matrix

| Topic | Consumer Module | Action |
|-------|----------------|--------|
| `sie.supplier.registered` | Search Index | Upsert to Elasticsearch/pgvector |
| `sie.supplier.registered` | ERP Sync | Create vendor in SAP MM |
| `sie.supplier.registered` | Embedding Service | Generate supplier embedding |
| `sie.supplier.status.changed` (SUSPENDED) | RFQ Engine | Block supplier from bidding |
| `sie.supplier.status.changed` (SUSPENDED) | ERP Sync | Block purchase orders |
| `sie.scorecard.updated` | RFQ Engine | Update preferred supplier ranking |
| `sie.scorecard.updated` | Cost Calc Engine | Update supplier price factor |
| `sie.quality.ncr.raised` | Scorecard Calc | Trigger quality recalculation |
| `sie.quality.ncr.raised` (CRITICAL) | Procurement Alert | Notify category manager |
| `sie.delivery.recorded` | Scorecard Calc | Trigger delivery score recalculation |
| `sie.price.offer.received` | Cost Calc Engine | Update price inputs |
| `sie.price.offer.received` | MIE | Update supplier-material mapping |
| `sie.price.expired` | Procurement Alert | Alert buyer to refresh price |
| `sie.risk.alert.raised` (HIGH/CRITICAL) | Procurement Director | Push notification |
| `sie.risk.alert.raised` | Dashboard | Real-time alert feed |
| `sie.financial.signal.received` | Risk Engine | Recalculate financial risk score |
| `sie.lead_time.changed` | MRP / Scheduler | Update MRP parameters |
| `sie.lead_time.changed` | Cost Calc | Update lead time cost factor |
| `sie.embedding.refreshed` | Vector Search Index | Upsert to HNSW index |
| `sie.certification.expiring` | Procurement Alert | 90/30/7 day warning |
