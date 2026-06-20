# Similarity Cost Search Engine — Part 1
# Strategy, Feature Engineering, Embeddings, Vector Schema, Qdrant Design

---

## Section 1: Similarity Strategy

### 1.1 Business Goals

The Similarity Cost Search Engine (SCSE) is the semantic intelligence layer of the Industrial Cost
Intelligence platform. Its purpose is to find entities that are "similar" across five domains —
products, quotes, materials, processes, and suppliers — enabling procurement and engineering teams
to make faster, more informed decisions without manual cross-referencing.

**Core business problems solved:**

| Problem | Without SCSE | With SCSE |
|---------|-------------|-----------|
| Cost estimation for new product | 3–5 days of analyst work | <1 minute, 85%+ confidence |
| Finding alternative supplier | Manual ERP search, incomplete | Semantic search across all suppliers |
| Detecting duplicate RFQ | Human review, error-prone | Automatic near-duplicate alert |
| Price benchmarking | Spreadsheet comparison | Real-time percentile vs similar quotes |
| Alternative material sourcing | Metallurgy expert consultation | Instant spec-compatible substitutes |

### 1.2 Similarity Use Cases Matrix

| Use Case | Products | Quotes | Materials | Processes | Suppliers |
|----------|----------|--------|-----------|-----------|-----------|
| **Cost Estimation** | ✓ Primary | ✓ Primary | ✓ Supporting | ✓ Supporting | — |
| **Alternative Sourcing** | ✓ Primary | — | ✓ Primary | ✓ Supporting | ✓ Primary |
| **RFQ Matching** | ✓ Supporting | ✓ Primary | ✓ Supporting | — | ✓ Supporting |
| **Anomaly Detection** | ✓ Supporting | ✓ Primary | — | ✓ Supporting | ✓ Supporting |
| **Duplicate Detection** | ✓ Primary | ✓ Primary | ✓ Primary | ✓ Primary | ✓ Supporting |
| **Price Benchmarking** | ✓ Supporting | ✓ Primary | ✓ Supporting | ✓ Supporting | — |

### 1.3 Strategy Overview

SCSE uses a **multi-modal hybrid search** architecture combining three complementary signals:

```
Query Input
    │
    ├─► [Dense Vector Search]   — semantic embedding similarity (cosine, 1024d fused vector)
    │       via Qdrant HNSW ANN
    │
    ├─► [Sparse BM25 Search]    — keyword / term matching (TF-IDF weighted)
    │       via Qdrant sparse vectors
    │
    └─► [Structured Filters]    — hard constraints (price range, country, certification, status)
            via Qdrant payload indexes

         ↓ RRF Fusion (Reciprocal Rank Fusion)
         ↓ Re-ranking (cross-encoder + business rules + MMR diversity)
         ↓ Confidence Scoring (5-component, grade A–D)
         ↓ Final top-K results with explanations
```

The three-stage pipeline ensures:
- **Recall**: dense vectors capture semantic/functional similarity even without keyword overlap
- **Precision**: sparse BM25 boosts exact matches (grade codes, standard numbers, part numbers)
- **Business relevance**: filters and business rules enforce procurement constraints

### 1.4 Entity Similarity Taxonomy

Each entity type has a domain-specific definition of "similar":

#### ProductSimilarity
Two products are similar if they serve the same functional purpose with compatible technical
specifications. Similarity is evaluated across:
- **Technical specs**: dimensions, weight, ratings, tolerance class
- **Functional equivalence**: same application domain, interchangeable use cases
- **Cost profile**: similar price band, comparable lead time and MOQ
- **Supply chain**: same or substitute commodity classification (UNSPSC)

#### QuoteSimilarity
Two quotes are similar if they cover comparable scope (material, quantity, process) at a similar
price level. Evaluated across:
- **Price level**: unit price EUR within ±20% of benchmark
- **Validity window**: overlapping validity periods
- **Commercial terms**: same incoterm family, similar payment terms
- **Supplier tier**: comparable quality tier (Tier 1/2/3)
- **Scope**: same or compatible material and process combination

#### MaterialSimilarity
Two materials are similar if they share physical/chemical properties that make them functionally
interchangeable. Evaluated across:
- **Physical properties**: density, tensile strength, elongation, hardness within tolerance bands
- **Chemical composition**: alloy group, key alloying elements within specification ranges
- **Standards equivalence**: cross-referenced international standards (ISO/ASTM/DIN/EN)
- **Commodity group**: same LME commodity, same material family

#### ProcessSimilarity
Two manufacturing processes are similar if they can produce comparable parts with compatible
quality characteristics. Evaluated across:
- **Operations sequence**: same process class (CUT/MAC/FOR/JOI/ASS/FIN), similar operation types
- **Machine type**: same machine family, compatible work envelope
- **Quality output**: same achievable tolerances (IT grade), surface roughness (Ra)
- **Economic profile**: similar cost-per-unit, comparable cycle time range

#### SupplierSimilarity
Two suppliers are similar if they offer comparable capabilities, quality level, and risk profile.
Evaluated across:
- **Capability overlap**: shared process and material capabilities (multi-hot vectors)
- **Geography**: same country or logistics zone, comparable lead time to plant
- **Certification profile**: overlapping certification set (ISO/IATF/AS9100)
- **Scorecard profile**: similar composite scores across all 5 dimensions

### 1.5 Distance Metrics per Entity Type

| Entity Type | Primary Metric | Secondary Metric | Justification |
|-------------|---------------|-----------------|--------------|
| Product | Cosine (fused 1024d) | Euclidean (structured 512d) | Semantic + spec comparison |
| Quote | Cosine (fused 1024d) | Euclidean (price features) | Meaning + price proximity |
| Material | Cosine (text 3072d) | Euclidean (physical 512d) | Chemistry text + numeric props |
| Process | Cosine (fused 1024d) | Euclidean (params 512d) | Capability text + cycle time |
| Supplier | Cosine (fused 1024d) | Dot product (capability vectors) | Semantic + multi-hot overlap |

**Metric selection rationale:**
- **Cosine similarity**: ideal for normalized embeddings; invariant to magnitude; best for semantic
  comparisons where the direction of the vector matters more than its length
- **Euclidean (L2)**: appropriate for numeric feature spaces where absolute differences matter
  (tensile strength deviation of 50 MPa is always 50 MPa regardless of context)
- **Dot product**: efficient for multi-hot binary capability vectors; equivalent to counting shared
  capabilities when vectors are normalized

### 1.6 Similarity Thresholds

| Label | Score Range | Interpretation | Typical Use |
|-------|-------------|---------------|-------------|
| `EXACT` | > 0.97 | Same item, different record | Duplicate detection, deduplication |
| `NEAR_DUPLICATE` | 0.93 – 0.97 | Nearly identical, minor variation | Duplicate alert, version detection |
| `HIGHLY_SIMILAR` | 0.85 – 0.92 | Strong match, suitable alternative | Alternative sourcing, substitution |
| `SIMILAR` | 0.70 – 0.84 | Valid comparison baseline | Cost benchmarking, price analysis |
| `RELATED` | 0.50 – 0.69 | Same category, different specs | Market awareness, exploration |
| `UNRELATED` | < 0.50 | Not comparable | Filtered out of results |

These thresholds are calibrated on a labeled dataset of 5,000 human-verified entity pairs per
entity type. They are re-evaluated monthly as part of the model evaluation pipeline (Section 16).

### 1.7 Multi-Stage Retrieval Pipeline

