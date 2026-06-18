# Material Intelligence Engine — Events, Validation, Search, AI

## 16. Event Model — Kafka Events

### Topiki Kafka

| Topik | Partycje | Retencja | Opis |
|-------|----------|----------|------|
| `mie.material.created` | 6 | 30 dni | Nowy materiał |
| `mie.material.updated` | 6 | 30 dni | Zmiana danych materiału |
| `mie.material.deactivated` | 3 | 90 dni | Wycofanie materiału |
| `mie.material.status_changed` | 3 | 30 dni | Zmiana statusu |
| `mie.price.updated` | 12 | 7 dni | Zmiana ceny |
| `mie.price.anomaly_detected` | 3 | 30 dni | Anomalia cenowa |
| `mie.substitution.created` | 3 | 30 dni | Nowy zamiennik |
| `mie.substitution.updated` | 3 | 30 dni | Zmiana reguły zamiany |
| `mie.supplier_mapping.changed` | 6 | 30 dni | Zmiana powiązania dostawca-materiał |
| `mie.compatibility.updated` | 3 | 30 dni | Zmiana kompatybilności proces-materiał |
| `mie.embedding.regenerated` | 3 | 7 dni | Nowy embedding (do indeksowania) |

### Schematy zdarzeń (Avro)

#### mie.material.created

```json
{
  "type": "record",
  "name": "MaterialCreatedEvent",
  "namespace": "com.industrial.mie.events",
  "fields": [
    { "name": "event_id",       "type": "string" },
    { "name": "event_type",     "type": "string", "default": "material.created" },
    { "name": "event_version",  "type": "string", "default": "1.0" },
    { "name": "occurred_at",    "type": "string" },
    { "name": "trace_id",       "type": "string" },
    { "name": "material_id",    "type": "string" },
    { "name": "material_code",  "type": "string" },
    { "name": "material_name",  "type": "string" },
    { "name": "material_class", "type": "string" },
    { "name": "grade",          "type": ["null", "string"], "default": null },
    { "name": "category_code",  "type": "string" },
    { "name": "created_by",     "type": "string" }
  ]
}
```

#### mie.material.updated

```json
{
  "type": "record",
  "name": "MaterialUpdatedEvent",
  "namespace": "com.industrial.mie.events",
  "fields": [
    { "name": "event_id",        "type": "string" },
    { "name": "event_type",      "type": "string", "default": "material.updated" },
    { "name": "event_version",   "type": "string", "default": "1.0" },
    { "name": "occurred_at",     "type": "string" },
    { "name": "trace_id",        "type": "string" },
    { "name": "material_id",     "type": "string" },
    { "name": "material_code",   "type": "string" },
    { "name": "changed_fields",  "type": { "type": "array", "items": "string" } },
    { "name": "previous_version","type": "int" },
    { "name": "new_version",     "type": "int" },
    { "name": "changed_by",      "type": "string" },
    { "name": "requires_embedding_refresh", "type": "boolean", "default": false }
  ]
}
```

#### mie.price.updated

```json
{
  "type": "record",
  "name": "PriceUpdatedEvent",
  "namespace": "com.industrial.mie.events",
  "fields": [
    { "name": "event_id",         "type": "string" },
    { "name": "event_type",       "type": "string", "default": "price.updated" },
    { "name": "occurred_at",      "type": "string" },
    { "name": "trace_id",         "type": "string" },
    { "name": "material_id",      "type": "string" },
    { "name": "material_code",    "type": "string" },
    { "name": "price_type",       "type": "string" },
    { "name": "previous_price",   "type": "double" },
    { "name": "new_price",        "type": "double" },
    { "name": "currency",         "type": "string" },
    { "name": "unit",             "type": "string" },
    { "name": "change_pct",       "type": "double" },
    { "name": "price_date",       "type": "string" },
    { "name": "source_code",      "type": "string" },
    { "name": "region",           "type": "string" }
  ]
}
```

#### mie.price.anomaly_detected

