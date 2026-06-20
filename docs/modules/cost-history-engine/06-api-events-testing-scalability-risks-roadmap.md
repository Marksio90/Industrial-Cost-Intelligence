# Cost History Engine — API, Events, Testing, Scalability, Risks & Roadmap

## 19. REST API — OpenAPI 3.1

```yaml
openapi: "3.1.0"
info:
  title: Cost History Engine API
  version: "1.0.0"
  description: |
    Central repository for all historical cost data: snapshots, quotes,
    RFQ rounds, supplier prices, material prices, process cost records.
    Append-only design with full versioning and audit trail.

servers:
  - url: https://api.ici.internal/che/v1
    description: Internal production

security:
  - BearerAuth: []

tags:
  - name: Snapshots
  - name: Quotes
  - name: RFQ
  - name: Pricing
  - name: Analytics
  - name: Search
  - name: Admin

paths:
  # ─────────────────────────────────────
  # SNAPSHOTS
  # ─────────────────────────────────────
  /snapshots:
    get:
      tags: [Snapshots]
      summary: List cost snapshots
      parameters:
        - {name: reference_id,   in: query, schema: {type: string, format: uuid}}
        - {name: reference_type, in: query, schema: {type: string}}
        - {name: snapshot_type,  in: query, schema: {type: string}}
        - {name: status,         in: query, schema: {type: string}}
        - {name: part_number,    in: query, schema: {type: string}}
        - {name: from_date,      in: query, schema: {type: string, format: date}}
        - {name: to_date,        in: query, schema: {type: string, format: date}}
        - {name: page,           in: query, schema: {type: integer, default: 1}}
        - {name: page_size,      in: query, schema: {type: integer, default: 20, maximum: 100}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/SnapshotListResponse"}

    post:
      tags: [Snapshots]
      summary: Create cost snapshot
      requestBody:
        required: true
        content:
          application/json:
            schema: {$ref: "#/components/schemas/CostSnapshotCreateRequest"}
      responses:
        "201":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/CostSnapshotResponse"}

  /snapshots/{snapshot_id}:
    get:
      tags: [Snapshots]
      summary: Get snapshot details
      parameters:
        - {name: snapshot_id, in: path, required: true, schema: {type: string, format: uuid}}
        - name: include
          in: query
          schema: {type: array, items: {type: string, enum: [lines, meta, versions]}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/CostSnapshotResponse"}

  /snapshots/{snapshot_id}/status:
    patch:
      tags: [Snapshots]
      summary: Transition snapshot status
      parameters:
        - {name: snapshot_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [status]
              properties:
                status:    {type: string, enum: [PENDING_APPROVAL, APPROVED, ARCHIVED]}
                reason:    {type: string}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/CostSnapshotResponse"}

  /snapshots/{snapshot_id}/lines:
    get:
      tags: [Snapshots]
      summary: List snapshot line items
      parameters:
        - {name: snapshot_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items: {$ref: "#/components/schemas/SnapshotLineItemResponse"}

  /snapshots/compare:
    post:
      tags: [Snapshots]
      summary: Compare two snapshots
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [snapshot_id_a, snapshot_id_b]
              properties:
                snapshot_id_a: {type: string, format: uuid}
                snapshot_id_b: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/SnapshotDeltaResponse"}

  # ─────────────────────────────────────
  # QUOTES
  # ─────────────────────────────────────
  /quotes:
    get:
      tags: [Quotes]
      summary: List quotes
      parameters:
        - {name: status,      in: query, schema: {type: string}}
        - {name: customer_id, in: query, schema: {type: string, format: uuid}}
        - {name: part_number, in: query, schema: {type: string}}
        - {name: from_date,   in: query, schema: {type: string, format: date}}
        - {name: to_date,     in: query, schema: {type: string, format: date}}
        - {name: page,        in: query, schema: {type: integer, default: 1}}
        - {name: page_size,   in: query, schema: {type: integer, default: 20}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteListResponse"}

    post:
      tags: [Quotes]
      summary: Create quote
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/QuoteCreateRequest"}
      responses:
        "201":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

  /quotes/{quote_id}:
    get:
      tags: [Quotes]
      summary: Get quote
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
        - name: include
          in: query
          schema: {type: array, items: {type: string, enum: [lines, versions, approvals]}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

    patch:
      tags: [Quotes]
      summary: Update quote (DRAFT only)
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/QuoteUpdateRequest"}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

  /quotes/{quote_id}/submit:
    post:
      tags: [Quotes]
      summary: Submit quote for approval
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

  /quotes/{quote_id}/approve:
    post:
      tags: [Quotes]
      summary: Approve quote (role-gated by approval tier)
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                decision_reason: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

  /quotes/{quote_id}/reject:
    post:
      tags: [Quotes]
      summary: Reject quote
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [reason]
              properties:
                reason: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuoteResponse"}

  /quotes/{quote_id}/versions:
    get:
      tags: [Quotes]
      summary: Get quote revision history
      parameters:
        - {name: quote_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items: {$ref: "#/components/schemas/QuoteVersionResponse"}

  # ─────────────────────────────────────
  # RFQ
  # ─────────────────────────────────────
  /rfq:
    get:
      tags: [RFQ]
      summary: List RFQ rounds
      parameters:
        - {name: status,      in: query, schema: {type: string}}
        - {name: material_id, in: query, schema: {type: string, format: uuid}}
        - {name: from_date,   in: query, schema: {type: string, format: date}}
        - {name: page,        in: query, schema: {type: integer, default: 1}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQListResponse"}

    post:
      tags: [RFQ]
      summary: Create RFQ round
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/RFQCreateRequest"}
      responses:
        "201":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQResponse"}

  /rfq/{rfq_id}:
    get:
      tags: [RFQ]
      summary: Get RFQ with bids
      parameters:
        - {name: rfq_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQResponse"}

  /rfq/{rfq_id}/bids:
    post:
      tags: [RFQ]
      summary: Submit supplier bid
      parameters:
        - {name: rfq_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/BidSubmitRequest"}
      responses:
        "201":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/BidResponse"}

  /rfq/{rfq_id}/award:
    post:
      tags: [RFQ]
      summary: Award RFQ to supplier
      parameters:
        - {name: rfq_id, in: path, required: true, schema: {type: string, format: uuid}}
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required: [awarded_supplier_id, award_reason]
              properties:
                awarded_supplier_id: {type: string, format: uuid}
                award_reason:        {type: string}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQResponse"}

  /rfq/{rfq_id}/comparison:
    get:
      tags: [RFQ]
      summary: Bid comparison matrix
      parameters:
        - {name: rfq_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/BidComparisonResponse"}

  /rfq/savings:
    get:
      tags: [RFQ]
      summary: RFQ savings analytics
      parameters:
        - {name: from_date,   in: query, schema: {type: string, format: date}}
        - {name: to_date,     in: query, schema: {type: string, format: date}}
        - {name: category_id, in: query, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQSavingsResponse"}

  # ─────────────────────────────────────
  # PRICING
  # ─────────────────────────────────────
  /pricing/supplier:
    get:
      tags: [Pricing]
      summary: Supplier price history
      parameters:
        - {name: supplier_id, in: query, required: true, schema: {type: string, format: uuid}}
        - {name: material_id, in: query, schema: {type: string, format: uuid}}
        - {name: from_date,   in: query, schema: {type: string, format: date}}
        - {name: to_date,     in: query, schema: {type: string, format: date}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/SupplierPriceHistoryResponse"}

  /pricing/material:
    get:
      tags: [Pricing]
      summary: Material price history with indices
      parameters:
        - {name: material_id, in: query, required: true, schema: {type: string, format: uuid}}
        - {name: from_date,   in: query, schema: {type: string, format: date}}
        - {name: to_date,     in: query, schema: {type: string, format: date}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/MaterialPriceHistoryResponse"}

  /pricing/forecast/{material_id}:
    get:
      tags: [Pricing]
      summary: Material price forecast
      parameters:
        - {name: material_id,    in: path, required: true, schema: {type: string, format: uuid}}
        - {name: horizon_months, in: query, schema: {type: integer, default: 6, maximum: 24}}
        - {name: model,          in: query, schema: {type: string, enum: [PROPHET, ARIMA, LINEAR, ENSEMBLE], default: ENSEMBLE}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/ForecastResponse"}

  /pricing/benchmark:
    get:
      tags: [Pricing]
      summary: Process cost benchmarks
      parameters:
        - {name: process_class, in: query, schema: {type: string, enum: [CUT, MAC, FOR, JOI, ASS, FIN]}}
        - {name: region,        in: query, schema: {type: string, enum: [EU_WEST, EU_EAST, ASIA, NORTH_AMERICA]}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/BenchmarkResponse"}

  # ─────────────────────────────────────
  # ANALYTICS
  # ─────────────────────────────────────
  /analytics/cost-evolution:
    get:
      tags: [Analytics]
      summary: Cost evolution over time for a reference
      parameters:
        - {name: reference_id,   in: query, required: true, schema: {type: string, format: uuid}}
        - {name: reference_type, in: query, required: true, schema: {type: string}}
        - {name: from_date,      in: query, schema: {type: string, format: date}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/CostEvolutionResponse"}

  /analytics/rfq-savings:
    get:
      tags: [Analytics]
      summary: RFQ savings dashboard
      parameters:
        - {name: period_year,    in: query, schema: {type: integer}}
        - {name: category_id,    in: query, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/RFQSavingsDashboardResponse"}

  /analytics/quote-pipeline:
    get:
      tags: [Analytics]
      summary: Quote funnel metrics
      parameters:
        - {name: from_date, in: query, schema: {type: string, format: date}}
        - {name: to_date,   in: query, schema: {type: string, format: date}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/QuotePipelineResponse"}

  # ─────────────────────────────────────
  # SEARCH
  # ─────────────────────────────────────
  /search:
    post:
      tags: [Search]
      summary: Full-text search across snapshots, quotes and RFQs
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/SearchRequest"}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/SearchResponse"}

  # ─────────────────────────────────────
  # ADMIN
  # ─────────────────────────────────────
  /admin/retention:
    get:
      tags: [Admin]
      summary: List retention policies
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items: {$ref: "#/components/schemas/RetentionPolicyResponse"}

  /admin/retention/{policy_id}/execute:
    post:
      tags: [Admin]
      summary: Trigger retention execution
      parameters:
        - {name: policy_id, in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "202":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/AsyncJobResponse"}

  /admin/audit:
    get:
      tags: [Admin]
      summary: Query audit events (COMPLIANCE_OFFICER only)
      parameters:
        - {name: entity_type, in: query, schema: {type: string}}
        - {name: entity_id,   in: query, schema: {type: string, format: uuid}}
        - {name: action,      in: query, schema: {type: string}}
        - {name: actor_id,    in: query, schema: {type: string, format: uuid}}
        - {name: from_dt,     in: query, schema: {type: string, format: date-time}}
        - {name: to_dt,       in: query, schema: {type: string, format: date-time}}
        - {name: page_size,   in: query, schema: {type: integer, default: 100}}
      responses:
        "200":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/AuditEventListResponse"}

  /admin/gdpr/erasure:
    post:
      tags: [Admin]
      summary: Submit GDPR erasure request
      requestBody:
        content:
          application/json:
            schema: {$ref: "#/components/schemas/GDPRErasureRequest"}
      responses:
        "202":
          content:
            application/json:
              schema: {$ref: "#/components/schemas/GDPRRequestResponse"}

  /admin/versions/{entity_type}/{entity_id}:
    get:
      tags: [Admin]
      summary: Version history for any entity
      parameters:
        - {name: entity_type, in: path, required: true, schema: {type: string}}
        - {name: entity_id,   in: path, required: true, schema: {type: string, format: uuid}}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items: {$ref: "#/components/schemas/EntityVersionResponse"}

components:
  securitySchemes:
    BearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  schemas:
    CostSnapshotCreateRequest:
      type: object
      required: [reference_id, reference_type, snapshot_type, part_number, currency]
      properties:
        reference_id:    {type: string, format: uuid}
        reference_type:  {type: string}
        snapshot_type:   {type: string, enum: [PART_COST, RFQ, QUOTE, BUDGET, ACTUALS, BENCHMARK]}
        part_number:     {type: string}
        revision:        {type: string}
        currency:        {type: string, default: EUR}
        tags:            {type: object}
        lines:
          type: array
          items: {$ref: "#/components/schemas/SnapshotLineItemRequest"}

    SnapshotLineItemRequest:
      type: object
      required: [line_type, description, qty, unit, unit_cost_eur]
      properties:
        line_type:        {type: string, enum: [MATERIAL, PROCESS, SUPPLIER, OVERHEAD, TOOLING, LOGISTICS]}
        reference_id:     {type: string, format: uuid}
        description:      {type: string}
        qty:              {type: number}
        unit:             {type: string}
        unit_cost_eur:    {type: number}
        cost_driver:      {type: string}
        cost_driver_value: {type: number}

    CostSnapshotResponse:
      type: object
      properties:
        snapshot_id:        {type: string, format: uuid}
        snapshot_type:      {type: string}
        reference_id:       {type: string, format: uuid}
        reference_type:     {type: string}
        part_number:        {type: string}
        revision:           {type: string}
        total_cost_eur:     {type: number}
        material_cost_eur:  {type: number}
        process_cost_eur:   {type: number}
        overhead_cost_eur:  {type: number}
        tooling_cost_eur:   {type: number}
        logistics_cost_eur: {type: number}
        profit_margin_pct:  {type: number}
        status:             {type: string}
        snapshot_hash:      {type: string}
        version:            {type: integer}
        valid_from:         {type: string, format: date}
        valid_until:        {type: string, format: date}
        approved_at:        {type: string, format: date-time}
        created_at:         {type: string, format: date-time}
        lines:
          type: array
          items: {$ref: "#/components/schemas/SnapshotLineItemResponse"}

    SnapshotLineItemResponse:
      type: object
      properties:
        line_id:          {type: string, format: uuid}
        line_type:        {type: string}
        description:      {type: string}
        qty:              {type: number}
        unit:             {type: string}
        unit_cost_eur:    {type: number}
        total_cost_eur:   {type: number}
        cost_driver:      {type: string}
        cost_driver_value: {type: number}

    SnapshotDeltaResponse:
      type: object
      properties:
        snapshot_id_a:        {type: string, format: uuid}
        snapshot_id_b:        {type: string, format: uuid}
        absolute_delta_eur:   {type: number}
        pct_change:           {type: number}
        material_cost_delta:  {type: number}
        process_cost_delta:   {type: number}
        overhead_cost_delta:  {type: number}
        tooling_cost_delta:   {type: number}
        cost_drivers_changed:
          type: array
          items: {type: string}
        line_deltas:
          type: array
          items:
            type: object
            properties:
              line_type:         {type: string}
              description:       {type: string}
              old_cost_eur:      {type: number}
              new_cost_eur:      {type: number}
              delta_eur:         {type: number}
              delta_pct:         {type: number}

    QuoteCreateRequest:
      type: object
      required: [customer_id, part_number, snapshot_id, quote_type]
      properties:
        customer_id:       {type: string, format: uuid}
        part_number:       {type: string}
        revision:          {type: string}
        snapshot_id:       {type: string, format: uuid}
        quote_type:        {type: string, enum: [INITIAL, REVISED, FINAL, BINDING, INDICATIVE]}
        valid_until:       {type: string, format: date}
        incoterms:         {type: string}
        payment_terms_days: {type: integer}

    QuoteUpdateRequest:
      type: object
      properties:
        valid_until:         {type: string, format: date}
        incoterms:           {type: string}
        payment_terms_days:  {type: integer}

    QuoteResponse:
      type: object
      properties:
        quote_id:           {type: string, format: uuid}
        quote_number:       {type: string}
        revision_number:    {type: integer}
        quote_type:         {type: string}
        status:             {type: string}
        total_price_eur:    {type: number}
        target_price_eur:   {type: number}
        floor_price_eur:
          type: number
          description: "Null for COST_VIEWER role (masked)"
        margin_pct:
          type: number
          description: "Null for COST_VIEWER role (masked)"
        currency:           {type: string}
        valid_until:        {type: string, format: date}
        submitted_at:       {type: string, format: date-time}
        accepted_at:        {type: string, format: date-time}
        version:            {type: integer}
        created_at:         {type: string, format: date-time}

    RFQCreateRequest:
      type: object
      required: [rfq_type, part_number, target_qty, qty_unit, deadline]
      properties:
        rfq_type:         {type: string, enum: [OPEN, SELECTIVE, SINGLE_SOURCE, FRAMEWORK, SPOT]}
        part_number:      {type: string}
        material_id:      {type: string, format: uuid}
        target_qty:       {type: number}
        qty_unit:         {type: string}
        target_price_eur: {type: number}
        budget_price_eur: {type: number}
        deadline:         {type: string, format: date-time}

    RFQResponse:
      type: object
      properties:
        rfq_id:                {type: string, format: uuid}
        rfq_number:            {type: string}
        round_number:          {type: integer}
        rfq_type:              {type: string}
        status:                {type: string}
        part_number:           {type: string}
        target_qty:            {type: number}
        target_price_eur:      {type: number}
        budget_price_eur:      {type: number}
        issued_at:             {type: string, format: date-time}
        deadline:              {type: string, format: date-time}
        awarded_at:            {type: string, format: date-time}
        awarded_supplier_id:   {type: string, format: uuid}
        savings_vs_budget_pct: {type: number}
        savings_vs_prior_pct:  {type: number}
        bids:
          type: array
          items: {$ref: "#/components/schemas/BidResponse"}

    BidSubmitRequest:
      type: object
      required: [supplier_id, bid_price_eur, currency, lead_time_days]
      properties:
        supplier_id:    {type: string, format: uuid}
        bid_price_eur:  {type: number}
        currency:       {type: string}
        lead_time_days: {type: integer}
        moq_qty:        {type: number}

    BidResponse:
      type: object
      properties:
        bid_id:                {type: string, format: uuid}
        supplier_id:           {type: string, format: uuid}
        bid_price_eur:         {type: number}
        lead_time_days:        {type: integer}
        bid_status:            {type: string}
        technical_score:       {type: number}
        commercial_score:      {type: number}
        total_score:           {type: number}
        rank_position:         {type: integer}
        submitted_at:          {type: string, format: date-time}

    BidComparisonResponse:
      type: object
      properties:
        rfq_id:      {type: string, format: uuid}
        bids:
          type: array
          items:
            type: object
            properties:
              rank:               {type: integer}
              supplier_id:        {type: string, format: uuid}
              supplier_name:      {type: string}
              bid_price_eur:      {type: number}
              vs_target_pct:      {type: number}
              vs_budget_pct:      {type: number}
              lead_time_days:     {type: integer}
              total_score:        {type: number}
              is_recommended:     {type: boolean}

    SupplierPriceHistoryResponse:
      type: object
      properties:
        supplier_id:  {type: string, format: uuid}
        material_id:  {type: string, format: uuid}
        records:
          type: array
          items:
            type: object
            properties:
              record_id:       {type: string, format: uuid}
              price_per_unit_eur: {type: number}
              currency:        {type: string}
              price_unit:      {type: string}
              valid_from:      {type: string, format: date}
              valid_until:     {type: string, format: date}
              source:          {type: string}
              vs_benchmark_pct: {type: number}
        yoy_change_pct:        {type: number}
        cagr_3y_pct:           {type: number}
        market_position:       {type: string}

    MaterialPriceHistoryResponse:
      type: object
      properties:
        material_id: {type: string, format: uuid}
        records:
          type: array
          items:
            type: object
            properties:
              valid_date:        {type: string, format: date}
              price_per_kg_eur:  {type: number}
              source:            {type: string}
              index_name:        {type: string}
              anomaly_flag:      {type: boolean}
        index:
          type: object
          properties:
            mom_change_pct:  {type: number}
            yoy_change_pct:  {type: number}
            ma_12m_eur:      {type: number}
            volatility_std:  {type: number}
            all_time_high_eur: {type: number}

    ForecastResponse:
      type: object
      properties:
        material_id:     {type: string, format: uuid}
        model_type:      {type: string}
        horizon_months:  {type: integer}
        mape_backtest:   {type: number}
        forecasts:
          type: array
          items:
            type: object
            properties:
              forecast_date:    {type: string, format: date}
              forecast_value_eur: {type: number}
              confidence_lower: {type: number}
              confidence_upper: {type: number}

    BenchmarkResponse:
      type: array
      items:
        type: object
        properties:
          process_class:  {type: string}
          region:         {type: string}
          min_rate_eur:   {type: number}
          avg_rate_eur:   {type: number}
          max_rate_eur:   {type: number}
          period_year:    {type: integer}
          source:         {type: string}

    CostEvolutionResponse:
      type: object
      properties:
        reference_id:    {type: string, format: uuid}
        reference_type:  {type: string}
        snapshots:
          type: array
          items:
            type: object
            properties:
              snapshot_id:    {type: string, format: uuid}
              created_at:     {type: string, format: date-time}
              total_cost_eur: {type: number}
              status:         {type: string}
              delta_vs_prior_eur: {type: number}
              delta_vs_prior_pct: {type: number}

    SearchRequest:
      type: object
      required: [query]
      properties:
        query:        {type: string}
        scope:
          type: array
          items: {type: string, enum: [snapshots, quotes, rfq]}
          default: [snapshots, quotes, rfq]
        filters:      {type: object}
        page_size:    {type: integer, default: 20}

    SearchResponse:
      type: object
      properties:
        query:   {type: string}
        total:   {type: integer}
        results:
          type: array
          items:
            type: object
            properties:
              entity_type:    {type: string}
              entity_id:      {type: string, format: uuid}
              rank_score:     {type: number}
              highlight:      {type: string}

    ErrorResponse:
      type: object
      properties:
        error_code:     {type: string}
        message:        {type: string}
        details:        {type: object}
        correlation_id: {type: string, format: uuid}
        timestamp:      {type: string, format: date-time}

    AsyncJobResponse:
      type: object
      properties:
        job_id:     {type: string, format: uuid}
        status:     {type: string, enum: [QUEUED, RUNNING, COMPLETED, FAILED]}
        created_at: {type: string, format: date-time}

    AuditEventListResponse:
      type: object
      properties:
        items:
          type: array
          items:
            type: object
            properties:
              event_id:      {type: string, format: uuid}
              occurred_at:   {type: string, format: date-time}
              entity_type:   {type: string}
              entity_id:     {type: string, format: uuid}
              action:        {type: string}
              actor_role:    {type: string}
              changed_fields:
                type: array
                items: {type: string}
        total:     {type: integer}
        page_size: {type: integer}

    EntityVersionResponse:
      type: object
      properties:
        version_id:      {type: string, format: uuid}
        version_number:  {type: integer}
        semantic_version: {type: string}
        change_type:     {type: string}
        change_summary:  {type: string}
        is_current:      {type: boolean}
        created_at:      {type: string, format: date-time}

    GDPRErasureRequest:
      type: object
      required: [data_subject_id, data_subject_type]
      properties:
        data_subject_id:   {type: string, format: uuid}
        data_subject_type: {type: string}
        justification:     {type: string}

    GDPRRequestResponse:
      type: object
      properties:
        request_id:    {type: string, format: uuid}
        status:        {type: string}
        deadline_at:   {type: string, format: date-time}

    QuoteVersionResponse:
      type: object
      properties:
        version_id:      {type: string, format: uuid}
        revision_number: {type: integer}
        changed_by:      {type: string, format: uuid}
        changed_at:      {type: string, format: date-time}
        change_reason:   {type: string}

    RetentionPolicyResponse:
      type: object
      properties:
        policy_id:         {type: string, format: uuid}
        data_class:        {type: string}
        status_filter:     {type: array, items: {type: string}}
        hot_days:          {type: integer}
        warm_days:         {type: integer}
        delete_after_days: {type: integer}
        legal_basis:       {type: string}

    SnapshotListResponse:
      type: object
      properties:
        items:     {type: array, items: {$ref: "#/components/schemas/CostSnapshotResponse"}}
        total:     {type: integer}
        page:      {type: integer}
        page_size: {type: integer}
        pages:     {type: integer}

    QuoteListResponse:
      type: object
      properties:
        items:     {type: array, items: {$ref: "#/components/schemas/QuoteResponse"}}
        total:     {type: integer}
        page:      {type: integer}
        page_size: {type: integer}

    RFQListResponse:
      type: object
      properties:
        items:     {type: array, items: {$ref: "#/components/schemas/RFQResponse"}}
        total:     {type: integer}
        page:      {type: integer}
        page_size: {type: integer}

    RFQSavingsResponse:
      type: object
      properties:
        from_date:                  {type: string, format: date}
        to_date:                    {type: string, format: date}
        total_rfq_count:            {type: integer}
        awarded_rfq_count:          {type: integer}
        total_savings_vs_budget_eur: {type: number}
        avg_savings_vs_budget_pct:  {type: number}
        total_savings_vs_prior_eur:  {type: number}

    RFQSavingsDashboardResponse:
      allOf:
        - {$ref: "#/components/schemas/RFQSavingsResponse"}
        - type: object
          properties:
            savings_by_month:
              type: array
              items:
                type: object
                properties:
                  month:         {type: string}
                  savings_eur:   {type: number}
                  rfq_count:     {type: integer}
            top_savings_suppliers:
              type: array
              items:
                type: object
                properties:
                  supplier_id:   {type: string, format: uuid}
                  savings_eur:   {type: number}
                  awarded_rfqs:  {type: integer}

    QuotePipelineResponse:
      type: object
      properties:
        from_date:    {type: string, format: date}
        to_date:      {type: string, format: date}
        created:      {type: integer}
        submitted:    {type: integer}
        approved:     {type: integer}
        accepted:     {type: integer}
        rejected:     {type: integer}
        expired:      {type: integer}
        avg_approval_time_hours: {type: number}
        avg_margin_pct:          {type: number}
```

