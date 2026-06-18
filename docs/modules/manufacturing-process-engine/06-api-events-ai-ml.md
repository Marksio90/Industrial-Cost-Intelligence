# Manufacturing Process Engine — API, Events, AI/ML

## 18. API

### Specyfikacja REST API (OpenAPI 3.1 — skrócona)

```yaml
openapi: "3.1.0"
info:
  title: Manufacturing Process Engine API
  version: "1.0.0"
  description: |
    REST API for the Manufacturing Process Engine.
    Provides access to process definitions, machines, OEE, cost models,
    capacity planning, and AI-powered process search.

servers:
  - url: https://api.industrial-cost-intelligence.com/v1/processes

tags:
  - name: Processes
  - name: Parameters
  - name: Machines
  - name: OEE
  - name: Tools
  - name: Operators
  - name: Costs
  - name: Capacity
  - name: Search
  - name: AI

paths:

  # PROCESSES
  /processes:
    get:
      tags: [Processes]
      operationId: listProcesses
      summary: List manufacturing processes
      parameters:
        - { name: process_class, in: query, schema: { type: string, enum: [CUT,MAC,FOR,JOI,ASS,FIN] } }
        - { name: category_code, in: query, schema: { type: string }, example: "MAC.MI" }
        - { name: status, in: query, schema: { type: string }, default: ACTIVE }
        - { name: material_class, in: query, schema: { type: string }, description: "Filter by compatible material class" }
        - { name: q, in: query, schema: { type: string } }
        - { name: page, in: query, schema: { type: integer, default: 1 } }
        - { name: page_size, in: query, schema: { type: integer, default: 50 } }
      responses:
        "200": { description: Paginated process list }

    post:
      tags: [Processes]
      operationId: createProcess
      summary: Create new manufacturing process
      responses:
        "201": { description: Process created }

  /processes/{process_id}:
    get:
      tags: [Processes]
      operationId: getProcess
      summary: Get process detail
      parameters:
        - { name: process_id, in: path, required: true, schema: { type: string, format: uuid } }
        - name: include
          in: query
          schema:
            type: array
            items:
              type: string
              enum: [parameters, compatibility, cost_models, machines, embeddings]
      responses:
        "200": { description: Process detail }
        "404": { description: Not found }

  /processes/{process_id}/parameters:
    get:
      tags: [Parameters]
      operationId: getProcessParameters
      summary: Get all parameters for process
      responses:
        "200": { description: Parameter list }

    put:
      tags: [Parameters]
      operationId: setProcessParameter
      summary: Set process parameter value
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [param_key, param_value, param_type]
              properties:
                param_key:   { type: string }
                param_value: { type: string }
                param_type:  { type: string }
                unit:        { type: string }
      responses:
        "200": { description: Parameter updated }

  /processes/{process_id}/compatibility:
    get:
      tags: [Processes]
      operationId: getProcessCompatibility
      summary: Get material compatibility matrix for process
      responses:
        "200": { description: Compatibility matrix }

  /processes/{process_id}/cost-estimate:
    post:
      tags: [Costs]
      operationId: estimateProcessCost
      summary: Estimate cost for a process operation
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/CostEstimateRequest"
      responses:
        "200":
          description: Full cost breakdown
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/CostEstimateResponse"

  # MACHINES
  /machines:
    get:
      tags: [Machines]
      operationId: listMachines
      summary: List machines
      parameters:
        - { name: machine_class, in: query, schema: { type: string } }
        - { name: plant_id, in: query, schema: { type: string, format: uuid } }
        - { name: status, in: query, schema: { type: string } }
        - { name: process_type_code, in: query, schema: { type: string }, description: "Machines capable of this process" }
      responses:
        "200": { description: Machine list }

  /machines/{machine_id}:
    get:
      tags: [Machines]
      operationId: getMachine
      summary: Get machine detail
      parameters:
        - { name: machine_id, in: path, required: true, schema: { type: string, format: uuid } }
        - name: include
          in: query
          schema:
            type: array
            items:
              type: string
              enum: [oee_summary, maintenance, tools, energy_profile]
      responses:
        "200": { description: Machine detail }

  /machines/{machine_id}/oee:
    get:
      tags: [OEE]
      operationId: getMachineOEE
      summary: Get OEE records for machine
      parameters:
        - { name: machine_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: from_date, in: query, schema: { type: string, format: date } }
        - { name: to_date, in: query, schema: { type: string, format: date } }
        - { name: aggregate_by, in: query, schema: { type: string, enum: [day, week, month] }, default: day }
      responses:
        "200": { description: OEE data }

    post:
      tags: [OEE]
      operationId: recordOEE
      summary: Record OEE data for a shift
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/OEERecordInput"
      responses:
        "201": { description: OEE recorded }

  /machines/{machine_id}/downtime:
    get:
      tags: [OEE]
      operationId: getMachineDowntime
      summary: Get downtime records
      parameters:
        - { name: machine_id, in: path, required: true, schema: { type: string, format: uuid } }
        - { name: from_date, in: query, schema: { type: string, format: date } }
        - { name: to_date, in: query, schema: { type: string, format: date } }
        - { name: category, in: query, schema: { type: string } }
      responses:
        "200": { description: Downtime records }

    post:
      tags: [OEE]
      operationId: recordDowntime
      summary: Record a downtime event
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/DowntimeInput"
      responses:
        "201": { description: Downtime recorded }

  /machines/{machine_id}/energy-profile:
    get:
      tags: [Machines]
      operationId: getMachineEnergyProfile
      summary: Get machine energy consumption profile
      responses:
        "200": { description: Energy profile }

  # CAPACITY
  /capacity/slots:
    get:
      tags: [Capacity]
      operationId: getCapacitySlots
      summary: Get capacity slots for resources
      parameters:
        - { name: resource_ids, in: query, schema: { type: array, items: { type: string, format: uuid } }, style: form }
        - { name: from_date, in: query, required: true, schema: { type: string, format: date } }
        - { name: to_date, in: query, required: true, schema: { type: string, format: date } }
        - { name: include_oee_adjustment, in: query, schema: { type: boolean, default: true } }
      responses:
        "200": { description: Capacity slots }

  /capacity/bottlenecks:
    get:
      tags: [Capacity]
      operationId: detectBottlenecks
      summary: Detect bottleneck resources
      parameters:
        - { name: plant_id, in: query, required: true, schema: { type: string, format: uuid } }
        - { name: from_date, in: query, required: true, schema: { type: string, format: date } }
        - { name: to_date, in: query, required: true, schema: { type: string, format: date } }
        - { name: threshold_pct, in: query, schema: { type: number, default: 85 } }
      responses:
        "200":
          description: Bottleneck alerts
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/BottleneckAlert"

  /capacity/utilization:
    get:
      tags: [Capacity]
      operationId: getCapacityUtilization
      summary: Get capacity utilization report
      parameters:
        - { name: plant_id, in: query, required: true, schema: { type: string, format: uuid } }
        - { name: from_date, in: query, required: true, schema: { type: string, format: date } }
        - { name: to_date, in: query, required: true, schema: { type: string, format: date } }
      responses:
        "200": { description: Utilization report }

  # SEARCH
  /search:
    get:
      tags: [Search]
      operationId: searchProcesses
      summary: Full-text search across processes
      parameters:
        - { name: q, in: query, required: true, schema: { type: string } }
        - { name: process_class, in: query, schema: { type: array, items: { type: string } } }
        - { name: page, in: query, schema: { type: integer, default: 1 } }
        - { name: page_size, in: query, schema: { type: integer, default: 20 } }
      responses:
        "200": { description: Search results }

  /search/semantic:
    post:
      tags: [Search, AI]
      operationId: semanticSearchProcesses
      summary: Natural language search for processes
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [query]
              properties:
                query:        { type: string, example: "process for cutting thin stainless sheet with high edge quality" }
                top_k:        { type: integer, default: 10 }
                class_filter: { type: array, items: { type: string } }
      responses:
        "200": { description: Semantic search results }

  # AI endpoints
  /ai/process-recommendation:
    post:
      tags: [AI]
      operationId: recommendProcess
      summary: AI-powered process recommendation for part features
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/ProcessRecommendationRequest"
      responses:
        "200":
          description: Recommended processes ranked by suitability
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ProcessRecommendationResponse"

  /ai/cycle-time-prediction:
    post:
      tags: [AI]
      operationId: predictCycleTime
      summary: ML-based cycle time prediction
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/CycleTimePredictionRequest"
      responses:
        "200":
          description: Predicted cycle time with confidence
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/CycleTimePredictionResponse"

  /ai/oee-anomaly:
    post:
      tags: [AI]
      operationId: detectOEEAnomaly
      summary: Detect anomalies in OEE data stream
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [machine_id]
              properties:
                machine_id: { type: string, format: uuid }
                lookback_days: { type: integer, default: 30 }
      responses:
        "200": { description: Anomaly detection result }

components:
  schemas:

    CostEstimateRequest:
      type: object
      required: [process_type_code, batch_size]
      properties:
        process_type_code: { type: string }
        machine_id:        { type: string, format: uuid }
        batch_size:        { type: integer, minimum: 1 }
        part_weight_kg:    { type: number }
        material_id:       { type: string, format: uuid }
        cycle_time_override_sec: { type: number }
        shift_datetime:    { type: string, format: date-time }

    CostEstimateResponse:
      type: object
      properties:
        process_type_code:    { type: string }
        batch_size:           { type: integer }
        setup_eur:            { type: number }
        runtime_eur:          { type: number }
        energy_eur:           { type: number }
        maintenance_eur:      { type: number }
        scrap_eur:            { type: number }
        total_eur:            { type: number }
        cycle_time_sec:       { type: number }
        cost_per_min:         { type: number }
        cost_breakdown_pct:
          type: object
          properties:
            setup:       { type: number }
            runtime:     { type: number }
            energy:      { type: number }
            maintenance: { type: number }
            scrap:       { type: number }

    OEERecordInput:
      type: object
      required: [shift_date, shift_number, planned_production_time_min, total_parts_produced, good_parts]
      properties:
        shift_date:                   { type: string, format: date }
        shift_number:                 { type: integer, enum: [1, 2, 3] }
        planned_production_time_min:  { type: integer }
        unplanned_downtime_min:       { type: integer, default: 0 }
        planned_downtime_min:         { type: integer, default: 0 }
        ideal_cycle_time_sec:         { type: number }
        total_parts_produced:         { type: integer }
        good_parts:                   { type: integer }
        scrap_parts:                  { type: integer, default: 0 }
        rework_parts:                 { type: integer, default: 0 }
        energy_kwh:                   { type: number }
        notes:                        { type: string }
        source:                       { type: string, enum: [MANUAL, MES, IOT_AUTO], default: MANUAL }

    DowntimeInput:
      type: object
      required: [start_at, downtime_category, loss_type]
      properties:
        start_at:           { type: string, format: date-time }
        end_at:             { type: string, format: date-time }
        downtime_category:  { type: string }
        loss_type:          { type: string }
        description:        { type: string }
        repair_action:      { type: string }

    BottleneckAlert:
      type: object
      properties:
        resource_id:          { type: string, format: uuid }
        resource_code:        { type: string }
        alert_date:           { type: string, format: date }
        avg_utilization_pct:  { type: number }
        severity:             { type: string, enum: [LOW, MEDIUM, HIGH, CRITICAL] }
        recommendation:       { type: string }
        consecutive_days:     { type: integer }

    ProcessRecommendationRequest:
      type: object
      required: [material_class, process_requirements]
      properties:
        material_class:      { type: string }
        material_grade:      { type: string }
        material_thickness_mm: { type: number }
        part_features:       { type: array, items: { type: string } }
        process_requirements:
          type: object
          properties:
            surface_roughness_max_ra: { type: number }
            dimensional_tolerance_mm: { type: number }
            batch_size:               { type: integer }
            required_certifications:  { type: array, items: { type: string } }

    ProcessRecommendationResponse:
      type: object
      properties:
        recommendations:
          type: array
          items:
            type: object
            properties:
              process_type_code:  { type: string }
              process_name:       { type: string }
              suitability_score:  { type: number }
              go_nogo:            { type: string }
              estimated_cycle_sec:{ type: number }
              estimated_cost_eur: { type: number }
              notes:              { type: string }

    CycleTimePredictionRequest:
      type: object
      required: [process_type_code, part_features]
      properties:
        process_type_code:  { type: string }
        machine_id:         { type: string, format: uuid }
        material_class:     { type: string }
        part_features:
          type: object
          properties:
            bounding_box_x_mm: { type: number }
            bounding_box_y_mm: { type: number }
            bounding_box_z_mm: { type: number }
            weight_kg:         { type: number }
            cut_length_mm:     { type: number }
            bend_count:        { type: integer }
            hole_count:        { type: integer }
            weld_length_mm:    { type: number }
            surface_area_m2:   { type: number }

    CycleTimePredictionResponse:
      type: object
      properties:
        process_type_code:    { type: string }
        predicted_cycle_sec:  { type: number }
        confidence_interval:
          type: object
          properties:
            lower_90: { type: number }
            upper_90: { type: number }
        model_name:           { type: string }
        mape_pct:             { type: number }
        feature_importance:   { type: object }
```