```json
{
  "type": "record",
  "name": "PriceAnomalyEvent",
  "namespace": "com.industrial.mie.events",
  "fields": [
    { "name": "event_id",       "type": "string" },
    { "name": "event_type",     "type": "string", "default": "price.anomaly_detected" },
    { "name": "occurred_at",    "type": "string" },
    { "name": "material_id",    "type": "string" },
    { "name": "anomaly_type",   "type": "string" },
    { "name": "submitted_price","type": "double" },
    { "name": "expected_range_low",  "type": "double" },
    { "name": "expected_range_high", "type": "double" },
    { "name": "change_pct",     "type": "double" },
    { "name": "source_code",    "type": "string" },
    { "name": "requires_manual_review", "type": "boolean" }
  ]
}
```

### Konsumenci zdarzeń

| Zdarzenie | Konsument | Akcja |
|-----------|-----------|-------|
| material.created | Search Service | Reindeksuj materiał |
| material.created | ERP Sync Service | Utwórz w ERP |
| material.created | Embedding Service | Generuj embedding |
| material.updated | Search Service | Aktualizuj indeks |
| material.updated | Embedding Service | Regeneruj embedding (jeśli flaga) |
| material.updated | ERP Sync Service | Synchronizuj do ERP |
| material.deactivated | RFQ Engine | Zablokuj użycie w nowych RFQ |
| material.deactivated | Cost Calc | Oznacz kalkulacje jako przestarzałe |
| price.updated | Cost Calc | Przelicz bazowe koszty |
| price.updated | Forecasting | Aktualizuj model prognozy |
| price.updated | Notification Service | Alert jeśli zmiana > próg |
| price.anomaly_detected | Alert Service | Wyślij powiadomienie do zespołu |
| supplier_mapping.changed | Supplier Intelligence | Aktualizuj profil dostawcy |
| supplier_mapping.changed | Risk Assessor | Przelicz ryzyko dostaw |

---

## 17. Validation Rules — Pełny zestaw

### Walidacja materiału (CreateMaterial / UpdateMaterial)

```python
class MaterialValidator:

    RULES = [
        # Pole: material_code
        Rule('material_code', required=True, max_length=50,
             pattern=r'^[A-Z0-9][A-Z0-9\-\_\.]{2,49}$',
             message="Code must be uppercase alphanumeric with - _ . separators"),

        # Pole: material_name
        Rule('material_name', required=True, min_length=3, max_length=200),

        # Pole: material_class
        Rule('material_class', required=True,
             allowed_values=['METAL','POLYMER','WOOD','PACKAGING','COMPOSITE','SPECIAL']),

        # Pole: category_id
        Rule('category_id', required=True, type='uuid',
             foreign_key='material_categories.category_id',
             must_be_leaf=True,
             message="Category must be a leaf node in taxonomy"),

        # Pole: grade
        Rule('grade', required=False, max_length=100),

        # Pole: customs_tariff_code
        Rule('customs_tariff_code', required=False,
             pattern=r'^\d{8}$|^\d{10}$',
             message="Must be 8 or 10 digit CN/HS code"),

        # Biznesowe: replaced_by_id wymagane gdy status=REPLACED
        CrossFieldRule(
            when={'status': 'REPLACED'},
            then={'replaced_by_id': 'required'},
            message="replaced_by_id required when status is REPLACED"
        ),

        # Biznesowe: nie można zastąpić samego siebie
        CrossFieldRule(
            check=lambda m: m.replaced_by_id != m.material_id,
            message="Material cannot replace itself"
        ),
    ]
```

### Walidacja właściwości fizycznych

```python
PROPERTY_RULES = {
    'density_kg_m3': {
        'type': 'numeric',
        'min': 0.001,
        'max': 25000,       # Osmium = 22590 kg/m³
        'class_ranges': {
            'METAL':      (1000, 23000),
            'POLYMER':    (800, 2200),
            'WOOD':       (100, 1200),
            'PACKAGING':  (10, 1500),
            'COMPOSITE':  (1200, 3000),
            'SPECIAL':    (5, 3000),
        }
    },
    'tensile_strength_mpa': {
        'type': 'numeric',
        'min': 0.1,
        'max': 6000,        # Ultra-high strength steel
        'class_ranges': {
            'METAL':    (50, 6000),
            'POLYMER':  (10, 250),
            'COMPOSITE':(100, 3000),
        }
    },
    'melting_point_c': {
        'type': 'numeric',
        'min': -50,
        'max': 4000,        # Tungsten 3422°C
    },
    'thermal_conductivity_w_mk': {
        'type': 'numeric',
        'min': 0.01,
        'max': 450,         # Silver 429 W/m·K
    }
}
```