### Redis Caching Strategy

| Cache Key Pattern | TTL | Invalidated By |
|-------------------|-----|----------------|
| `che:snapshot:{id}` | 15 min | `che.snapshot.*` event |
| `che:snapshot:list:{hash}` | 2 min | Any snapshot create/status change |
| `che:quote:{id}` | 10 min | `che.quote.*` event |
| `che:rfq:{id}` | 5 min | Any bid or award event |
| `che:price:supplier:{sid}:{mid}` | 30 min | `che.price.supplier.recorded` |
| `che:price:material:{mid}` | 60 min | `che.price.material.updated` |
| `che:forecast:{mid}:{model}` | 4 h | `che.price.forecast.computed` |
| `che:benchmark:{class}:{region}` | 24 h | Time-based TTL only |
| `che:analytics:savings:{period}` | 30 min | `che.rfq.awarded` event |

---

## 20. Kafka Events

### Topic Configuration

| Topic | Partitions | Retention | Key | Description |
|-------|-----------|-----------|-----|-------------|
| `che.snapshot.created` | 12 | 30d | snapshot_id | New cost snapshot created |
| `che.snapshot.approved` | 12 | 90d | snapshot_id | Snapshot approved — downstream can use for cost calc |
| `che.snapshot.superseded` | 6 | 30d | snapshot_id | Snapshot replaced by newer version |
| `che.quote.submitted` | 8 | 90d | quote_id | Quote submitted for approval |
| `che.quote.approved` | 8 | 90d | quote_id | Quote approved — valid for commercial use |
| `che.quote.accepted` | 8 | 180d | quote_id | Quote accepted by customer — binding |
| `che.quote.rejected` | 8 | 90d | quote_id | Quote rejected |
| `che.rfq.issued` | 6 | 90d | rfq_id | RFQ round opened to suppliers |
| `che.rfq.awarded` | 6 | 180d | rfq_id | RFQ awarded, savings captured |
| `che.rfq.cancelled` | 4 | 30d | rfq_id | RFQ cancelled without award |
| `che.price.supplier.recorded` | 12 | 180d | supplier_id | New supplier price offer recorded |
| `che.price.material.updated` | 8 | 180d | material_id | Market price updated from connector |
| `che.price.forecast.computed` | 4 | 7d | material_id | New price forecast generated |
| `che.audit.event.written` | 4 | 30d | entity_id | Audit event persisted (for SIEM) |