---

## 19. Event Architecture

### Topiki Kafka

| Topik | Partycje | Retencja | Opis |
|-------|----------|----------|------|
| `mpe.process.created` | 6 | 30 dni | Nowy proces produkcyjny |
| `mpe.process.updated` | 6 | 30 dni | Zmiana definicji procesu |
| `mpe.process.deactivated` | 3 | 90 dni | Wycofanie procesu |
| `mpe.machine.registered` | 3 | 30 dni | Nowa maszyna |
| `mpe.machine.status_changed` | 6 | 30 dni | Zmiana statusu maszyny |
| `mpe.machine.downtime_started` | 12 | 14 dni | Początek przestoju |
| `mpe.machine.downtime_ended` | 12 | 14 dni | Koniec przestoju |
| `mpe.oee.recorded` | 12 | 30 dni | Zapis OEE (per zmiana) |
| `mpe.oee.anomaly_detected` | 6 | 90 dni | Anomalia OEE |
| `mpe.tool.life_expired` | 6 | 30 dni | Zużycie narzędzia |
| `mpe.tool.change_scheduled` | 3 | 30 dni | Zaplanowana wymiana |
| `mpe.cost.model_updated` | 6 | 30 dni | Zmiana modelu kosztowego |
| `mpe.capacity.slot_updated` | 12 | 14 dni | Zmiana dostępności zasobu |
| `mpe.capacity.bottleneck_detected` | 3 | 90 dni | Wykrycie wąskiego gardła |
| `mpe.setup.completed` | 12 | 14 dni | Zakończenie nastawu |
| `mpe.operation.completed` | 24 | 14 dni | Zakończenie operacji |
| `mpe.scrap.recorded` | 12 | 30 dni | Rejestracja złomu |