### Walidacja cen

```python
PRICE_VALIDATION_RULES = {
    # Absolutne limity per klasa materiału (EUR/kg)
    'price_bounds_eur_kg': {
        'METAL_CARBON':     {'min': 0.30,  'max': 5.00},
        'METAL_STAINLESS':  {'min': 1.00,  'max': 20.00},
        'METAL_ALUMINUM':   {'min': 0.80,  'max': 8.00},
        'METAL_COPPER':     {'min': 4.00,  'max': 30.00},
        'POLYMER_COMMODITY':{'min': 0.50,  'max': 5.00},
        'POLYMER_ENGINEERING':{'min':2.00, 'max': 30.00},
        'WOOD_BOARD':       {'min': 0.20,  'max': 3.00},
        'PACKAGING':        {'min': 0.30,  'max': 5.00},
        'COMPOSITE_GF':     {'min': 1.50,  'max': 20.00},
        'COMPOSITE_CF':     {'min': 20.00, 'max': 300.00},
    },
    # Max. zmiana dzienna
    'max_daily_change_pct': {
        'METAL':     8.0,
        'POLYMER':   5.0,
        'WOOD':      3.0,
        'PACKAGING': 3.0,
        'COMPOSITE': 5.0,
    },
    # Wymagane pola
    'required_fields': ['material_id', 'source_id', 'price_type',
                        'price_date', 'price_value', 'currency', 'unit']
}
```

### Walidacja zamienników

```python
SUBSTITUTION_RULES = [
    # Nie można zastąpić materiałem tej samej klasy z niższymi właściwościami
    Rule("Rm_candidate >= Rm_source * 0.95",
         message="Substitute must have Rm within 5% of source"),

    # FORBIDDEN combination check
    Rule("check_process_compatibility(substitute, context.processes) != FORBIDDEN",
         message="Substitute is FORBIDDEN for required processes"),

    # Regulatorne: RoHS
    Rule("if source.regulatory_flags.rohs_required then candidate.rohs_compliant must be TRUE",
         message="Source requires RoHS compliant substitute"),

    # Self-reference
    Rule("source_material_id != substitute_material_id",
         message="Material cannot be its own substitute"),

    # Score range
    Rule("0 <= compatibility_score <= 100"),
]
```

### Walidacja kompatybilności proces-materiał

```python
COMPATIBILITY_RULES = [
    Rule("process_code in ProcessRegistry.VALID_CODES",
         message="Unknown process code"),

    Rule("speed_factor > 0 and speed_factor <= 5.0",
         message="Speed factor must be between 0 and 5.0"),

    Rule("if min_thickness_mm then max_thickness_mm must be >= min_thickness_mm"),

    CrossFieldRule(
        when={'compatibility_level': 'OPTIMAL'},
        then={'speed_factor': lambda v: 0.8 <= v <= 1.5},
        message="OPTIMAL compatibility should have speed_factor 0.8-1.5"
    ),

    CrossFieldRule(
        when={'compatibility_level': 'FORBIDDEN'},
        then={'speed_factor': None},
        message="FORBIDDEN compatibility should not have speed_factor"
    ),
]
```

---

## 18. Search Engine

### Architektura wyszukiwania

```
SearchEngine
├── FullTextSearch        -- PostgreSQL tsvector (primary)
├── TrigramSearch         -- pg_trgm (typos, partial match)
├── FilterEngine          -- structured attribute filters
├── FacetBuilder          -- facets (class, grade, form_factor)
└── RankingEngine         -- scoring (ts_rank + recency + price_freshness)
```

### Zapytanie wyszukiwania pełnotekstowego (SQL)