### Avro Schemas

```json
{
  "namespace": "com.ici.che.events",
  "name": "CostSnapshotCreatedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "occurred_at",     "type": "long", "logicalType": "timestamp-millis"},
    {"name": "snapshot_id",     "type": "string"},
    {"name": "snapshot_type",   "type": "string"},
    {"name": "reference_id",    "type": "string"},
    {"name": "reference_type",  "type": "string"},
    {"name": "part_number",     "type": "string"},
    {"name": "total_cost_eur",  "type": "double"},
    {"name": "currency",        "type": "string"},
    {"name": "status",          "type": "string"},
    {"name": "created_by",      "type": ["null","string"], "default": null},
    {"name": "correlation_id",  "type": ["null","string"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.che.events",
  "name": "QuoteApprovedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",         "type": "string"},
    {"name": "occurred_at",      "type": "long", "logicalType": "timestamp-millis"},
    {"name": "quote_id",         "type": "string"},
    {"name": "quote_number",     "type": "string"},
    {"name": "revision_number",  "type": "int"},
    {"name": "total_price_eur",  "type": "double"},
    {"name": "margin_pct",       "type": ["null","float"], "default": null},
    {"name": "approved_by",      "type": "string"},
    {"name": "approval_tier",    "type": "string"},
    {"name": "correlation_id",   "type": ["null","string"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.che.events",
  "name": "RFQAwardedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",                "type": "string"},
    {"name": "occurred_at",             "type": "long", "logicalType": "timestamp-millis"},
    {"name": "rfq_id",                  "type": "string"},
    {"name": "rfq_number",              "type": "string"},
    {"name": "awarded_supplier_id",     "type": "string"},
    {"name": "awarded_price_eur",       "type": "double"},
    {"name": "savings_vs_budget_eur",   "type": "double"},
    {"name": "savings_vs_budget_pct",   "type": "float"},
    {"name": "savings_vs_prior_eur",    "type": ["null","double"], "default": null},
    {"name": "award_reason",            "type": "string"},
    {"name": "correlation_id",          "type": ["null","string"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.che.events",
  "name": "SupplierPriceRecordedEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",          "type": "string"},
    {"name": "occurred_at",       "type": "long", "logicalType": "timestamp-millis"},
    {"name": "record_id",         "type": "string"},
    {"name": "supplier_id",       "type": "string"},
    {"name": "material_id",       "type": "string"},
    {"name": "price_per_unit_eur","type": "double"},
    {"name": "currency",          "type": "string"},
    {"name": "price_unit",        "type": "string"},
    {"name": "valid_from",        "type": {"type":"int","logicalType":"date"}},
    {"name": "valid_until",       "type": ["null",{"type":"int","logicalType":"date"}], "default": null},
    {"name": "source",            "type": "string"},
    {"name": "vs_benchmark_pct",  "type": ["null","float"], "default": null}
  ]
}
```