```
┌─────────────────────────────────────────────────────────────────────┐
│                    SCSE Multi-Stage Pipeline                        │
│                                                                     │
│  Input: query_text / query_entity_id + optional filters             │
│                          │                                          │
│  Stage 0: Embedding      │  <50ms                                  │
│  ┌──────────────────────▼──────────────────────────┐               │
│  │  EmbeddingPipeline                               │               │
│  │  text → OpenAI text-embedding-3-large (3072d)   │               │
│  │  features → StructuredFeatureEncoder (512d)      │               │
│  │  fusion → FusionAttentionLayer (1024d)           │               │
│  └──────────────────────┬──────────────────────────┘               │
│                          │                                          │
│  Stage 1: ANN Recall     │  <100ms                                 │
│  ┌──────────────────────▼──────────────────────────┐               │
│  │  Qdrant Hybrid Search                            │               │
│  │  ├── Dense ANN (fused 1024d, HNSW ef=128)       │               │
│  │  │   → top-200 candidates                        │               │
│  │  ├── Sparse BM25 (TF-IDF 30k vocab)             │               │
│  │  │   → top-200 candidates                        │               │
│  │  └── RRF Fusion → top-200 merged candidates     │               │
│  └──────────────────────┬──────────────────────────┘               │
│                          │                                          │
│  Stage 2: Re-ranking     │  <100ms                                 │
│  ┌──────────────────────▼──────────────────────────┐               │
│  │  SimilarityReRanker                              │               │
│  │  ├── Structured similarity (cosine, 512d)        │               │
│  │  ├── Business rules (supplier tier, status)      │               │
│  │  ├── MMR diversity penalty                       │               │
│  │  └── Weighted score: sem×0.40 + struct×0.30     │               │
│  │      + biz×0.20 + diversity×0.10                │               │
│  └──────────────────────┬──────────────────────────┘               │
│                          │                                          │
│  Stage 3: Confidence     │  <10ms                                  │
│  ┌──────────────────────▼──────────────────────────┐               │
│  │  ConfidenceCalculator (5-component)              │               │
│  │  → grade: HIGH / MEDIUM / LOW / UNCERTAIN        │               │
│  │  → reasons[], warnings[]                         │               │
│  └──────────────────────┬──────────────────────────┘               │
│                          │                                          │
│  Output: top-K results with scores, confidence, explanations        │
└─────────────────────────────────────────────────────────────────────┘
```

**Latency budget (P95):**
- Embedding: ≤ 80ms (OpenAI API P95)
- ANN recall: ≤ 50ms (Qdrant HNSW ef=128)
- Re-ranking: ≤ 80ms (Python in-process)
- Confidence: ≤ 10ms
- Total target: ≤ 300ms P95

### 1.8 Architectural Decisions

#### ADR-001: Qdrant as Primary Vector Store
**Decision**: Use Qdrant as the real-time online search backend.
**Rationale**:
- Native multi-vector support: store text, structured, and fused vectors per point in one record
- Built-in sparse vector support (SPLADE/BM25) enabling native hybrid search without extra infra
- Payload filtering: pre-filter ANN search, reducing false positives vs full post-filter
- Production-grade clustering (Raft consensus, replication_factor=2)
- gRPC API for high-throughput low-latency access
**Rejected**: Pinecone (no sparse vectors, SaaS lock-in), Weaviate (higher ops complexity), Milvus (complex deployment)

#### ADR-002: FAISS for Offline Batch Processing
**Decision**: Use FAISS for nightly batch similarity computation and candidate pre-generation.
**Rationale**:
- GPU support for high-throughput batch embedding generation
- IVF-PQ quantization enables indexing 50M+ vectors with manageable RAM
- Batch all-pairs similarity computation for precomputing `similarity_cache` table
- No always-on cluster required — run as Airflow DAG on ephemeral GPU instance
**Rejected**: Qdrant for batch (would require large clusters for temporary jobs)

#### ADR-003: pgvector for Transactional Search
**Decision**: Use pgvector as the strongly-consistent search fallback within transactions.
**Rationale**:
- Same PostgreSQL transaction as quote creation — no eventual consistency lag
- SQL joins: combine vector search with complex relational filters in one query
- Automatic freshness: vectors updated in same transaction as entity data
- Used as Qdrant fallback if cluster unavailable
**Limitation**: Not suitable for >10M vectors or >500 QPS — use Qdrant at scale

#### ADR-004: Separate Qdrant Collections per Entity Type
**Decision**: One Qdrant collection per entity type (5 collections).
**Rationale**:
- Prevents cross-type vector pollution (supplier vectors ≠ material vectors in same space)
- Allows different HNSW parameters per entity type (suppliers: m=16, materials: m=32)
- Independent scaling: products may need 10× more shards than processes
- Cleaner payload schema (no nullable fields for irrelevant entity data)
**Rejected**: Single collection with entity_type filter (would pollute vector space)

#### ADR-005: SemVer Embedding Versioning
**Decision**: Store `model_version` and `encoder_version` with every vector record.
**Rationale**:
- Safe model migrations: old and new embeddings coexist during dual-write period
- Gradual traffic cutover: 10% → 50% → 100% without downtime
- Rollback capability: switch back to old embeddings if new model degrades recall
- Audit trail: know exactly which model version produced each similarity result

---

## Section 2: Feature Engineering

Feature engineering transforms raw entity data into dense numeric vectors suitable for the
structured feature encoder (MLP). Combined with text embeddings, these form the fused 1024d
representation used for similarity search.

### 2.1 Product Features (40 features)

#### Numeric Features (18) — StandardScaler normalization
```
unit_weight_kg          — product weight in kilograms
length_mm               — bounding box length
width_mm                — bounding box width
height_mm               — bounding box height
operating_temp_min_c    — minimum operating temperature (°C)
operating_temp_max_c    — maximum operating temperature (°C)
voltage_rating_v        — rated voltage (electrical products)
current_rating_a        — rated current (electrical products)
ip_rating_score         — IP protection level (IP00=0 → IP69K=69)
mtbf_hours              — mean time between failures (hours)
list_price_eur          — catalog list price (EUR)
avg_quote_price_eur     — average of last 12 months quotes (EUR)
price_volatility_cv     — coefficient of variation of quote prices
yoy_price_trend         — year-over-year price change (%)
avg_lead_time_days      — average supplier lead time (days)
min_order_qty           — minimum order quantity
active_supplier_count   — number of active qualified suppliers
availability_score      — 0-1 normalized stock/availability metric
```

#### Categorical Features (4) — OneHotEncoding
```
product_category        — top-level product category (50 categories)
commodity_code          — UNSPSC 8-digit code (hashed to 32-dim)
unit_of_measure         — EA / KG / M / M2 / M3 / L / SET (OHE 7 values)
lifecycle_stage         — ACTIVE / NPI / MATURE / EOL / DISCONTINUED (OHE 5 values)
```

#### Text Features (4) — embedded via text-embedding-3-large
```
product_name            — commercial name and model number
technical_description   — full technical specification text
application_notes       — intended use cases and application context
standards_list          — applicable standards joined as comma-separated string
```

#### Feature vector shape: numeric(18) + OHE_category(50) + OHE_uom(7) + OHE_lifecycle(5) = 80 dims
Plus text embedding 3072d. Total structured input to MLP: 80d → encoder output: 512d.

### 2.2 Quote Features (35 features)

#### Numeric Features (14)
```
unit_price_eur          — unit price in EUR
discount_pct            — negotiated discount percentage
tooling_cost_eur        — one-time tooling cost
nre_cost_eur            — non-recurring engineering cost
volume_break_count      — number of volume pricing tiers
supplier_scorecard      — composite supplier score (0-100)
supplier_tier           — 1 (strategic) / 2 (approved) / 3 (conditional)
country_risk_score      — Coface geopolitical risk (0-1, higher = riskier)
delivery_reliability    — OTD% from last 12 months (0-1)
payment_terms_days      — net payment days (30/45/60/90/120)
validity_days           — quote validity window in days
operation_count         — number of manufacturing operations in scope
setup_time_hrs          — total setup/NRE time in hours
complexity_score        — derived complexity score 0-1 (from operation count + tolerances)
```