```sql
CREATE OR REPLACE FUNCTION search_materials(
    p_query      TEXT,
    p_class      material_class_enum[] DEFAULT NULL,
    p_status     material_status_enum DEFAULT 'ACTIVE',
    p_page       INT DEFAULT 1,
    p_page_size  INT DEFAULT 20
)
RETURNS TABLE (
    material_id       UUID,
    material_code     VARCHAR,
    material_name     VARCHAR,
    material_class    material_class_enum,
    grade             VARCHAR,
    density_kg_m3     NUMERIC,
    current_price_eur NUMERIC,
    relevance_score   FLOAT,
    total_count       BIGINT
) AS $$
DECLARE
    v_tsquery   TSQUERY;
    v_offset    INT;
BEGIN
    v_tsquery := plainto_tsquery('simple', p_query);
    v_offset  := (p_page - 1) * p_page_size;

    RETURN QUERY
    WITH ranked AS (
        SELECT
            m.material_id,
            m.material_code,
            m.material_name,
            m.material_class,
            m.grade,
            mp.density_kg_m3,
            (SELECT price_value FROM market_price_records mpr
             WHERE mpr.material_id = m.material_id
               AND mpr.price_type = 'PURCHASE'
               AND mpr.is_active = TRUE
             ORDER BY mpr.price_date DESC LIMIT 1) AS current_price_eur,
            -- Primary ranking: full-text search
            ts_rank_cd(m.search_vector, v_tsquery, 4) * 10
            -- Boost for exact code match
            + CASE WHEN m.material_code ILIKE '%' || p_query || '%' THEN 5 ELSE 0 END
            -- Boost for exact grade match
            + CASE WHEN m.grade ILIKE p_query THEN 3 ELSE 0 END
            -- Trigram similarity boost
            + similarity(m.material_name, p_query) * 2
            AS relevance_score,
            COUNT(*) OVER() AS total_count
        FROM materials m
        LEFT JOIN material_properties mp ON mp.material_id = m.material_id AND mp.valid_to IS NULL
        WHERE
            m.status = p_status
            AND (p_class IS NULL OR m.material_class = ANY(p_class))
            AND (
                m.search_vector @@ v_tsquery
                OR m.material_code ILIKE '%' || p_query || '%'
                OR m.grade ILIKE '%' || p_query || '%'
                OR similarity(m.material_name, p_query) > 0.15
            )
    )
    SELECT *
    FROM ranked
    WHERE relevance_score > 0
    ORDER BY relevance_score DESC
    LIMIT p_page_size
    OFFSET v_offset;
END;
$$ LANGUAGE plpgsql STABLE;
```

### Filtry strukturalne (query parameters → SQL)

| Parametr | SQL fragment | Typ |
|----------|-------------|-----|
| `material_class` | `m.material_class = ANY($class)` | ENUM[] |
| `grade` | `m.grade ILIKE $grade` | TEXT |
| `form_factor` | `m.form_factor = $ff` | ENUM |
| `density_min` | `mp.density_kg_m3 >= $min` | NUMERIC |
| `density_max` | `mp.density_kg_m3 <= $max` | NUMERIC |
| `has_price` | `EXISTS (SELECT 1 FROM market_price_records ...)` | BOOLEAN |
| `supplier_id` | `EXISTS (SELECT 1 FROM supplier_material_mapping ...)` | UUID |
| `process_code` | `EXISTS (SELECT 1 FROM process_compatibility WHERE ...)` | TEXT |
| `standard_system` | `EXISTS (SELECT 1 FROM material_standards WHERE ...)` | ENUM |
| `price_min_eur_kg` | `(SELECT price_value ... ) >= $min` | NUMERIC |
| `price_max_eur_kg` | `(SELECT price_value ... ) <= $max` | NUMERIC |
| `compatibility_level` | `JOIN process_compatibility ... WHERE level = $level` | ENUM |

### Fasetowanie