### Schematy zdarzeń

#### mpe.oee.recorded

```json
{
  "type": "record",
  "name": "OEERecordedEvent",
  "namespace": "com.industrial.mpe.events",
  "fields": [
    { "name": "event_id",           "type": "string" },
    { "name": "event_type",         "type": "string", "default": "oee.recorded" },
    { "name": "occurred_at",        "type": "string" },
    { "name": "trace_id",           "type": "string" },
    { "name": "machine_id",         "type": "string" },
    { "name": "machine_code",       "type": "string" },
    { "name": "shift_date",         "type": "string" },
    { "name": "shift_number",       "type": "int" },
    { "name": "availability_pct",   "type": "double" },
    { "name": "performance_pct",    "type": "double" },
    { "name": "quality_pct",        "type": "double" },
    { "name": "oee_pct",            "type": "double" },
    { "name": "good_parts",         "type": "int" },
    { "name": "scrap_parts",        "type": "int" },
    { "name": "unplanned_downtime_min", "type": "int" },
    { "name": "energy_kwh",         "type": ["null", "double"] },
    { "name": "source",             "type": "string" }
  ]
}
```

#### mpe.machine.downtime_started

```json
{
  "type": "record",
  "name": "MachineDowntimeStartedEvent",
  "namespace": "com.industrial.mpe.events",
  "fields": [
    { "name": "event_id",           "type": "string" },
    { "name": "event_type",         "type": "string", "default": "machine.downtime_started" },
    { "name": "occurred_at",        "type": "string" },
    { "name": "machine_id",         "type": "string" },
    { "name": "machine_code",       "type": "string" },
    { "name": "downtime_id",        "type": "string" },
    { "name": "downtime_category",  "type": "string" },
    { "name": "loss_type",          "type": "string" },
    { "name": "estimated_duration_min", "type": ["null", "int"] },
    { "name": "plant_id",           "type": "string" },
    { "name": "resource_code",      "type": "string" }
  ]
}
```