#### Categorical Features (6) — OHE
```
currency_code           — ISO 4217 currency (EUR/USD/GBP/PLN/CZK/HUF)
incoterm                — EXW/FCA/CPT/CIP/DAP/DDP/FOB/CIF (OHE 8 values)
primary_material_category — steel/aluminum/plastic/composites/electronics/other
payment_type            — ADVANCE/NET/MILESTONE/LC (OHE 4)
supplier_country_iso    — 2-letter ISO country (OHE top-50 + OTHER)
quote_type              — SPOT/FRAMEWORK/RFQ/CATALOG (OHE 4)
```

#### Text Features (3)
```
material_description    — material and grade text
process_description     — process scope description
terms_notes             — commercial terms and notes
```

### 2.3 Material Features (45 features)

#### Numeric Physical Properties (16)
```
density_g_cm3           — material density (g/cm³)
tensile_strength_mpa    — ultimate tensile strength (MPa)
yield_strength_mpa      — yield strength / proof stress (MPa)
elongation_pct          — elongation at break (%)
hardness_hrc            — Rockwell hardness C scale (converted from other scales)
thermal_conductivity    — W/(m·K) thermal conductivity
electrical_resistivity  — Ω·m electrical resistivity (log10 scaled)
melting_point_c         — melting point (°C)
thermal_expansion       — coefficient of thermal expansion (10⁻⁶/K)
specific_heat           — specific heat capacity (J/(kg·K))
modulus_of_elasticity   — Young's modulus (GPa)
poisson_ratio           — Poisson's ratio
fatigue_limit_mpa       — endurance limit (MPa) at 10⁷ cycles
fracture_toughness      — KIC (MPa·m^0.5)
price_eur_per_kg        — current market price (EUR/kg)
price_volatility_30d    — 30-day price volatility (%)
```

#### Numeric Chemical Composition (8, range 0-100 wt%)
```
carbon_content_pct      — C content (wt%)
chromium_pct            — Cr content (wt%)
nickel_pct              — Ni content (wt%)
manganese_pct           — Mn content (wt%)
silicon_pct             — Si content (wt%)
molybdenum_pct          — Mo content (wt%)
copper_pct              — Cu content (wt%)
aluminum_pct            — Al content (wt%) for aluminum alloys
```

#### Categorical Features (7) — OHE
```
material_group          — steel/aluminum/copper/titanium/polymer/composite/ceramic/other
material_subgroup       — carbon_steel/stainless/tool_steel/structural/etc (30 values)
commodity_category      — FERROUS / NON_FERROUS / POLYMER / COMPOSITES
alloy_group             — austenitic/ferritic/martensitic/duplex/precipitation (stainless)
hazmat_class            — UN hazmat class (0-9, 0=none)
lme_commodity_code      — LME ticker or NULL
availability_score_bin  — HIGH/MEDIUM/LOW/CRITICAL (binned from numeric)
```

#### Text Features (6)
```
material_name           — commercial name (e.g. "316L Stainless Steel")
iso_grade               — ISO designation (e.g. "X2CrNiMo17-12-2")
astm_grade              — ASTM designation (e.g. "A316/316L")
din_grade               — DIN designation (e.g. "1.4404")
standards_list          — all applicable standards joined
typical_applications    — application domain description
```

### 2.4 Process Features (38 features)

#### Numeric Parameters (20)
```
cycle_time_sec          — nominal cycle time per piece (seconds)
setup_time_min          — setup / changeover time (minutes)
batch_size              — optimal batch size for the process
machine_power_kw        — installed power of primary machine (kW)
tooling_cost_eur        — amortized tooling cost per piece (EUR)
operator_skill_level    — 1 (unskilled) to 5 (highly specialized)
automation_level        — 0 (fully manual) to 1 (fully automated)
cost_per_hour_eur       — machine + operator cost per hour (EUR)
energy_cost_per_unit    — energy cost per piece (EUR)
scrap_rate_pct          — typical scrap/rework rate (%)
oee_score               — Overall Equipment Effectiveness (%)
achievable_cpk          — process capability index Cpk achievable
max_part_size_mm3       — maximum workable volume (mm³, log10 scaled)
min_feature_size_mm     — minimum achievable feature size (mm)
surface_roughness_ra    — achievable Ra surface finish (µm)
tolerance_it_grade      — IT grade number (IT1=finest to IT18=roughest)
material_removal_rate   — for machining: MRR (cm³/min)
depth_of_cut_mm         — for cutting processes
feed_rate               — process-specific feed parameter
temperature_c           — process temperature (forming, heat treatment, etc.)
```

#### Categorical Features (5) — OHE
```
process_class           — CUT / MAC / FOR / JOI / ASS / FIN (6 values)
process_type            — 50 specific process types within class
machine_family          — lathe/mill/grinder/press/welder/etc (30 values)
environment_required    — CLEAN_ROOM / INERT_GAS / STANDARD / OUTDOOR
certification_required  — AS9100/NADCAP/ISO13485/NONE (OHE multi-hot)
```

#### Text Features (3)
```
process_name            — full process name
process_description     — detailed description of operations
compatible_materials    — list of compatible material groups joined
```

### 2.5 Supplier Features (42 features)

#### Numeric Performance (12)
```
scorecard_total         — composite scorecard 0-100
quality_score           — quality component 0-100
delivery_score          — delivery component 0-100
price_score             — price competitiveness 0-100
service_score           — service / responsiveness 0-100
risk_score              — risk component 0-100 (higher = less risk)
altman_z_score          — financial stability score (normalized 0-10)
paydex_score            — D&B payment index 0-100
revenue_eur_log         — log10(annual revenue EUR)
debt_ratio              — total debt / total assets
years_in_business       — company age in years
employee_count_log      — log10(employee count)
```

#### Numeric Geographic (3)
```
logistics_hub_distance_km — distance to nearest major logistics hub (km)
port_distance_km        — distance to nearest sea/air port (km)
geopolitical_risk_score — Coface country risk 0-1
```

#### Capability Vectors (multi-hot, treated as numeric)
```
process_capability_vector[50] — one dimension per process type (1=capable, 0=not)
material_capability_vector[80] — one dimension per material group (1=capable, 0=not)
```

#### Categorical Features (4) — OHE
```
supplier_type           — MANUFACTURER/WHOLESALER/DISTRIBUTOR/SUBCONTRACTOR/TRADER (OHE 5)
country_iso             — ISO 3166-1 alpha-2 (OHE top-50 + OTHER)
size_class              — MICRO/<10 / SMALL/10-50 / MEDIUM/50-250 / LARGE/>250 (OHE 4)
market_segment          — AUTOMOTIVE/AEROSPACE/INDUSTRIAL/ELECTRONICS/GENERAL (OHE 5)
```

#### Text Features (3)
```
supplier_name           — full legal name
specialization_summary  — key specialization description
certifications_text     — all certifications joined as text
```

### 2.6 Python Feature Extractor Implementation