```json
{
  "namespace": "com.ici.che.events",
  "name": "AuditEventWrittenEvent",
  "type": "record",
  "fields": [
    {"name": "event_id",       "type": "string"},
    {"name": "occurred_at",    "type": "long", "logicalType": "timestamp-millis"},
    {"name": "audit_event_id", "type": "string"},
    {"name": "entity_type",    "type": "string"},
    {"name": "entity_id",      "type": "string"},
    {"name": "action",         "type": "string"},
    {"name": "actor_id",       "type": "string"},
    {"name": "actor_role",     "type": "string"},
    {"name": "severity",       "type": {"type":"enum","name":"AuditSeverity",
      "symbols":["LOW","MEDIUM","HIGH","CRITICAL"]}}
  ]
}
```

### Event Consumer Matrix

| Topic | Consumer Module | Action |
|-------|----------------|--------|
| `che.snapshot.approved` | Cost Calculation Engine | Update price inputs with new approved snapshot |
| `che.snapshot.approved` | Dashboard | Refresh cost overview widget |
| `che.snapshot.superseded` | CCE Redis cache | Invalidate `che:snapshot:{id}` |
| `che.quote.accepted` | ERP Sync | Create sales order reference in SAP SD |
| `che.quote.accepted` | Dashboard | Update quote pipeline funnel |
| `che.rfq.awarded` | SIE | Record savings vs prior contract on supplier scorecard |
| `che.rfq.awarded` | Dashboard | Update RFQ savings KPIs |
| `che.rfq.issued` | SIE | Notify preferred suppliers eligible for this RFQ |
| `che.price.supplier.recorded` | MIE | Update supplier-material price mapping |
| `che.price.supplier.recorded` | CCE | Invalidate affected cached cost calculations |
| `che.price.material.updated` | CCE | Trigger recalculation for all parts using this material |
| `che.price.forecast.computed` | Dashboard | Refresh forecast sparklines |
| `che.audit.event.written` | SIEM (Splunk/ELK) | Security monitoring and alerting |