#### mpe.cost.model_updated

```json
{
  "type": "record",
  "name": "CostModelUpdatedEvent",
  "namespace": "com.industrial.mpe.events",
  "fields": [
    { "name": "event_id",           "type": "string" },
    { "name": "event_type",         "type": "string", "default": "cost.model_updated" },
    { "name": "occurred_at",        "type": "string" },
    { "name": "process_type_code",  "type": "string" },
    { "name": "machine_id",         "type": ["null", "string"] },
    { "name": "cost_model_type",    "type": "string" },
    { "name": "changed_fields",     "type": { "type": "array", "items": "string" } },
    { "name": "effective_from",     "type": "string" },
    { "name": "changed_by",         "type": "string" }
  ]
}
```

#### mpe.capacity.bottleneck_detected

```json
{
  "type": "record",
  "name": "BottleneckDetectedEvent",
  "namespace": "com.industrial.mpe.events",
  "fields": [
    { "name": "event_id",           "type": "string" },
    { "name": "event_type",         "type": "string", "default": "capacity.bottleneck_detected" },
    { "name": "occurred_at",        "type": "string" },
    { "name": "resource_id",        "type": "string" },
    { "name": "resource_code",      "type": "string" },
    { "name": "avg_utilization_pct","type": "double" },
    { "name": "consecutive_overloaded_days", "type": "int" },
    { "name": "severity",           "type": "string" },
    { "name": "first_bottleneck_date", "type": "string" },
    { "name": "recommendation",     "type": "string" }
  ]
}
```