```python
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID
from datetime import datetime
from typing import Any
import numpy as np
from sklearn.preprocessing import StandardScaler, MinMaxScaler, OneHotEncoder

class EntityType(Enum):
    PRODUCT  = "PRODUCT"
    QUOTE    = "QUOTE"
    MATERIAL = "MATERIAL"
    PROCESS  = "PROCESS"
    SUPPLIER = "SUPPLIER"

@dataclass
class FeatureVector:
    entity_type: EntityType
    entity_id: UUID
    numeric: np.ndarray         # shape: (n_numeric,) — normalized
    categorical: np.ndarray     # shape: (n_ohe_dims,) — one-hot encoded
    text_raw: str               # rendered text template (for embedding)
    text_embedding: np.ndarray  # shape: (3072,) — filled by EmbeddingPipeline
    combined: np.ndarray        # shape: (n_numeric + n_ohe_dims,) — structured input to MLP
    version: str = "1.0.0"
    extracted_at: datetime = field(default_factory=datetime.utcnow)

class FeatureNormalizer:
    """Normalizes raw feature values using appropriate scaler per feature group."""
    
    def __init__(self):
        self.numeric_scaler = StandardScaler()
        self.price_scaler   = MinMaxScaler()
        self.ratio_scaler   = MinMaxScaler((0.0, 1.0))
    
    def fit(self, dataset: list[dict], numeric_fields: list[str]) -> None:
        numeric_matrix = [
            [d.get(f) or 0.0 for f in numeric_fields]
            for d in dataset
        ]
        self.numeric_scaler.fit(numeric_matrix)
    
    def transform(self, raw: list[float]) -> np.ndarray:
        return self.numeric_scaler.transform([raw])[0]

class ProductFeatureExtractor:
    NUMERIC_FEATURES = [
        "unit_weight_kg", "length_mm", "width_mm", "height_mm",
        "operating_temp_min_c", "operating_temp_max_c", "voltage_rating_v",
        "current_rating_a", "ip_rating_score", "mtbf_hours",
        "list_price_eur", "avg_quote_price_eur", "price_volatility_cv",
        "yoy_price_trend", "avg_lead_time_days", "min_order_qty",
        "active_supplier_count", "availability_score",
    ]
    CATEGORICAL_FEATURES = [
        "product_category", "unit_of_measure", "lifecycle_stage",
    ]
    TEXT_FEATURES = [
        "product_name", "technical_description", "application_notes",
    ]
    
    def __init__(self, normalizer: FeatureNormalizer, ohe: OneHotEncoder):
        self.normalizer = normalizer
        self.ohe = ohe
    
    def extract(self, product: dict) -> FeatureVector:
        numeric_raw  = [product.get(f) or 0.0 for f in self.NUMERIC_FEATURES]
        numeric_norm = self.normalizer.transform(numeric_raw)
        cats         = [[product.get(f, "UNKNOWN") for f in self.CATEGORICAL_FEATURES]]
        categorical  = self.ohe.transform(cats).toarray()[0]
        combined     = np.concatenate([numeric_norm, categorical])
        text_raw     = self._render_template(product)
        return FeatureVector(
            entity_type=EntityType.PRODUCT,
            entity_id=product["product_id"],
            numeric=numeric_norm,
            categorical=categorical,
            text_raw=text_raw,
            text_embedding=np.zeros(3072),
            combined=combined,
        )
    
    def _render_template(self, p: dict) -> str:
        stds = ", ".join(p.get("standards_list", []))
        return (
            f"Product: {p.get('product_name', '')}\n"
            f"Category: {p.get('product_category', '')} | UNSPSC: {p.get('commodity_code', '')}\n"
            f"Description: {p.get('technical_description', '')}\n"
            f"Standards: {stds}\n"
            f"Applications: {p.get('application_notes', '')}\n"
            f"Key specs: weight={p.get('unit_weight_kg', '')}kg, "
            f"lifecycle={p.get('lifecycle_stage', '')}"
        )

class FeaturePipeline:
    """Versioned feature pipeline with Population Stability Index drift detection."""
    
    VERSION = "1.0.0"
    PSI_THRESHOLD = 0.20  # Alert threshold for feature distribution drift
    PSI_BINS = 10
    
    def __init__(self, extractor: Any, normalizer: FeatureNormalizer):
        self.extractor = extractor
        self.normalizer = normalizer
        self._reference_distribution: np.ndarray | None = None
    
    def set_reference(self, reference_vectors: np.ndarray) -> None:
        self._reference_distribution = reference_vectors
    
    def process(self, entity: dict) -> FeatureVector:
        fv = self.extractor.extract(entity)
        if self._reference_distribution is not None:
            psi = self._compute_psi(fv.numeric)
            if psi > self.PSI_THRESHOLD:
                self._emit_drift_alert(psi)
        return fv
    
    def _compute_psi(self, current: np.ndarray) -> float:
        """Population Stability Index = Σ (A_i - E_i) × ln(A_i / E_i)"""
        # Compare current vector distribution against reference using binning
        ref_hist, bin_edges = np.histogram(
            self._reference_distribution.mean(axis=0), bins=self.PSI_BINS
        )
        cur_hist, _ = np.histogram(current, bins=bin_edges)
        # Normalize to proportions, add small epsilon to avoid log(0)
        ref_prop = (ref_hist / ref_hist.sum()) + 1e-6
        cur_prop = (cur_hist / cur_hist.sum()) + 1e-6
        return float(np.sum((cur_prop - ref_prop) * np.log(cur_prop / ref_prop)))
    
    def _emit_drift_alert(self, psi: float) -> None:
        # Publish to Kafka topic: scse.model.retrain-requested
        import logging
        logging.getLogger("scse.drift").warning(
            "Feature drift detected PSI=%.4f (threshold=%.2f). "
            "Publishing retrain-requested event.", psi, self.PSI_THRESHOLD
        )
```

---

## Section 3: Embedding Design

### 3.1 Embedding Architecture

SCSE uses a **three-vector architecture** per entity:

```
Entity (structured data)
    │
    ├─► Text Template Renderer
    │       │
    │       ▼
    │   text_embedding (3072d)  ←  OpenAI text-embedding-3-large
    │
    ├─► FeatureExtractor
    │       │
    │       ▼
    │   structured_embedding (512d)  ←  StructuredFeatureEncoder (MLP, trained)
    │
    └─► FusionAttentionLayer (learned α_text, α_struct)
            │
            ▼
        fused_embedding (1024d)  ←  L2-normalized output

Stored in Qdrant as named vectors: {"text": 3072d, "structured": 512d, "fused": 1024d}
```

**Model selection:**
- `text-embedding-3-large` (3072d): primary; best semantic richness from OpenAI; ~$0.13/1M tokens
- `text-embedding-3-small` (1536d): fallback; 5× cheaper; used when cost or latency is priority
- `StructuredFeatureEncoder`: custom PyTorch MLP; trained on labeled similar/dissimilar pairs;
  captures numeric feature similarity that text cannot (tensile strength, cycle time, price band)
- `FusionAttentionLayer`: attention-weighted late fusion; learns entity-type-specific balance of
  text vs structured signals (materials: higher text weight for chemistry; quotes: higher structured
  weight for price features)

### 3.2 Text Embedding Templates

Templates are rendered per entity before sending to the OpenAI embedding API. They follow a
structured format that maximizes information density within the token limit.

**Product Template:**
```
Product: {{ product_name }}
Category: {{ product_category }} | UNSPSC: {{ commodity_code }}
Description: {{ technical_description }}
Standards: {{ standards_list | join(', ') }}
Applications: {{ application_notes }}
Key specs: weight={{ unit_weight_kg }}kg, material={{ primary_material }},
           lifecycle={{ lifecycle_stage }}, voltage={{ voltage_rating_v }}V
```

**Quote Template:**
```
Quote for: {{ material_name }} ({{ quantity }} {{ unit_of_measure }})
Supplier: {{ supplier_name }} (Tier {{ supplier_tier }}, {{ supplier_country }})
Price: {{ unit_price_eur }} EUR/{{ unit_of_measure }} | Validity: {{ validity_days }} days
Terms: {{ incoterm }}, {{ payment_terms_days }} days net
Process scope: {{ process_description }}
Material grade: {{ material_grade }}
```

**Material Template:**
```
Material: {{ material_name }} | Grade: {{ iso_grade }}
Group: {{ material_group }} / {{ material_subgroup }}
Chemical composition: {{ composition_summary }}
Properties: tensile={{ tensile_strength_mpa }}MPa, density={{ density_g_cm3 }}g/cm³,
            hardness={{ hardness_hrc }}HRC, elongation={{ elongation_pct }}%
Standards: {{ standards_list | join(', ') }}
Applications: {{ typical_applications }}
```