### Transactional Outbox Pattern

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4
import json

@dataclass
class OutboxRecord:
    outbox_id:      UUID
    topic:          str
    message_key:    str
    payload:        dict
    correlation_id: UUID
    created_at:     datetime
    published_at:   datetime | None = None
    retry_count:    int = 0
    status:         str = "PENDING"   # PENDING / PUBLISHED / FAILED

class CHEOutboxPublisher:
    """
    Transactional outbox: events written to che_outbox table
    in the SAME database transaction as business data.
    A relay process polls and publishes to Kafka every 500ms.
    """

    def publish_in_transaction(
        self,
        conn,
        topic: str,
        key: str,
        payload: dict,
        correlation_id: UUID | None = None,
    ) -> UUID:
        outbox_id = uuid4()
        conn.execute("""
            INSERT INTO cost_history.che_outbox
                (outbox_id, topic, message_key, payload, correlation_id)
            VALUES (%(id)s, %(topic)s, %(key)s, %(payload)s, %(corr)s)
        """, {
            "id": outbox_id, "topic": topic, "key": key,
            "payload": json.dumps(payload), "corr": str(correlation_id),
        })
        return outbox_id

    def relay_pending_events(self, kafka_producer, batch_size: int = 100):
        with db.transaction() as conn:
            rows = conn.execute("""
                SELECT outbox_id, topic, message_key, payload
                FROM cost_history.che_outbox
                WHERE status = 'PENDING'
                ORDER BY created_at
                LIMIT %(n)s
                FOR UPDATE SKIP LOCKED
            """, {"n": batch_size}).fetchall()

            for row in rows:
                kafka_producer.produce(
                    topic=row["topic"],
                    key=row["message_key"].encode(),
                    value=row["payload"].encode(),
                )
            kafka_producer.flush()

            ids = [str(r["outbox_id"]) for r in rows]
            if ids:
                conn.execute("""
                    UPDATE cost_history.che_outbox
                    SET status = 'PUBLISHED', published_at = now()
                    WHERE outbox_id = ANY(%(ids)s)
                """, {"ids": ids})
```

---

## 21. Testing

### Unit Tests

```python
import pytest
import hashlib
import json
from datetime import date

class TestCostSnapshotBuilder:
    def test_hash_deterministic(self):
        """Same line items always produce same SHA-256 hash."""
        builder = CostSnapshotBuilder()
        builder.add_line(LineType.MATERIAL, "Steel S355", qty=10, unit="KG", unit_cost_eur=2.50)
        snap_a = builder.finalize()
        snap_b = builder.finalize()
        assert snap_a.snapshot_hash == snap_b.snapshot_hash

    def test_hash_changes_on_line_modification(self):
        """Changing a line item changes the hash."""
        builder = CostSnapshotBuilder()
        builder.add_line(LineType.MATERIAL, "Steel", qty=10, unit="KG", unit_cost_eur=2.50)
        snap_a = builder.finalize()
        builder.add_line(LineType.MATERIAL, "Steel", qty=10, unit="KG", unit_cost_eur=2.60)
        snap_b = builder.finalize()
        assert snap_a.snapshot_hash != snap_b.snapshot_hash

    def test_finalize_rejects_empty_snapshot(self):
        builder = CostSnapshotBuilder()
        with pytest.raises(ValueError, match="at least one line item"):
            builder.finalize()

    def test_cost_aggregation(self):
        builder = CostSnapshotBuilder()
        builder.add_line(LineType.MATERIAL, "Steel", qty=10, unit="KG", unit_cost_eur=2.50)
        builder.add_line(LineType.PROCESS,  "Laser cut", qty=1, unit="OP", unit_cost_eur=15.00)
        snap = builder.finalize()
        assert snap.total_cost_eur == pytest.approx(40.00)
        assert snap.material_cost_eur == pytest.approx(25.00)
        assert snap.process_cost_eur  == pytest.approx(15.00)