#### mpe.operation.completed

```json
{
  "type": "record",
  "name": "OperationCompletedEvent",
  "namespace": "com.industrial.mpe.events",
  "fields": [
    { "name": "event_id",           "type": "string" },
    { "name": "event_type",         "type": "string", "default": "operation.completed" },
    { "name": "occurred_at",        "type": "string" },
    { "name": "production_order_id","type": "string" },
    { "name": "operation_id",       "type": "string" },
    { "name": "process_type_code",  "type": "string" },
    { "name": "machine_id",         "type": "string" },
    { "name": "operator_id",        "type": "string" },
    { "name": "quantity_produced",  "type": "int" },
    { "name": "scrap_quantity",     "type": "int" },
    { "name": "actual_setup_min",   "type": "double" },
    { "name": "actual_runtime_min", "type": "double" },
    { "name": "actual_cost_eur",    "type": "double" }
  ]
}
```

### Konsumenci zdarzeń

| Zdarzenie | Konsument | Akcja |
|-----------|-----------|-------|
| process.created/updated | Search Service | Reindeksuj |
| process.created/updated | Embedding Service | Generuj embedding |
| process.updated | Cost Calc Engine | Odśwież modele kosztowe |
| machine.downtime_started | Scheduler | Blokuj capacity slots |
| machine.downtime_ended | Scheduler | Odblokuj capacity slots |
| oee.recorded | OEE Dashboard | Aktualizuj wykresy |
| oee.recorded | Cost Calc | Przelicz współczynnik OEE |
| oee.anomaly_detected | Alert Service | Powiadom kierownika |
| tool.life_expired | Procurement | Utwórz zapotrzebowanie |
| capacity.bottleneck_detected | Planning | Alert planisty |
| cost.model_updated | Cost Calc | Aktualizuj kalkulacje |
| operation.completed | Quality | Aktualizuj statystyki |
| scrap.recorded | Quality / MES | Dashboard jakości |