```python
class FacetBuilder:
    """Builds search facets for UI filter sidebar."""

    def build_facets(self, base_query: Query) -> dict:
        return {
            'material_class': self._facet_count(base_query, 'material_class'),
            'form_factor':    self._facet_count(base_query, 'form_factor'),
            'grade_prefix':   self._facet_grade_groups(base_query),
            'price_range':    self._facet_price_histogram(base_query, buckets=10),
            'has_price':      self._facet_bool(base_query, 'has_market_price'),
            'process':        self._facet_process_compatibility(base_query),
            'supplier_count': self._facet_supplier_count(base_query),
        }
```

---

## 19. AI Readiness — Embeddings i Vector Search

### Architektura AI

```
AILayer
├── EmbeddingService        -- generowanie wektorów (text-embedding-3-small)
├── VectorStore             -- pgvector (HNSW index)
├── SemanticSearchEngine    -- wyszukiwanie podobieństwa kosinusowego
├── MaterialContextBuilder  -- buduje kontekst dla agentów AI
└── RAGRetriever            -- retrieval dla LLM (RAG pipeline)
```

### Generowanie embeddingów

```python
class EmbeddingService:
    """
    Generates vector embeddings for materials using OpenAI text-embedding-3-small.
    Embedding dimension: 1536.
    Input: structured text combining material attributes.
    """

    MODEL = "text-embedding-3-small"
    DIMENSION = 1536

    def build_embedding_text(self, material: MaterialDetail) -> str:
        """
        Constructs the text representation for embedding.
        Includes: name, grade, class, standards, properties, applications.
        """
        parts = [
            f"Material: {material.material_name}",
            f"Grade: {material.grade}",
            f"Class: {material.material_class}",
            f"Form: {material.form_factor}",
        ]

        if material.properties:
            p = material.properties
            if p.density_kg_m3:
                parts.append(f"Density: {p.density_kg_m3} kg/m3")
            if p.max_service_temp_c:
                parts.append(f"Max temperature: {p.max_service_temp_c}°C")

        if material.mechanical_properties:
            m = material.mechanical_properties[0]
            if m.tensile_strength_mpa:
                parts.append(f"Tensile strength: {m.tensile_strength_mpa} MPa")
            if m.yield_strength_mpa:
                parts.append(f"Yield strength: {m.yield_strength_mpa} MPa")

        if material.standards:
            std_strs = [f"{s.standard_system} {s.standard_number} {s.standard_grade or ''}"
                        for s in material.standards if s.is_primary]
            if std_strs:
                parts.append(f"Standards: {', '.join(std_strs)}")

        if material.description:
            parts.append(material.description[:500])

        return ". ".join(parts)

    def generate(self, material: MaterialDetail) -> list[float]:
        text = self.build_embedding_text(material)
        response = openai_client.embeddings.create(
            input=text,
            model=self.MODEL
        )
        return response.data[0].embedding

    def generate_batch(self, materials: list[MaterialDetail]) -> list[list[float]]:
        texts = [self.build_embedding_text(m) for m in materials]
        response = openai_client.embeddings.create(
            input=texts,
            model=self.MODEL
        )
        return [item.embedding for item in response.data]
```

### Wyszukiwanie semantyczne (pgvector)

```python
class SemanticSearchEngine:

    def search(self, query: str, top_k: int = 10,
               min_similarity: float = 0.7,
               class_filter: list[str] | None = None) -> list[SemanticSearchResult]:

        # Embed query
        query_vector = self.embedding_service.embed_query(query)

        # Vector search with optional filter
        results = self.db.execute("""
            SELECT
                m.material_id,
                m.material_code,
                m.material_name,
                m.material_class,
                m.grade,
                1 - (me.vector <=> $1::vector) AS similarity_score
            FROM material_embeddings me
            JOIN materials m ON m.material_id = me.material_id
            WHERE me.is_current = TRUE
              AND m.status = 'ACTIVE'
              AND ($2::text[] IS NULL OR m.material_class::text = ANY($2))
              AND 1 - (me.vector <=> $1::vector) >= $3
            ORDER BY me.vector <=> $1::vector
            LIMIT $4
        """, [query_vector, class_filter, min_similarity, top_k])

        return [SemanticSearchResult(**r) for r in results]
```

### RAG Context Builder (dla agentów AI)