**Process Template:**
```
Process: {{ process_name }} | Class: {{ process_class }} | Type: {{ process_type }}
Machine family: {{ machine_family }} | Tolerance: IT{{ tolerance_it_grade }}
Cycle time: {{ cycle_time_sec }}s | Setup: {{ setup_time_min }}min
Compatible materials: {{ compatible_materials | join(', ') }}
Achievable: Ra={{ surface_roughness_ra }}µm, Cpk≥{{ achievable_cpk }}
Parameters: {{ key_parameters_summary }}
```

**Supplier Template:**
```
Supplier: {{ supplier_name }} | Type: {{ supplier_type }} | Country: {{ country_iso }}
Certifications: {{ certifications | join(', ') }}
Process capabilities: {{ top_processes | join(', ') }}
Material capabilities: {{ top_materials | join(', ') }}
Performance: score={{ scorecard_total }}/100
             (Quality={{ quality_score }}, Delivery={{ delivery_score }},
              Price={{ price_score }}, Service={{ service_score }}, Risk={{ risk_score }})
Specializations: {{ specialization_summary }}
```

### 3.3 Structured Feature Encoder (PyTorch)

```python
import torch
import torch.nn as nn
import torch.nn.functional as F

class StructuredFeatureEncoder(nn.Module):
    """
    Encodes concatenated numeric + categorical features into a 512-dimensional embedding.
    Trained with cosine similarity loss on labeled similar/dissimilar entity pairs.
    """
    
    def __init__(self, input_dim: int, hidden_dim: int = 1024, output_dim: int = 512,
                 dropout_rate: float = 0.2):
        super().__init__()
        self.network = nn.Sequential(
            nn.Linear(input_dim, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout_rate),
            nn.Linear(hidden_dim, hidden_dim // 2),
            nn.BatchNorm1d(hidden_dim // 2),
            nn.ReLU(),
            nn.Dropout(dropout_rate / 2),
            nn.Linear(hidden_dim // 2, output_dim),
        )
    
    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.network(x)
        return F.normalize(out, p=2, dim=1)  # L2 normalize output

class FusionAttentionLayer(nn.Module):
    """
    Late fusion of text embedding (3072d) + structured embedding (512d) → 1024d.
    Uses learned attention weights α_text and α_struct (sum to 1.0).
    """
    
    def __init__(self, text_dim: int = 3072, struct_dim: int = 512, output_dim: int = 1024):
        super().__init__()
        self.text_proj   = nn.Linear(text_dim, output_dim)
        self.struct_proj = nn.Linear(struct_dim, output_dim)
        # Attention: learns which modality to trust more per entity type
        self.attention   = nn.Sequential(
            nn.Linear(output_dim * 2, 64),
            nn.ReLU(),
            nn.Linear(64, 2),
            nn.Softmax(dim=1),  # outputs [α_text, α_struct] summing to 1.0
        )
    
    def forward(self, text_emb: torch.Tensor, struct_emb: torch.Tensor) -> torch.Tensor:
        t = self.text_proj(text_emb)      # (B, 1024)
        s = self.struct_proj(struct_emb)  # (B, 1024)
        # Compute attention weights from concatenated projections
        combined  = torch.cat([t, s], dim=1)  # (B, 2048)
        weights   = self.attention(combined)   # (B, 2)
        α_text    = weights[:, 0:1]            # (B, 1)
        α_struct  = weights[:, 1:2]            # (B, 1)
        fused     = α_text * t + α_struct * s  # weighted sum
        return F.normalize(fused, p=2, dim=1)  # L2 normalize

class CosineSimilarityLoss(nn.Module):
    """Training loss for StructuredFeatureEncoder using labeled pairs."""
    
    def forward(
        self,
        emb_a: torch.Tensor,
        emb_b: torch.Tensor,
        labels: torch.Tensor,  # 1.0 = similar, -1.0 = dissimilar
        margin: float = 0.3,
    ) -> torch.Tensor:
        cosine_sim = F.cosine_similarity(emb_a, emb_b)
        # Similar pairs: minimize (1 - cosine_sim)
        # Dissimilar pairs: minimize max(0, cosine_sim - margin)
        loss_similar    = (1.0 - cosine_sim) * (labels == 1.0).float()
        loss_dissimilar = torch.clamp(cosine_sim - margin, min=0.0) * (labels == -1.0).float()
        return (loss_similar + loss_dissimilar).mean()
```

### 3.4 EmbeddingPipeline

```python
import asyncio
from dataclasses import dataclass
from uuid import UUID
from datetime import datetime
from openai import AsyncOpenAI
import torch

@dataclass
class EmbeddingResult:
    entity_id: UUID
    entity_type: EntityType
    text_embedding: list[float]        # 3072d
    structured_embedding: list[float]  # 512d
    fused_embedding: list[float]       # 1024d
    model_version: str                 # e.g. "text-embedding-3-large"
    encoder_version: str               # e.g. "scse-encoder-v1.2.0"
    created_at: datetime

class EmbeddingPipeline:
    MAX_TOKENS_PER_MINUTE = 8_000_000  # text-embedding-3-large limit
    
    def __init__(
        self,
        openai_client: AsyncOpenAI,
        feature_encoder: StructuredFeatureEncoder,
        fusion_layer: FusionAttentionLayer,
        model: str = "text-embedding-3-large",
        encoder_version: str = "1.0.0",
    ):
        self.openai          = openai_client
        self.feature_encoder = feature_encoder
        self.fusion          = fusion_layer
        self.model           = model
        self.encoder_version = encoder_version
    
    async def embed_entity(
        self,
        entity: dict,
        feature_vector: FeatureVector,
    ) -> EmbeddingResult:
        """Embed a single entity synchronously."""
        # Step 1: Text embedding via OpenAI
        response  = await self.openai.embeddings.create(
            input=feature_vector.text_raw,
            model=self.model,
        )
        text_emb = response.data[0].embedding  # list[float], len=3072
        
        # Step 2: Structured embedding via MLP
        struct_tensor = torch.tensor(
            feature_vector.combined, dtype=torch.float32
        ).unsqueeze(0)
        with torch.no_grad():
            struct_emb = self.feature_encoder(struct_tensor).squeeze(0).tolist()
        
        # Step 3: Fusion
        text_t   = torch.tensor(text_emb, dtype=torch.float32).unsqueeze(0)
        struct_t = torch.tensor(struct_emb, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            fused = self.fusion(text_t, struct_t).squeeze(0).tolist()
        
        return EmbeddingResult(
            entity_id=entity["entity_id"],
            entity_type=feature_vector.entity_type,
            text_embedding=text_emb,
            structured_embedding=struct_emb,
            fused_embedding=fused,
            model_version=self.model,
            encoder_version=self.encoder_version,
            created_at=datetime.utcnow(),
        )
    
    async def batch_embed(
        self,
        entities: list[dict],
        feature_vectors: list[FeatureVector],
        batch_size: int = 100,
    ) -> list[EmbeddingResult]:
        """Batch embedding with OpenAI API (more efficient per-token)."""
        results = []
        from more_itertools import chunked
        
        for batch_e, batch_fv in zip(
            chunked(entities, batch_size),
            chunked(feature_vectors, batch_size),
        ):
            batch_e  = list(batch_e)
            batch_fv = list(batch_fv)
            
            # Batch OpenAI call for text embeddings
            response = await self.openai.embeddings.create(
                input=[fv.text_raw for fv in batch_fv],
                model=self.model,
            )
            text_embeddings = [d.embedding for d in response.data]
            
            # Batch structured encoding
            struct_inputs = torch.tensor(
                [fv.combined for fv in batch_fv], dtype=torch.float32
            )
            with torch.no_grad():
                struct_embeddings = self.feature_encoder(struct_inputs).tolist()
            
            # Batch fusion
            text_tensors   = torch.tensor(text_embeddings, dtype=torch.float32)
            struct_tensors = torch.tensor(struct_embeddings, dtype=torch.float32)
            with torch.no_grad():
                fused_tensors = self.fusion(text_tensors, struct_tensors).tolist()
            
            for entity, fv, text_emb, struct_emb, fused in zip(
                batch_e, batch_fv, text_embeddings, struct_embeddings, fused_tensors
            ):
                results.append(EmbeddingResult(
                    entity_id=entity["entity_id"],
                    entity_type=fv.entity_type,
                    text_embedding=text_emb,
                    structured_embedding=struct_emb,
                    fused_embedding=fused,
                    model_version=self.model,
                    encoder_version=self.encoder_version,
                    created_at=datetime.utcnow(),
                ))
        
        return results
```