---

## 21. AI Layer

### Architektura AI

```
AILayer (MPE)
├── ProcessEmbeddingService      -- generowanie wektorów dla procesów
├── SemanticProcessSearch        -- wyszukiwanie semantyczne
├── ProcessRecommendationEngine  -- rekomendacja procesu per część
├── CycleTimePredictionModel     -- przewidywanie czasu cyklu (ML)
├── OEEAnomalyDetector           -- wykrywanie anomalii OEE
├── ToolLifePredictionModel      -- predykcja zużycia narzędzi
└── ProcessCostNormalizer        -- normalizacja kosztów dla AI/RAG
```

### Process Embedding Service

```python
class ProcessEmbeddingService:
    """
    Generates vector embeddings for manufacturing processes.
    Embedding captures: process type, parameters, capabilities,
    material compatibility, quality outputs.
    """

    MODEL = "text-embedding-3-small"
    DIMENSION = 1536

    def build_text(self, process: ManufacturingProcess) -> str:
        parts = [
            f"Process: {process.process_name}",
            f"Type: {process.process_type_code}",
            f"Class: {process.process_class}",
        ]

        # Key parameters
        for p in process.parameters:
            if p.param_key in SIGNIFICANT_PARAMS:
                parts.append(f"{p.param_key}: {p.param_value} {p.unit or ''}")

        # Material compatibility
        compat = [c for c in process.compatibility if c.compatibility_level in ('OPTIMAL', 'ACCEPTABLE')]
        if compat:
            mats = ", ".join(f"{c.material_class} {c.material_grade or ''}".strip() for c in compat[:5])
            parts.append(f"Compatible with: {mats}")

        # Quality outputs
        if process.parameters.get('surface_roughness_ra_um'):
            parts.append(f"Surface roughness Ra: {process.parameters['surface_roughness_ra_um']} µm")
        if process.parameters.get('dimensional_accuracy_mm'):
            parts.append(f"Dimensional accuracy: ±{process.parameters['dimensional_accuracy_mm']} mm")

        if process.description:
            parts.append(process.description[:400])

        return ". ".join(parts)
```

### Process Recommendation Engine

```python
class ProcessRecommendationEngine:
    """
    Recommends processes for given part features using:
    1. Rule-based filtering (hard constraints: material, thickness)
    2. Semantic similarity (embedding-based)
    3. Cost scoring
    4. Quality scoring
    """

    def recommend(self, request: ProcessRecommendationRequest) -> list[ProcessRecommendation]:
        # Step 1: Hard constraint filter
        candidates = self.filter_by_constraints(
            material_class=request.material_class,
            material_grade=request.material_grade,
            thickness_mm=request.material_thickness_mm,
        )

        # Step 2: Feature-based scoring
        scored = []
        for process in candidates:
            score = self._score_process(process, request)
            scored.append((process, score))

        # Step 3: Sort and return top N
        scored.sort(key=lambda x: x[1].total, reverse=True)

        return [
            ProcessRecommendation(
                process_type_code=proc.process_type_code,
                process_name=proc.process_name,
                suitability_score=score.total,
                go_nogo=score.go_nogo,
                estimated_cycle_sec=self._estimate_cycle(proc, request),
                estimated_cost_eur=self._estimate_cost(proc, request),
                notes=score.notes,
            )
            for proc, score in scored[:10]
        ]

    def _score_process(self, process, request) -> ProcessScore:
        scores = {
            'material_compatibility': self._score_material(process, request),
            'quality':                self._score_quality(process, request),
            'batch_efficiency':       self._score_batch(process, request),
            'feature_feasibility':    self._score_features(process, request),
        }
        total = (
            scores['material_compatibility'] * 0.35 +
            scores['quality']                * 0.30 +
            scores['batch_efficiency']       * 0.20 +
            scores['feature_feasibility']    * 0.15
        )
        go_nogo = 'GO' if total >= 70 else ('CONDITIONAL' if total >= 50 else 'NOGO')
        return ProcessScore(total=total, breakdown=scores, go_nogo=go_nogo, notes="")
```