class TestRFQScoringEngine:
    engine = RFQScoringEngine()

    @pytest.mark.parametrize("bid,best,worst,expected", [
        (100.0, 100.0, 200.0, 100.0),   # best bid → 100
        (200.0, 100.0, 200.0,   0.0),   # worst bid → 0
        (150.0, 100.0, 200.0,  50.0),   # mid bid → 50
    ])
    def test_price_normalization_bounds(self, bid, best, worst, expected):
        score = self.engine.normalize_price_score(bid, best, worst)
        assert score == pytest.approx(expected)

    def test_weights_sum_to_one(self):
        total = sum(self.engine.WEIGHTS.values())
        assert abs(total - 1.0) < 1e-9

    def test_award_recommendation_picks_highest_ranked(self):
        bids = [
            BidScoreInput(supplier_id="A", bid_price_eur=95.0, lead_time_days=10,
                          quality_history_score=85.0, risk_score=80.0),
            BidScoreInput(supplier_id="B", bid_price_eur=110.0, lead_time_days=5,
                          quality_history_score=90.0, risk_score=75.0),
        ]
        ranked = self.engine.rank_bids([self.engine.score_bid(b, rfq=mock_rfq()) for b in bids])
        rec = self.engine.generate_award_recommendation(ranked)
        assert rec.recommended_supplier_id == ranked[0].supplier_id

    def test_savings_calculation(self):
        savings = self.engine.calculate_savings(
            awarded_price=90.0, budget_price=100.0, prior_contract_price=95.0
        )
        assert savings.savings_vs_budget_pct == pytest.approx(10.0)
        assert savings.savings_vs_prior_pct  == pytest.approx(5.26, abs=0.01)

class TestVersionManager:
    def test_rollback_creates_new_version_not_mutate(self):
        mgr = VersionManager(db=mock_db())
        v3_id = "version-3-id"
        old_count = mock_db().version_count
        mgr.rollback_to("Quote", "quote-1", v3_id, reason="Rollback test", actor_id="user-1")
        # Should have added a new version, not modified v3
        assert mock_db().version_count == old_count + 1
        v3 = mock_db().get_version(v3_id)
        assert v3.is_current is False  # v3 still exists, unchanged

    def test_diff_detects_price_change(self):
        mgr = VersionManager(db=mock_db())
        old = {"total_price_eur": 1000.0, "margin_pct": 15.0}
        new = {"total_price_eur": 1050.0, "margin_pct": 12.0}
        diffs = mgr._compute_diff(old, new)
        price_diff = next(d for d in diffs if d.field_name == "total_price_eur")
        assert price_diff.delta_absolute == pytest.approx(50.0)
        assert price_diff.delta_pct       == pytest.approx(5.0)

    def test_get_version_at_returns_correct_historical_state(self):
        mgr = VersionManager(db=mock_db_with_versions())
        from datetime import datetime, timezone
        point_in_time = datetime(2025, 6, 1, 12, 0, tzinfo=timezone.utc)
        v = mgr.get_version_at("Quote", "quote-1", point_in_time)
        assert v.version_number == 2   # v2 was current on that date
```

### Integration Tests (Testcontainers)

```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.kafka import KafkaContainer

@pytest.fixture(scope="session")
def postgres():
    with PostgresContainer("postgres:16-alpine") as pg:
        conn = pg.get_connection_url()
        run_migrations(conn)  # applies cost_history schema + partitions
        yield pg

@pytest.fixture(scope="session")
def kafka():
    with KafkaContainer("confluentinc/cp-kafka:7.5.0") as k:
        yield k

class TestSnapshotRepository:
    def test_create_and_approve_snapshot_flow(self, postgres):
        repo = SnapshotRepository(postgres.get_connection_url())
        snap_id = repo.create(snapshot=build_test_snapshot())

        repo.transition_status(snap_id, "PENDING_APPROVAL", actor="user-1")
        repo.transition_status(snap_id, "APPROVED", actor="manager-1")

        snap = repo.get(snap_id)
        assert snap.status == "APPROVED"
        assert snap.approved_by == "manager-1"

    def test_snapshot_hash_verification(self, postgres):
        repo = SnapshotRepository(postgres.get_connection_url())
        snap_id = repo.create(snapshot=build_test_snapshot())
        result = repo.verify_hash(snap_id)
        assert result is True

    def test_version_chain_on_supersede(self, postgres):
        repo = SnapshotRepository(postgres.get_connection_url())
        snap_a = repo.create(snapshot=build_test_snapshot())
        snap_b = repo.create(snapshot=build_test_snapshot(parent_id=snap_a))

        a = repo.get(snap_a)
        assert a.status == "SUPERSEDED"
        b = repo.get(snap_b)
        assert b.parent_snapshot_id == snap_a

class TestAuditTrail:
    def test_quote_approval_writes_audit_event(self, postgres, kafka):
        quote_id = create_and_submit_quote(postgres)
        approve_quote(postgres, quote_id, actor="manager-1")

        audit_events = get_audit_events(postgres, entity_id=quote_id, action="APPROVE")
        assert len(audit_events) == 1
        assert audit_events[0].actor_role is not None

    def test_audit_event_immutable_no_update(self, postgres):
        """RLS must block UPDATE on audit_events."""
        with pytest.raises(Exception, match="permission denied"):
            postgres.execute(
                "UPDATE cost_history.audit_events SET action = 'TAMPERED' WHERE TRUE",
                as_role="che_app",
            )

    def test_sensitive_field_access_logged(self, postgres):
        """Reading floor_price triggers READ_SENSITIVE audit event."""
        get_quote_with_floor_price(postgres, quote_id="q-1", role="QUOTE_MANAGER")
        events = get_audit_events(postgres, action="READ_SENSITIVE")
        assert any(e.entity_id == "q-1" for e in events)
```

### Contract Tests (Pact)

```python
from pact import Consumer, Provider

def test_che_contract_for_cost_calculation_engine():
    pact = Consumer("CostCalculationEngine").has_pact_with(Provider("CHE"))
    pact.given("Approved snapshot exists for reference BOM-001") \
        .upon_receiving("snapshot approved event on che.snapshot.approved") \
        .with_request("GET", "/che/v1/snapshots/snap-001") \
        .will_respond_with(200, body={
            "snapshot_id":    Like("snap-001"),
            "status":         "APPROVED",
            "total_cost_eur": Like(1250.50),
            "snapshot_hash":  Like("a" * 64),
        })
    with pact:
        result = CCEClient().fetch_snapshot("snap-001")
        assert result["status"] == "APPROVED"

def test_che_contract_for_rfq_engine():
    pact = Consumer("RFQEngine").has_pact_with(Provider("CHE"))
    pact.given("RFQ round exists with 3 bids") \
        .upon_receiving("bid comparison request") \
        .with_request("GET", "/che/v1/rfq/rfq-001/comparison") \
        .will_respond_with(200, body={
            "rfq_id": Like("rfq-001"),
            "bids": EachLike({
                "rank":            Like(1),
                "bid_price_eur":   Like(950.0),
                "total_score":     Like(82.5),
                "is_recommended":  Like(True),
            }),
        })
    with pact:
        result = RFQClient().get_bid_comparison("rfq-001")
        assert len(result["bids"]) >= 1
```

### Test Matrix

| Type | Tool | Target | SLA |
|------|------|--------|-----|
| Unit | pytest | Score algorithms, hash, version diff, savings calc | >90% line coverage, 100% pass |
| Integration | Testcontainers (PG16 + Kafka) | DB ops, triggers, RLS, Kafka outbox | 100% critical paths |
| Contract | Pact | CHE↔CCE, CHE↔SIE, CHE↔RFQ, CHE↔MIE | 100% published events |
| API | pytest + httpx | All 22 endpoints, auth, error cases, pagination | 100% endpoints |
| Load | k6 | P95 read <300ms, write <500ms, 1000 RPS | P95 targets met |
| Security | OWASP ZAP + Trivy | OWASP Top 10, CVEs | 0 critical findings |
| Data quality | great_expectations | Schema, null %, hash integrity, score bounds | 100% expectations |
| Performance | pgbench + EXPLAIN ANALYZE | Index utilization, partition pruning | All queries use indexes |

### k6 Load Test

```javascript
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [
    { duration: "2m", target: 200  },
    { duration: "5m", target: 1000 },
    { duration: "2m", target: 0    },
  ],
  thresholds: {
    http_req_duration: ["p(95)<300", "p(99)<500"],
    http_req_failed:   ["rate<0.005"],
  },
};