### 3.5 Embedding Versioning Strategy

| Phase | Duration | Action |
|-------|----------|--------|
| Pre-migration | Before cutover | Evaluate new model on labeled dataset |
| Dual-write | 2 weeks | Write old + new embeddings simultaneously |
| Shadow traffic | 1 week | Route 10% search traffic to new embeddings, compare results |
| Gradual cutover | 1 week | 10% → 25% → 50% → 75% → 100% |
| Post-migration | 30 days | Keep old embeddings, monitor for regressions |
| Cleanup | After 30 days | Delete old embedding vectors |

**Drift detection:**
```python
class EmbeddingDriftMonitor:
    ALERT_THRESHOLD = 0.15  # avg cosine distance between old and new embeddings
    
    def check_drift(
        self,
        old_embeddings: list[list[float]],
        new_embeddings: list[list[float]],
        sample_size: int = 1000,
    ) -> DriftReport:
        import random
        sample_indices = random.sample(range(len(old_embeddings)), min(sample_size, len(old_embeddings)))
        distances = []
        for i in sample_indices:
            old = np.array(old_embeddings[i])
            new = np.array(new_embeddings[i])
            # Cosine distance = 1 - cosine_similarity
            cos_sim  = np.dot(old, new) / (np.linalg.norm(old) * np.linalg.norm(new))
            distances.append(1.0 - cos_sim)
        avg_distance = np.mean(distances)
        return DriftReport(
            avg_cosine_distance=avg_distance,
            is_drifted=avg_distance > self.ALERT_THRESHOLD,
            sample_size=len(distances),
        )
```

---

## Section 4: Vector Schema

### 4.1 Universal VectorRecord

```python
from dataclasses import dataclass, field, asdict
from uuid import UUID, uuid4
from datetime import datetime
from typing import Any

@dataclass
class VectorPayload:
    """Qdrant point payload — stored alongside vectors for filtering and retrieval."""
    
    # --- Universal fields (all entity types) ---
    entity_type:  str   # EntityType value
    entity_id:    str   # UUID as string (Qdrant payload limitation)
    name:         str
    status:       str   # ACTIVE / INACTIVE / ARCHIVED
    created_at:   str   # ISO-8601 timestamp
    updated_at:   str   # ISO-8601 timestamp
    
    # --- Numeric filter fields (pre-indexed in Qdrant) ---
    price_eur:        float | None = None
    lead_time_days:   int   | None = None
    quality_score:    float | None = None
    
    # --- Categorical filter fields (pre-indexed as KEYWORD) ---
    category:       str | None = None
    country_iso:    str | None = None
    currency:       str | None = None
    
    # --- Model provenance ---
    embedding_model:  str = "text-embedding-3-large"
    encoder_version:  str = "1.0.0"
    is_active:        bool = True
    
    # --- Entity-specific extended data ---
    entity_data: dict = field(default_factory=dict)

@dataclass
class VectorRecord:
    """Master record for a single entity's vector representations."""
    
    id:            UUID = field(default_factory=uuid4)
    entity_type:   EntityType = EntityType.PRODUCT
    entity_id:     UUID = field(default_factory=uuid4)
    collection:    str = ""
    
    # Embeddings
    text_vector:        list[float] = field(default_factory=list)  # 3072d
    structured_vector:  list[float] = field(default_factory=list)  # 512d
    fused_vector:       list[float] = field(default_factory=list)  # 1024d
    
    # Payload for Qdrant
    payload: VectorPayload = field(default_factory=VectorPayload)
    
    # Versioning / lifecycle
    embedding_model:   str = "text-embedding-3-large"
    encoder_version:   str = "1.0.0"
    indexed_at:        datetime | None = None
    source_updated_at: datetime | None = None
    is_active:         bool = True
    superseded_by:     UUID | None = None
```

### 4.2 Product-Specific Payload Fields

```python
# Stored in VectorPayload.entity_data for PRODUCT entities
PRODUCT_ENTITY_DATA = {
    "product_category":    str,    # top-level category
    "commodity_code":      str,    # UNSPSC 8-digit
    "unspsc_code":         str,    # full UNSPSC path
    "lifecycle_stage":     str,    # ACTIVE/NPI/MATURE/EOL
    "unit_of_measure":     str,    # EA/KG/M/M2/SET
    "unit_weight_kg":      float,  # weight in kg
    "primary_material":    str,    # primary material group
    "standards_list":      list,   # list of applicable standards
    "active_supplier_count": int,  # number of qualified suppliers
    "min_order_qty":       float,  # minimum order quantity
    "avg_quote_price_eur": float,  # average quoted price EUR
    "availability_score":  float,  # 0-1 availability index
}
```

### 4.3 Quote-Specific Payload Fields

```python
QUOTE_ENTITY_DATA = {
    "quote_number":         str,
    "supplier_id":          str,   # UUID string
    "supplier_name":        str,
    "supplier_tier":        int,   # 1/2/3
    "supplier_scorecard":   float,
    "material_id":          str,
    "material_name":        str,
    "unit_price_eur":       float,
    "floor_price_eur":      None,  # encrypted at rest, None in Qdrant payload
    "discount_pct":         float,
    "incoterm":             str,
    "payment_terms_days":   int,
    "validity_date":        str,   # ISO date
    "tooling_cost_eur":     float,
    "currency":             str,
}
```

### 4.4 Material-Specific Payload Fields

```python
MATERIAL_ENTITY_DATA = {
    "material_group":       str,
    "material_subgroup":    str,
    "iso_grade":            str,
    "astm_grade":           str,
    "din_grade":            str,
    "density_g_cm3":        float,
    "tensile_strength_mpa": float,
    "yield_strength_mpa":   float,
    "elongation_pct":       float,
    "hardness_hrc":         float,
    "alloy_group":          str,
    "lme_commodity_code":   str | None,
    "price_eur_per_kg":     float,
    "hazmat_class":         str,
    "standards_list":       list,
}
```

### 4.5 Process-Specific Payload Fields

```python
PROCESS_ENTITY_DATA = {
    "process_class":        str,   # CUT/MAC/FOR/JOI/ASS/FIN
    "process_type":         str,
    "machine_family":       str,
    "tolerance_it_grade":   int,   # IT grade number
    "surface_roughness_ra": float, # µm
    "cycle_time_sec":       float,
    "setup_time_min":       float,
    "cost_per_hour_eur":    float,
    "energy_cost_per_unit": float,
    "scrap_rate_pct":       float,
    "oee_score":            float,
    "automation_level":     float, # 0-1
    "compatible_materials": list,
}
```

### 4.6 Supplier-Specific Payload Fields

```python
SUPPLIER_ENTITY_DATA = {
    "supplier_type":        str,
    "country_iso":          str,   # pre-indexed as KEYWORD in Qdrant
    "years_in_business":    int,
    "scorecard_total":      float, # pre-indexed as quality_score
    "quality_score":        float,
    "delivery_score":       float,
    "price_score":          float,
    "risk_score":           float,
    "certifications":       list,  # ["ISO9001", "IATF16949", ...]
    "top_processes":        list,  # top 10 process capabilities
    "top_materials":        list,  # top 10 material capabilities
    "altman_z_score":       float,
    "paydex_score":         float,
    "geopolitical_risk_score": float,
}
```