---

## 22. ML Features

### Zestaw cech dla modeli ML

#### Feature Store — Cycle Time Prediction

```python
CYCLE_TIME_FEATURES = {
    # Part geometry
    'bounding_box_x_mm':    'NUMERIC',
    'bounding_box_y_mm':    'NUMERIC',
    'bounding_box_z_mm':    'NUMERIC',
    'bounding_box_volume':  'NUMERIC',   # derived: x*y*z
    'weight_kg':            'NUMERIC',
    'surface_area_m2':      'NUMERIC',

    # Process-specific features
    'cut_length_mm':        'NUMERIC',   # CUT processes
    'cut_perimeter_mm':     'NUMERIC',
    'pierce_count':         'INTEGER',   # number of piercing points
    'nesting_efficiency':   'NUMERIC',

    'milling_volume_cm3':   'NUMERIC',   # MAC processes
    'drilling_count':       'INTEGER',
    'thread_count':         'INTEGER',
    'surface_finish_target':'CATEGORICAL',

    'bend_count':           'INTEGER',   # FOR processes
    'bend_complexity':      'CATEGORICAL',

    'weld_length_mm':       'NUMERIC',   # JOI processes
    'weld_joint_count':     'INTEGER',

    'surface_area_coated_m2':'NUMERIC',  # FIN processes

    # Material features
    'material_class':       'CATEGORICAL',
    'material_grade':       'CATEGORICAL',
    'material_thickness_mm':'NUMERIC',
    'material_hardness_hb': 'NUMERIC',
    'material_tensile_mpa': 'NUMERIC',

    # Machine features
    'machine_class':        'CATEGORICAL',
    'machine_axis_count':   'INTEGER',
    'machine_spindle_rpm':  'NUMERIC',
    'machine_age_years':    'NUMERIC',

    # Operator features
    'operator_skill_level': 'CATEGORICAL',
    'operator_experience_h':'NUMERIC',    # hours on this process type

    # Historical
    'historical_avg_cycle_sec': 'NUMERIC',
    'historical_std_cycle_sec': 'NUMERIC',
    'similar_part_cycle_sec':   'NUMERIC',  # from embedding similarity
}
```

#### Feature Store — OEE Anomaly Detection

```python
OEE_ANOMALY_FEATURES = {
    # Time series OEE
    'oee_pct_rolling_7d':    'NUMERIC',
    'oee_pct_rolling_30d':   'NUMERIC',
    'oee_pct_std_30d':       'NUMERIC',
    'availability_rolling':  'NUMERIC',
    'performance_rolling':   'NUMERIC',
    'quality_rolling':       'NUMERIC',

    # Downtime patterns
    'downtime_events_7d':    'INTEGER',
    'avg_downtime_duration': 'NUMERIC',
    'breakdown_count_30d':   'INTEGER',
    'mtbf_actual_h':         'NUMERIC',
    'mttr_actual_h':         'NUMERIC',

    # Maintenance
    'days_since_last_pm':    'INTEGER',
    'overdue_pm_count':      'INTEGER',
    'tool_changes_7d':       'INTEGER',

    # Environmental
    'shift_number':          'CATEGORICAL',
    'day_of_week':           'CATEGORICAL',
    'ambient_temp_c':        'NUMERIC',    # if IoT sensor available

    # Machine health
    'vibration_rms':         'NUMERIC',    # from IoT (if available)
    'spindle_load_avg_pct':  'NUMERIC',
    'coolant_temp_c':        'NUMERIC',
}
```