const BASE = "https://api.ici.internal/che/v1";

export default function () {
  const headers = { Authorization: `Bearer ${__ENV.TOKEN}` };

  // Read-heavy: list snapshots
  const snaps = http.get(`${BASE}/snapshots?status=APPROVED&page_size=20`, { headers });
  check(snaps, { "snapshots 200": (r) => r.status === 200 });

  // RFQ comparison (analytical)
  const comp = http.get(`${BASE}/rfq/${__ENV.RFQ_ID}/comparison`, { headers });
  check(comp, { "comparison 200": (r) => r.status === 200 });

  // Price history (time-series)
  const price = http.get(
    `${BASE}/pricing/supplier?supplier_id=${__ENV.SUP_ID}&material_id=${__ENV.MAT_ID}`,
    { headers }
  );
  check(price, { "price 200": (r) => r.status === 200 });

  // Forecast
  const forecast = http.get(`${BASE}/pricing/forecast/${__ENV.MAT_ID}?horizon_months=6`, { headers });
  check(forecast, { "forecast 200": (r) => r.status === 200 });

  sleep(0.1);
}
```

---

## 22. Scalability

### Scalability Tiers

| Tier | Snapshots | Total Records | Peak RPS | Architecture |
|------|-----------|--------------|----------|-------------|
| Small | <100k | <5M | <50 | Single PG16 + Redis + 2 API pods |
| Medium | 100k–2M | 5M–100M | 50–500 | PG primary + 2 read replicas + Redis cluster + 5 API pods + PgBouncer |
| Large | 2M+ | 100M+ | 500+ | PG primary + 4 read replicas + ClickHouse analytics + Redis cluster + 10+ API pods + PgBouncer |

### Read Replica Routing

```python
from enum import Enum

class OperationType(str, Enum):
    READ_LIST    = "READ_LIST"
    READ_DETAIL  = "READ_DETAIL"
    ANALYTICS    = "ANALYTICS"
    WRITE        = "WRITE"

READ_OPERATIONS = {OperationType.READ_LIST, OperationType.READ_DETAIL, OperationType.ANALYTICS}

class CHEDatabaseRouter:
    def __init__(self, primary_url: str, replica_urls: list[str]):
        self.primary  = create_engine(primary_url, pool_size=10, max_overflow=20)
        self.replicas = [create_engine(url, pool_size=5, max_overflow=10) for url in replica_urls]
        self._idx = 0

    def get_connection(self, operation: OperationType):
        if operation in READ_OPERATIONS and self.replicas:
            engine = self.replicas[self._idx % len(self.replicas)]
            self._idx += 1
            return engine.connect()
        return self.primary.connect()
```

### PgBouncer Configuration

```ini
[databases]
cost_history = host=pg-primary port=5432 dbname=cost_history

[pgbouncer]
pool_mode          = transaction
max_client_conn    = 1000
default_pool_size  = 20
min_pool_size      = 5
reserve_pool_size  = 5
reserve_pool_timeout = 5
server_idle_timeout  = 600
log_connections    = 0
log_disconnections = 0
```

### ClickHouse for Analytics

```sql
-- Long-range material price queries (100M+ rows)
CREATE TABLE che_analytics.material_price_history (
    material_id      UUID,
    valid_date       Date,
    price_per_kg_eur Float64,
    source           String,
    index_name       LowCardinality(String),
    region_code      LowCardinality(String),
    anomaly_flag     UInt8
) ENGINE = MergeTree()
PARTITION BY toYYYYMM(valid_date)
ORDER BY (material_id, valid_date)
SETTINGS index_granularity = 8192;

-- Materialized view for monthly averages
CREATE MATERIALIZED VIEW che_analytics.material_price_monthly
ENGINE = AggregatingMergeTree()
ORDER BY (material_id, month)
AS SELECT
    material_id,
    toStartOfMonth(valid_date) AS month,
    avgState(price_per_kg_eur) AS avg_price_state,
    minState(price_per_kg_eur) AS min_price_state,
    maxState(price_per_kg_eur) AS max_price_state
FROM che_analytics.material_price_history
GROUP BY material_id, month;
```

### Cache Warming

```python
class CacheWarmupService:
    def warm_on_startup(self, redis, db):
        # 1. Active quotes (SUBMITTED + NEGOTIATING)
        active_quotes = db.execute(
            "SELECT quote_id FROM cost_history.quotes WHERE status IN ('SUBMITTED','NEGOTIATING')"
        ).fetchall()
        for q in active_quotes:
            quote_data = db.execute("SELECT * FROM cost_history.quotes WHERE quote_id = %s", (q,)).fetchone()
            redis.setex(f"che:quote:{q}", 600, json.dumps(quote_data))

        # 2. Recent approved snapshots (last 30 days)
        recent = db.execute("""
            SELECT snapshot_id FROM cost_history.cost_snapshots
            WHERE status = 'APPROVED' AND created_at >= now() - INTERVAL '30 days'
        """).fetchall()
        for s in recent:
            snap_data = fetch_snapshot_with_lines(db, s)
            redis.setex(f"che:snapshot:{s}", 900, json.dumps(snap_data))

        # 3. All benchmarks (low volume, high reuse)
        benchmarks = db.execute("SELECT * FROM cost_history.benchmark_snapshots").fetchall()
        by_class = group_by(benchmarks, "process_class", "region")
        for key, data in by_class.items():
            redis.setex(f"che:benchmark:{key}", 86400, json.dumps(data))
```

### Query Optimization Examples

```sql
-- Partition pruning: only scans 2025 Q2 partition (not all 40 partitions)
EXPLAIN (ANALYZE, BUFFERS)
SELECT snapshot_id, total_cost_eur
FROM cost_history.cost_snapshots
WHERE created_at BETWEEN '2025-04-01' AND '2025-06-30'
  AND status = 'APPROVED';
-- → Index Scan on cost_snapshots_2025_q2 (rows=1247, loops=1, Buffers: shared hit=12)

-- Index-only scan for supplier price lookup
EXPLAIN (ANALYZE)
SELECT price_per_unit_eur, valid_from, valid_until
FROM cost_history.supplier_price_records
WHERE supplier_id = 'sup-001' AND material_id = 'mat-001' AND is_active = TRUE
ORDER BY valid_from DESC LIMIT 1;
-- → Index Only Scan using idx_spr_supplier_material_active (cost=0.29..8.31 rows=1)

-- Parallel aggregation for analytics
SET max_parallel_workers_per_gather = 4;
SELECT DATE_TRUNC('month', issued_at) AS month,
       COUNT(*) AS rfq_count,
       SUM(savings_vs_budget_eur) AS total_savings
FROM cost_history.rfq_rounds
WHERE status = 'AWARDED'
  AND issued_at >= '2024-01-01'