```python
class MaterialContextBuilder:
    """
    Builds material context for LLM agents (Cost Calculator, RFQ AI, etc.)
    Returns structured JSON context ready for system prompt injection.
    """

    def build_context(self, material_id: str,
                      context_type: str = 'cost_calculation') -> dict:
        material = self.material_service.get_with_all(material_id)

        if context_type == 'cost_calculation':
            return {
                'material_code':     material.material_code,
                'grade':             material.grade,
                'density_kg_m3':     material.properties.density_kg_m3,
                'unit_of_measure':   material.properties.unit_of_measure,
                'current_price_eur': self.price_service.get_current(material_id),
                'scrap_rate_pct':    material.cost_coefficients.scrap_rate_pct,
                'yield_rate_pct':    material.cost_coefficients.yield_rate_pct,
                'cutting_waste_pct': material.cost_coefficients.cutting_waste_pct,
                'nesting_eff_pct':   material.cost_coefficients.nesting_efficiency_pct,
            }

        elif context_type == 'process_selection':
            return {
                'material_code': material.material_code,
                'grade':         material.grade,
                'material_class':material.material_class,
                'density_kg_m3': material.properties.density_kg_m3,
                'hardness_hb':   material.mechanical_properties[0].hardness_hb if material.mechanical_properties else None,
                'compatible_processes': [
                    {'process': c.process_code, 'level': c.compatibility_level, 'speed_factor': c.speed_factor}
                    for c in material.compatibility
                    if c.compatibility_level not in ('FORBIDDEN', 'NOT_RECOMMENDED')
                ],
            }

        elif context_type == 'rfq':
            return {
                'material_code':   material.material_code,
                'material_name':   material.material_name,
                'grade':           material.grade,
                'standards':       [f"{s.standard_system} {s.standard_number}"
                                    for s in material.standards if s.is_primary],
                'form_factor':     material.form_factor,
                'is_hazardous':    material.is_hazardous,
                'certificate_req': any(s.certificate_required for s in material.standards),
                'lead_times':      self.supplier_service.get_lead_times(material_id),
                'supplier_count':  self.supplier_service.count_approved(material_id),
            }
```

### Embedding pipeline (Kafka consumer)

```python
class EmbeddingRefreshConsumer:
    """
    Listens to mie.material.updated and mie.material.created events.
    Regenerates embeddings when material data changes.
    """

    TRIGGER_FIELDS = {
        'material_name', 'grade', 'description', 'material_class',
        'density_kg_m3', 'tensile_strength_mpa', 'standard_number'
    }

    def handle(self, event: MaterialUpdatedEvent):
        changed = set(event.changed_fields)
        if not (changed & self.TRIGGER_FIELDS) and not event.requires_embedding_refresh:
            return  # Skip — irrelevant fields changed

        material = self.material_service.get_with_all(event.material_id)
        new_vector = self.embedding_service.generate(material)

        with self.db.transaction():
            # Mark old embedding as not current
            self.db.execute("""
                UPDATE material_embeddings
                SET is_current = FALSE
                WHERE material_id = $1 AND is_current = TRUE
            """, [event.material_id])

            # Insert new embedding
            self.db.execute("""
                INSERT INTO material_embeddings
                  (material_id, embedding_model, embedding_version, vector, text_input, is_current)
                VALUES ($1, $2, $3, $4, $5, TRUE)
            """, [event.material_id, self.embedding_service.MODEL,
                  self.embedding_service.VERSION, new_vector,
                  self.embedding_service.build_embedding_text(material)])

        self.kafka.publish('mie.embedding.regenerated', {
            'material_id': event.material_id,
            'model': self.embedding_service.MODEL
        })
```

---

## 20. Monitoring

### Metryki aplikacji (Prometheus)