#### Feature Store — Tool Life Prediction

```python
TOOL_LIFE_FEATURES = {
    'tool_category':         'CATEGORICAL',
    'tool_material':         'CATEGORICAL',
    'tool_coating':          'CATEGORICAL',
    'cutting_minutes_used':  'NUMERIC',
    'cutting_speed_m_min':   'NUMERIC',
    'feed_per_tooth_mm':     'NUMERIC',
    'axial_depth_mm':        'NUMERIC',
    'radial_depth_mm':       'NUMERIC',
    'material_hardness_hb':  'NUMERIC',
    'material_class':        'CATEGORICAL',
    'coolant_used':          'BOOLEAN',
    'vibration_rms':         'NUMERIC',    # IoT
    'spindle_load_pct':      'NUMERIC',    # IoT
    'surface_quality_ok':    'BOOLEAN',    # last inspection
    'previous_changes_count':'INTEGER',
    'resharpening_count':    'INTEGER',
}
```

### Modele ML — przegląd

| Model | Algorytm | Target | Input features | Accuracy target |
|-------|---------|--------|----------------|----------------|
| Cycle Time Prediction | XGBoost Regressor | cycle_time_sec | Part geometry + process + material | MAPE < 15% |
| OEE Anomaly Detection | Isolation Forest + LSTM | anomaly_score | OEE time series + downtime | F1 > 0.80 |
| Tool Life Prediction | Random Forest Regressor | remaining_life_min | Tool usage + machining params | MAPE < 20% |
| Setup Time Estimation | Gradient Boosting | setup_time_min | Part complexity + process | MAPE < 20% |
| Bottleneck Prediction | Time Series (Prophet) | utilization_pct (7d ahead) | Capacity slots + order book | MAPE < 10% |
| Process Cost Normalization | LightGBM | cost_eur | All cost factors | R² > 0.92 |

### Model training pipeline

```python
class CycleTimePredictionPipeline:
    """
    Training pipeline for cycle time prediction.
    Data source: mpe.operation.completed Kafka topic (historical).
    Retraining: weekly (or on MAPE drift > 5%).
    """

    def prepare_features(self, operations: list[OperationRecord]) -> pd.DataFrame:
        df = pd.DataFrame([op.to_dict() for op in operations])

        # Feature engineering
        df['bounding_box_volume'] = df['bbox_x'] * df['bbox_y'] * df['bbox_z']
        df['cut_density'] = df['cut_length_mm'] / (df['surface_area_m2'] * 1e6)
        df['specific_material_removal'] = (
            df['milling_volume_cm3'] /
            (df['cutting_speed_m_min'] * df['feed_per_tooth_mm'])
        )

        # Encode categoricals
        for col in ['material_class', 'machine_class', 'operator_skill_level']:
            df[col] = LabelEncoder().fit_transform(df[col].fillna('UNKNOWN'))

        return df[CYCLE_TIME_FEATURES.keys()]

    def train(self, df: pd.DataFrame, y: pd.Series) -> CycleTimeModel:
        X_train, X_val, y_train, y_val = train_test_split(df, y, test_size=0.2)

        model = XGBRegressor(
            n_estimators=500,
            max_depth=6,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            objective='reg:squarederror',
        )
        model.fit(X_train, y_train,
                  eval_set=[(X_val, y_val)],
                  early_stopping_rounds=50,
                  verbose=False)

        mape = mean_absolute_percentage_error(y_val, model.predict(X_val))
        return CycleTimeModel(model=model, mape=mape, trained_at=datetime.utcnow())
```