GROUP BY 1 ORDER BY 1;
-- → Gather (workers=4) → Partial HashAggregate
```

---

## 23. Risk Register

| # | Risk | P | I | Mitigation |
|---|------|---|---|------------|
| R1 | Snapshot hash tampering — malicious modification of approved snapshot | N | K | SHA-256 stored at finalization; `verify_snapshot_hash()` run on every read; append-only table |
| R2 | Legal hold gap — data deleted before litigation completed | N | K | `legal_holds` table checked before every DELETE; DB-level block via RLS; alert on hold count |
| R3 | Floor price / margin exposure to unauthorized roles | Ś | W | Column masking view; RLS policy; AES-256 field encryption; READ_SENSITIVE audit event on access |
| R4 | Kafka outbox lag → events lost during crash | N | W | Transactional outbox pattern; relay with exponential backoff; at-least-once + idempotent consumers |
| R5 | Default partition overflow (missing future partition) | Ś | Ś | `PartitionMaintenanceJob` creates 6 months ahead; alert on default partition row_count > 0 |
| R6 | Read replica lag during write spike | Ś | Ś | Route analytics to replicas; alert on lag >5s; auto-fallback to primary on lag >30s |
| R7 | Audit log storage cost explosion | W | Ś | Monthly partitions; S3 cold archive after 12 months; compression; 7-year then GDPR-compliant delete |
| R8 | GDPR erasure blocked by active legal hold | N | Ś | Block erasure request; notify data subject of hold existence + expected release date |
| R9 | Price forecast drift — MAPE > 8% | Ś | Ś | Monthly walk-forward backtest; auto-retrain if MAPE threshold breached; ensemble fallback |
| R10 | Index bloat on high-write audit_events | W | Ś | Weekly `REINDEX CONCURRENTLY`; autovacuum tuning (`vacuum_scale_factor = 0.01`); bloat gauge alert |
| R11 | Cross-border data transfer compliance failure | N | W | Data stays in EU; SCCs for D&B/Coface API calls; annual DPA review; DPIA updated annually |
| R12 | ERP sync failure — actuals not recorded | Ś | Ś | Kafka dead-letter queue; exponential backoff retry (3×); alert after 3 consecutive failures |
| R13 | API bulk export → competitive price intelligence leak | N | W | Rate limiting 200 req/min; bulk export (>1000 rows) requires COMPLIANCE_OFFICER approval; watermarked |
| R14 | PostgreSQL version incompatibility (pg_trgm / pgvector) | N | Ś | Pin extension versions in Docker image; test on PG16 only; upgrade gated by integration test suite |

**P = Prawdopodobieństwo: N=Niskie, Ś=Średnie, W=Wysokie**
**I = Wpływ: N=Niski, Ś=Średni, W=Wysoki, K=Krytyczny**

---

## 24. Roadmap

### Faza 1 — Foundation (Miesiąc 1–3)

| Sprint | Zakres |
|--------|--------|
| S1 | PostgreSQL schema (24 tables, 14 ENUMs, partitions, indexes, triggers, functions) |
| S2 | `CostSnapshot` CRUD, `CostSnapshotBuilder`, SHA-256 hash, status machine |
| S3 | `SnapshotLineItem` management, `SnapshotMeta`, `CostSnapshotComparator` |
| S4 | `Quote` CRUD, `QuotePricer` (floor/target/volume discount), margin guards |
| S5 | Quote approval workflow (4 tiers: auto/manager/director/exec), `QuoteVersion` tracking |
| S6 | REST API for Snapshots + Quotes (OpenAPI), JWT RS256, RBAC, RLS |

**Deliverable:** Cost snapshot creation with cryptographic hash integrity and full quote lifecycle with tiered approval.

---

### Faza 2 — RFQ & Pricing History (Miesiąc 4–6)

| Sprint | Zakres |
|--------|--------|
| S7 | `RFQRound` CRUD, `RFQLine`, `RFQBid` submission |
| S8 | `RFQScoringEngine` (weighted scoring, normalization, ranking) |
| S9 | Award workflow, savings calculation (vs budget + vs prior contract) |
| S10 | `SupplierPriceRecord`, `PriceAdjustment`, `IndexLink` |
| S11 | `MaterialPriceRecord`, market data connectors (LME, Platts, ICIS, PPI) |
| S12 | Kafka outbox, all 14 topics, Avro schemas, consumer integrations |
| S13 | Redis caching layer (9 patterns), cache invalidation on domain events |
| S14 | RFQ + Pricing REST API endpoints, BidComparison matrix |

**Deliverable:** Full RFQ lifecycle with savings tracking, supplier and material price history, event-driven integrations with CCE/SIE/MIE.

---

### Faza 3 — Intelligence & Compliance (Miesiąc 7–9)

| Sprint | Zakres |
|--------|--------|
| S15 | `VersionManager` (semantic versioning, diff, rollback, labels, `SnapshotLineageTracer`) |
| S16 | `AuditEvent` append-only (RESTRICTIVE RLS, WAL archiving, immutability guarantees) |
| S17 | `AuditEventConsumer` (Kafka → audit log), `@log_sensitive_access` decorator |
| S18 | `RetentionManager` (hot/warm/cold/delete phases), `LegalHold` mechanism |
| S19 | `GDPRErasureHandler`, `DataAccessReport` (Art. 15), erasure verification |
| S20 | `PriceForecastEngine` (Linear + Prophet + ARIMA + ensemble, MAPE<8% backtest) |
| S21 | `ProcessCostRecord`, `OEESnapshot`, `ProcessCostBenchmark`, benchmark table |
| S22 | `BackupOrchestrator` (WAL + basebackup + logical dump + Glacier), restore test automation |

**Deliverable:** Full versioning, immutable audit trail, GDPR compliance (Art. 17+15), price forecasting, automated backup with restore testing.

---

### Faza 4 — Scale & Analytics (Miesiąc 10–12)

| Sprint | Zakres |
|--------|--------|
| S23 | PostgreSQL read replicas, `CHEDatabaseRouter`, PgBouncer connection pooling |
| S24 | ClickHouse analytics table for 100M+ price records, materialized monthly view |
| S25 | Analytics API: `/analytics/cost-evolution`, `/analytics/rfq-savings`, `/analytics/quote-pipeline` |
| S26 | Full-text search (tsvector) + semantic search (pgvector HNSW embeddings) across CHE |
| S27 | Grafana dashboards (6 dashboards), Prometheus metrics (20), Alertmanager (8 rules) |
| S28 | k6 load testing — P95 <300ms read, <500ms write at 1000 RPS; bottleneck resolution |
| S29 | OWASP ZAP security scan, SOX control mapping, penetration test findings remediation |
| S30 | Pact contract tests (CHE↔CCE, CHE↔SIE, CHE↔RFQ, CHE↔MIE) |

**Deliverable:** Production-grade scalability (1000+ RPS), analytics, full monitoring stack, security posture for SOX/GDPR audit.

---

### Długoterminowe inicjatywy (12m+)

| Inicjatywa | Opis |
|-----------|------|
| **Carbon Cost Layer** | Embed Scope 3 emission cost (CO₂e/unit) into every snapshot line item; align with CSRD reporting |
| **Digital Quote Twin** | AI-generated quote commentary using GPT-4 + CHE history for negotiation support and win-rate analysis |
| **Blockchain Audit Anchoring** | Monthly hash of audit_events partition anchored to Hyperledger Fabric for tamper-proof regulatory evidence |
| **Multi-Currency Analytics** | Real-time FX normalization for global plant cost comparison; hedging exposure calculation |
| **Cost Intelligence Copilot** | RAG system over CHE historical snapshots: "Why did part X cost increase 12% vs Q1?" natural language Q&A |
| **Predictive Savings Engine** | ML model predicting RFQ savings potential before issuing RFQ, based on market indices + supplier score trends |