```yaml
# Metryki eksponowane przez MIE service

metrics:
  # Business metrics
  - name: mie_materials_total
    type: gauge
    labels: [material_class, status]
    description: "Total number of materials by class and status"

  - name: mie_price_updates_total
    type: counter
    labels: [material_class, source_code, status]
    description: "Price updates processed"

  - name: mie_price_anomalies_total
    type: counter
    labels: [material_class, anomaly_type]
    description: "Price anomalies detected"

  - name: mie_materials_without_price_total
    type: gauge
    labels: [material_class]
    description: "Active materials missing current price"

  - name: mie_substitution_evaluations_total
    type: counter
    labels: [result_go_nogo]
    description: "Substitution evaluations by result"

  # API metrics
  - name: mie_api_requests_total
    type: counter
    labels: [endpoint, method, status_code]

  - name: mie_api_request_duration_seconds
    type: histogram
    labels: [endpoint, method]
    buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5]

  # Search metrics
  - name: mie_search_queries_total
    type: counter
    labels: [search_type]  # fulltext, semantic, filter

  - name: mie_search_duration_seconds
    type: histogram
    labels: [search_type]
    buckets: [0.01, 0.05, 0.1, 0.25, 0.5, 1]

  - name: mie_search_zero_results_total
    type: counter
    description: "Searches returning no results"

  # Embedding metrics
  - name: mie_embedding_generation_duration_seconds
    type: histogram
    labels: [model]

  - name: mie_embeddings_stale_total
    type: gauge
    description: "Materials with stale/missing embeddings"

  # Database metrics
  - name: mie_db_query_duration_seconds
    type: histogram
    labels: [query_type]

  - name: mie_db_pool_connections
    type: gauge
    labels: [state]  # active, idle, waiting
```

### Dashboardy Grafana

| Dashboard | Zawartość |
|-----------|-----------|
| MIE Overview | Liczba materiałów, ceny (live), alerty anomalii |
| Price Monitoring | Trendy cen per kategoria, świeżość danych, źródła |
| API Health | RPS, latencja P50/P95/P99, error rate per endpoint |
| Search Analytics | Top queries, zero-result rate, latencja |
| AI/Embeddings | Freshness embeddingów, czas generowania, podobieństwo |
| Data Quality | Materiały bez cen, bez dostawców, bez właściwości |

### Alerty (Alertmanager)

```yaml
alerts:
  - name: PriceSyncDown
    expr: time() - mie_price_source_last_sync > 86400
    severity: warning
    message: "Price source {{ $labels.source_code }} not synced for 24h"

  - name: PriceAnomalySpike
    expr: rate(mie_price_anomalies_total[1h]) > 5
    severity: critical
    message: "High rate of price anomalies detected"

  - name: MaterialsWithoutPrice
    expr: mie_materials_without_price_total{material_class="METAL"} > 10
    severity: warning
    message: "{{ $value }} active metals without current price"

  - name: EmbeddingsStaleness
    expr: mie_embeddings_stale_total > 50
    severity: warning
    message: "{{ $value }} materials have stale embeddings (>7 days)"

  - name: APIHighLatency
    expr: histogram_quantile(0.95, mie_api_request_duration_seconds) > 2
    severity: warning
    message: "API P95 latency > 2s"

  - name: APIHighErrorRate
    expr: rate(mie_api_requests_total{status_code=~"5.."}[5m]) > 0.05
    severity: critical
    message: "API error rate > 5%"
```

### Healthcheck endpoint

```
GET /health          -- basic liveness
GET /health/ready    -- readiness (DB, cache, Kafka)
GET /health/detailed -- full component status
```

```json
{
  "status": "healthy",
  "timestamp": "2026-06-18T12:00:00Z",
  "version": "1.4.2",
  "components": {
    "database":        { "status": "healthy", "latency_ms": 3 },
    "vector_store":    { "status": "healthy", "index_size": 12840 },
    "kafka":           { "status": "healthy", "lag": 0 },
    "cache":           { "status": "healthy", "hit_rate_pct": 87 },
    "price_sources": {
      "LME":    { "status": "healthy", "last_sync": "2026-06-18T08:00:00Z" },
      "PLATTS": { "status": "healthy", "last_sync": "2026-06-18T09:00:00Z" }
    }
  },
  "metrics": {
    "total_materials":          1243,
    "active_materials":         1178,
    "materials_with_price_pct": 94.2,
    "embeddings_current_pct":   98.1
  }
}
```