### 4.7 Collection Naming Convention

```python
COLLECTIONS = {
    "products":    "scse_products_{env}",
    "quotes":      "scse_quotes_{env}",
    "materials":   "scse_materials_{env}",
    "processes":   "scse_processes_{env}",
    "suppliers":   "scse_suppliers_{env}",
    "crossentity": "scse_crossentity_{env}",
}

ENTITY_TYPE_TO_COLLECTION = {
    EntityType.PRODUCT:  "scse_products",
    EntityType.QUOTE:    "scse_quotes",
    EntityType.MATERIAL: "scse_materials",
    EntityType.PROCESS:  "scse_processes",
    EntityType.SUPPLIER: "scse_suppliers",
}

def get_collection_name(entity_type: EntityType, env: str = "prod") -> str:
    base = ENTITY_TYPE_TO_COLLECTION[entity_type]
    return f"{base}_{env}"
```

---

## Section 5: Qdrant Design

### 5.1 Why Qdrant (ADR-001)

Qdrant is selected as the primary online vector store based on five key capabilities:

| Capability | Qdrant | Pinecone | Weaviate | pgvector |
|-----------|--------|----------|---------|---------|
| Multi-vector per point | ✓ Named vectors | ✗ | ✓ | ✗ |
| Sparse + dense hybrid | ✓ Native | ✓ (sparse index) | ✗ | ✗ |
| Payload pre-filtering | ✓ HNSW+filter | ✓ | ✓ | ✗ |
| Clustering / sharding | ✓ Raft + shards | ✓ (managed) | ✓ | ✗ |
| Self-hosted / on-prem | ✓ | ✗ (SaaS) | ✓ | ✓ |
| gRPC API | ✓ | ✗ | ✗ | ✗ |
| INT8 quantization | ✓ | ✗ | ✗ | ✗ |
| Rust performance | ✓ | N/A | ✗ (Go) | ✗ |

### 5.2 Collection Configuration

```python
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, SparseVectorParams, Modifier,
    HnswConfigDiff, ScalarQuantization, ScalarQuantizationConfig, ScalarType,
    OptimizersConfigDiff, PayloadSchemaType,
)
import os

QDRANT_API_KEY = os.environ["QDRANT_API_KEY"]

client = QdrantClient(
    url="https://qdrant-cluster.internal:6333",
    api_key=QDRANT_API_KEY,
    grpc_port=6334,
    prefer_grpc=True,        # gRPC for lower latency
    timeout=30,
)

# Standard HNSW config for all collections
HNSW_CONFIG = HnswConfigDiff(
    m=32,                    # 32 bi-directional links per node (high recall)
    ef_construct=128,        # construction beam width (higher = better index quality)
    full_scan_threshold=10_000,  # below this, use exact scan instead of HNSW
    on_disk=False,           # keep HNSW graph in RAM for lowest latency
)

# INT8 scalar quantization — ~4× memory reduction, ~1% recall loss
QUANTIZATION = ScalarQuantization(
    scalar=ScalarQuantizationConfig(
        type=ScalarType.INT8,
        quantile=0.99,       # clip top 1% outliers before quantizing
        always_ram=True,     # keep quantized vectors in RAM
    )
)

OPTIMIZERS = OptimizersConfigDiff(
    indexing_threshold=20_000,   # start HNSW indexing after 20K vectors
    memmap_threshold=50_000,     # use mmap for segments > 50K vectors
)

def create_collection(client: QdrantClient, name: str, env: str = "prod") -> None:
    collection_name = f"{name}_{env}"
    client.create_collection(
        collection_name=collection_name,
        vectors_config={
            "text":       VectorParams(size=3072, distance=Distance.COSINE),
            "structured": VectorParams(size=512,  distance=Distance.EUCLID),
            "fused":      VectorParams(size=1024, distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "bm25": SparseVectorParams(modifier=Modifier.IDF),
        },
        hnsw_config=HNSW_CONFIG,
        quantization_config=QUANTIZATION,
        optimizers_config=OPTIMIZERS,
        on_disk_payload=False,       # payload in RAM for fast filter evaluation
        replication_factor=2,        # 2 copies of each shard
        write_consistency_factor=2,  # both replicas must confirm write
        shard_number=4,              # 4 shards per collection
    )
    _create_payload_indexes(client, collection_name)
    print(f"Created collection: {collection_name}")

def _create_payload_indexes(client: QdrantClient, collection_name: str) -> None:
    """Create payload indexes for efficient pre-filtering."""
    indexes = [
        ("entity_type",     PayloadSchemaType.KEYWORD),
        ("category",        PayloadSchemaType.KEYWORD),
        ("status",          PayloadSchemaType.KEYWORD),
        ("country_iso",     PayloadSchemaType.KEYWORD),
        ("currency",        PayloadSchemaType.KEYWORD),
        ("is_active",       PayloadSchemaType.BOOL),
        ("price_eur",       PayloadSchemaType.FLOAT),
        ("lead_time_days",  PayloadSchemaType.INTEGER),
        ("quality_score",   PayloadSchemaType.FLOAT),
    ]
    for field_name, schema_type in indexes:
        client.create_payload_index(collection_name, field_name, schema_type)

def bootstrap_all_collections(env: str = "prod") -> None:
    for name in ["scse_products", "scse_quotes", "scse_materials", "scse_processes", "scse_suppliers", "scse_crossentity"]:
        create_collection(client, name, env)
```

### 5.3 Upsert Patterns

```python
from qdrant_client.models import PointStruct, PointIdsList

class QdrantRepository:
    def __init__(self, client: QdrantClient):
        self.client = client
    
    async def upsert_entity(self, record: VectorRecord) -> None:
        """Upsert a single entity vector (synchronous — waits for indexing)."""
        point = PointStruct(
            id=str(record.entity_id),  # use entity_id as Qdrant point ID
            vector={
                "text":       record.text_vector,
                "structured": record.structured_vector,
                "fused":      record.fused_vector,
            },
            payload=asdict(record.payload),
        )
        await self.client.upsert(
            collection_name=record.collection,
            points=[point],
            wait=True,   # wait=True ensures point is searchable immediately
        )
    
    async def batch_upsert(
        self,
        records: list[VectorRecord],
        batch_size: int = 100,
    ) -> None:
        """Batch upsert with async=False (fire and forget for bulk indexing)."""
        from more_itertools import chunked
        for batch in chunked(records, batch_size):
            points = [
                PointStruct(
                    id=str(r.entity_id),
                    vector={
                        "text": r.text_vector,
                        "structured": r.structured_vector,
                        "fused": r.fused_vector,
                    },
                    payload=asdict(r.payload),
                )
                for r in batch
            ]
            await self.client.upsert(
                collection_name=batch[0].collection,
                points=points,
                wait=False,  # async for bulk performance
            )
    
    async def soft_delete(self, collection: str, entity_id: UUID) -> None:
        """Mark as inactive — vectors remain for audit, filtered from search."""
        await self.client.set_payload(
            collection_name=collection,
            payload={
                "is_active":  False,
                "status":     "DELETED",
                "deleted_at": datetime.utcnow().isoformat(),
            },
            points=[str(entity_id)],
        )
    
    async def get_point(self, collection: str, entity_id: UUID) -> dict | None:
        results = await self.client.retrieve(
            collection_name=collection,
            ids=[str(entity_id)],
            with_payload=True,
            with_vectors=False,
        )
        return results[0].payload if results else None
```

### 5.4 Search Patterns

```python
from qdrant_client.models import (
    Filter, FieldCondition, MatchValue, MatchAny, Range, MustNot,
    NamedVector, SearchParams, SparseVector, Prefetch, FusionQuery, Fusion,
    ScoredPoint,
)

class QdrantSearchEngine:
    def __init__(self, client: QdrantClient):
        self.client = client
    
    async def search_by_fused(
        self,
        collection: str,
        query_vector: list[float],
        filters: Filter | None = None,
        limit: int = 20,
        ef: int = 128,
        score_threshold: float = 0.70,
    ) -> list[ScoredPoint]:
        """Dense fused-vector ANN search."""
        active_filter = self._with_active_filter(filters)
        return await self.client.search(
            collection_name=collection,
            query_vector=NamedVector(name="fused", vector=query_vector),
            query_filter=active_filter,
            limit=limit,
            search_params=SearchParams(hnsw_ef=ef, exact=False),
            with_payload=True,
            score_threshold=score_threshold,
        )
    
    async def search_hybrid(
        self,
        collection: str,
        dense_vector: list[float],
        sparse_vector: SparseVector,
        filters: Filter | None = None,
        limit: int = 20,
        recall_factor: int = 3,
    ) -> list[ScoredPoint]:
        """Hybrid dense + sparse search with Qdrant native RRF fusion."""
        active_filter = self._with_active_filter(filters)
        return await self.client.query_points(
            collection_name=collection,
            prefetch=[
                Prefetch(
                    query=dense_vector,
                    using="fused",
                    limit=limit * recall_factor,
                ),
                Prefetch(
                    query=sparse_vector,
                    using="bm25",
                    limit=limit * recall_factor,
                ),
            ],
            query=FusionQuery(fusion=Fusion.RRF),
            limit=limit,
            query_filter=active_filter,
            with_payload=True,
        )
    
    async def recommend(
        self,
        collection: str,
        positive_ids: list[str],
        negative_ids: list[str] | None = None,
        using: str = "fused",
        limit: int = 10,
        filters: Filter | None = None,
    ) -> list[ScoredPoint]:
        """Collaborative filtering via Qdrant's recommend API."""
        return await self.client.recommend(
            collection_name=collection,
            positive=positive_ids,
            negative=negative_ids or [],
            using=using,
            limit=limit,
            query_filter=self._with_active_filter(filters),
            with_payload=True,
        )
    
    async def search_by_text_only(
        self,
        collection: str,
        query_vector: list[float],
        filters: Filter | None = None,
        limit: int = 20,
    ) -> list[ScoredPoint]:
        """Pure text-embedding search (3072d)."""
        return await self.client.search(
            collection_name=collection,
            query_vector=NamedVector(name="text", vector=query_vector),
            query_filter=self._with_active_filter(filters),
            limit=limit,
            with_payload=True,
        )
    
    @staticmethod
    def _with_active_filter(filters: Filter | None) -> Filter:
        """Always inject is_active=True filter."""
        active_condition = FieldCondition(key="is_active", match=MatchValue(value=True))
        if filters is None:
            return Filter(must=[active_condition])
        existing_must = filters.must or []
        return Filter(must=[*existing_must, active_condition])
```

### 5.5 Filter Builder

```python
@dataclass
class ProductSearchParams:
    categories: list[str] | None = None
    price_min: float | None = None
    price_max: float | None = None
    lifecycle_stages: list[str] | None = None
    min_supplier_count: int | None = None

@dataclass
class SupplierSearchParams:
    min_scorecard: float | None = None
    countries: list[str] | None = None
    certifications: list[str] | None = None
    supplier_types: list[str] | None = None
    max_lead_time_days: int | None = None

class FilterBuilder:
    def build_product_filter(self, params: ProductSearchParams) -> Filter | None:
        conditions = []
        if params.categories:
            conditions.append(FieldCondition(key="category", match=MatchAny(any=params.categories)))
        if params.price_min is not None or params.price_max is not None:
            conditions.append(FieldCondition(
                key="price_eur",
                range=Range(gte=params.price_min, lte=params.price_max),
            ))
        if params.lifecycle_stages:
            conditions.append(FieldCondition(
                key="entity_data.lifecycle_stage",
                match=MatchAny(any=params.lifecycle_stages),
            ))
        return Filter(must=conditions) if conditions else None
    
    def build_supplier_filter(self, params: SupplierSearchParams) -> Filter | None:
        conditions = []
        if params.min_scorecard is not None:
            conditions.append(FieldCondition(
                key="quality_score",
                range=Range(gte=params.min_scorecard),
            ))
        if params.countries:
            conditions.append(FieldCondition(
                key="country_iso",
                match=MatchAny(any=params.countries),
            ))
        if params.max_lead_time_days is not None:
            conditions.append(FieldCondition(
                key="lead_time_days",
                range=Range(lte=params.max_lead_time_days),
            ))
        return Filter(must=conditions) if conditions else None
```

### 5.6 Qdrant Cluster Configuration

**Cluster topology:**

```
┌──────────────────────────────────────────────┐
│              Qdrant 3-Node Cluster            │
│                                              │
│  ┌───────────┐  ┌───────────┐  ┌──────────┐ │
│  │  Node-1   │  │  Node-2   │  │  Node-3  │ │
│  │  (leader) │  │(follower) │  │(follower)│ │
│  │  Shard 1  │  │  Shard 1  │  │  Shard 3 │ │
│  │  Shard 2  │  │  Shard 3  │  │  Shard 4 │ │
│  │  Shard 4  │  │  Shard 2  │  │  Shard 2 │ │
│  └───────────┘  └───────────┘  └──────────┘ │
│       │               │               │      │
│       └───── Raft Consensus (p2p:6335) ──────┘
└──────────────────────────────────────────────┘
```

**Resource sizing per node:**

| Resource | Specification | Justification |
|----------|-------------|--------------|
| CPU | 32 vCPU | Parallel HNSW search threads |
| RAM | 128 GB | Vectors in RAM (on_disk=False) for 10M entities |
| NVMe SSD | 2 TB | Payload storage + WAL + snapshots |
| Network | 10 Gbps | Replication traffic during ingestion spikes |

**Storage estimate (10M entities, INT8 quantized):**

| Collection | Vectors (INT8) | Payload | Total |
|-----------|---------------|---------|-------|
| Products | 10M × (3072+512+1024) × 1 byte = 46 GB | ~10 GB | ~56 GB |
| Quotes | same | ~15 GB | ~61 GB |
| Materials | same | ~8 GB | ~54 GB |
| Processes | same | ~5 GB | ~51 GB |
| Suppliers | same | ~10 GB | ~56 GB |
| **Total (×repl_factor=2)** | | | **~556 GB** |

**Qdrant server config.yaml:**
```yaml
storage:
  storage_path: /var/lib/qdrant/storage
  snapshots_path: /var/lib/qdrant/snapshots
  on_disk_payload: false
  performance:
    max_search_threads: 0        # auto = CPU count
    max_optimization_threads: 2  # limit compaction impact on search

service:
  http_port: 6333
  grpc_port: 6334
  enable_tls: true
  max_request_size_mb: 256

cluster:
  enabled: true
  p2p:
    port: 6335
  consensus:
    tick_period_ms: 100

tls:
  cert:    /etc/ssl/qdrant/server.crt
  key:     /etc/ssl/qdrant/server.key
  ca_cert: /etc/ssl/qdrant/ca.crt
  verify_https_client_certificate: true

telemetry_disabled: false  # enable for Prometheus /metrics endpoint
```

**Snapshot schedule (cron via Airflow):**
```python
# Daily snapshot per collection to S3
async def snapshot_all_collections(env: str = "prod") -> None:
    for collection in COLLECTIONS.values():
        name = collection.format(env=env)
        snapshot_info = await client.create_snapshot(collection_name=name)
        s3_key = f"qdrant-snapshots/{name}/{datetime.utcnow().date()}/{snapshot_info.name}"
        await s3_client.upload_file(
            f"/var/lib/qdrant/snapshots/{name}/{snapshot_info.name}",
            bucket="ici-qdrant-backups",
            key=s3_key,
        )
        # Prune snapshots older than 7 days on S3
        await prune_old_snapshots(name, retention_days=7)
```
