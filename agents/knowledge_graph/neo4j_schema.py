"""
Section 4 — Neo4j Schema

Definicje:
  - Constraints (unikalność, istnienie)
  - Indexes (wyszukiwanie, pełnotekstowe, wektorowe)
  - Node labels i właściwości
  - Relationship types i właściwości

Neo4j 5.x — używa składni CREATE CONSTRAINT / CREATE INDEX.
Wektorowy index wymaga Neo4j 5.11+ (ANN / HNSW).
Fulltext index obsługuje wielopolowe wyszukiwanie.
"""
from __future__ import annotations

from typing import Any

# ─────────────────────────────────────────────────────────────────────────────
# DDL — Constraints
# ─────────────────────────────────────────────────────────────────────────────

CONSTRAINTS: list[str] = [
    # ── Node uniqueness ───────────────────────────────────────────────────────
    "CREATE CONSTRAINT material_node_id IF NOT EXISTS FOR (n:Material) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT process_node_id  IF NOT EXISTS FOR (n:Process)  REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT supplier_node_id IF NOT EXISTS FOR (n:Supplier) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT product_node_id  IF NOT EXISTS FOR (n:Product)  REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT machine_node_id  IF NOT EXISTS FOR (n:Machine)  REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT standard_node_id IF NOT EXISTS FOR (n:Standard) REQUIRE n.node_id IS UNIQUE",
    "CREATE CONSTRAINT offer_node_id    IF NOT EXISTS FOR (n:Offer)    REQUIRE n.node_id IS UNIQUE",

    # ── Mandatory property existence ──────────────────────────────────────────
    "CREATE CONSTRAINT material_name IF NOT EXISTS FOR (n:Material) REQUIRE n.name IS NOT NULL",
    "CREATE CONSTRAINT supplier_name IF NOT EXISTS FOR (n:Supplier) REQUIRE n.name IS NOT NULL",
    "CREATE CONSTRAINT product_sku   IF NOT EXISTS FOR (n:Product)  REQUIRE n.sku  IS NOT NULL",
    "CREATE CONSTRAINT standard_num  IF NOT EXISTS FOR (n:Standard) REQUIRE n.number IS NOT NULL",

    # ── Relationship constraints ──────────────────────────────────────────────
    "CREATE CONSTRAINT similar_to_weight IF NOT EXISTS FOR ()-[r:SIMILAR_TO]-() REQUIRE r.weight IS NOT NULL",
]

# ─────────────────────────────────────────────────────────────────────────────
# DDL — Indexes
# ─────────────────────────────────────────────────────────────────────────────

INDEXES: list[str] = [
    # ── B-tree indexes ────────────────────────────────────────────────────────
    "CREATE INDEX material_class_idx    IF NOT EXISTS FOR (n:Material) ON (n.material_class)",
    "CREATE INDEX material_grade_idx    IF NOT EXISTS FOR (n:Material) ON (n.grade)",
    "CREATE INDEX material_tenant_idx   IF NOT EXISTS FOR (n:Material) ON (n.tenant_id)",
    "CREATE INDEX material_critical_idx IF NOT EXISTS FOR (n:Material) ON (n.is_critical)",
    "CREATE INDEX supplier_country_idx  IF NOT EXISTS FOR (n:Supplier) ON (n.country)",
    "CREATE INDEX supplier_approved_idx IF NOT EXISTS FOR (n:Supplier) ON (n.approved)",
    "CREATE INDEX supplier_risk_idx     IF NOT EXISTS FOR (n:Supplier) ON (n.risk_score)",
    "CREATE INDEX product_family_idx    IF NOT EXISTS FOR (n:Product)  ON (n.product_family)",
    "CREATE INDEX product_active_idx    IF NOT EXISTS FOR (n:Product)  ON (n.active)",
    "CREATE INDEX offer_status_idx      IF NOT EXISTS FOR (n:Offer)    ON (n.status)",
    "CREATE INDEX offer_currency_idx    IF NOT EXISTS FOR (n:Offer)    ON (n.currency)",
    "CREATE INDEX process_type_idx      IF NOT EXISTS FOR (n:Process)  ON (n.process_type)",
    "CREATE INDEX standard_body_idx     IF NOT EXISTS FOR (n:Standard) ON (n.body)",

    # ── Composite indexes ─────────────────────────────────────────────────────
    "CREATE INDEX mat_tenant_class_idx  IF NOT EXISTS FOR (n:Material) ON (n.tenant_id, n.material_class)",
    "CREATE INDEX offer_supplier_status IF NOT EXISTS FOR (n:Offer)    ON (n.status, n.valid_until)",

    # ── Fulltext indexes (multi-property text search) ─────────────────────────
    """
    CREATE FULLTEXT INDEX material_ft_idx IF NOT EXISTS
    FOR (n:Material)
    ON EACH [n.name, n.name_pl, n.grade, n.sub_class, n.tags]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}
    """,
    """
    CREATE FULLTEXT INDEX supplier_ft_idx IF NOT EXISTS
    FOR (n:Supplier)
    ON EACH [n.name, n.legal_name, n.city, n.country, n.tags]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}
    """,
    """
    CREATE FULLTEXT INDEX product_ft_idx IF NOT EXISTS
    FOR (n:Product)
    ON EACH [n.name, n.sku, n.description, n.product_family, n.tags]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}
    """,
    """
    CREATE FULLTEXT INDEX process_ft_idx IF NOT EXISTS
    FOR (n:Process)
    ON EACH [n.name, n.description, n.tags]
    OPTIONS {indexConfig: {`fulltext.analyzer`: 'standard-no-stop-words'}}
    """,
    """
    CREATE FULLTEXT INDEX standard_ft_idx IF NOT EXISTS
    FOR (n:Standard)
    ON EACH [n.number, n.title, n.scope, n.tags]
    """,
    """
    CREATE FULLTEXT INDEX global_ft_idx IF NOT EXISTS
    FOR (n:Material|Process|Supplier|Product|Machine|Standard|Offer)
    ON EACH [n.name, n.tags]
    """,

    # ── Vector index (Neo4j 5.11+ HNSW ANN) ─────────────────────────────────
    """
    CREATE VECTOR INDEX material_embedding_idx IF NOT EXISTS
    FOR (n:Material) ON (n.embedding)
    OPTIONS {indexConfig: {
      `vector.dimensions`: 512,
      `vector.similarity_function`: 'cosine'
    }}
    """,
    """
    CREATE VECTOR INDEX product_embedding_idx IF NOT EXISTS
    FOR (n:Product) ON (n.embedding)
    OPTIONS {indexConfig: {
      `vector.dimensions`: 512,
      `vector.similarity_function`: 'cosine'
    }}
    """,
    """
    CREATE VECTOR INDEX supplier_embedding_idx IF NOT EXISTS
    FOR (n:Supplier) ON (n.embedding)
    OPTIONS {indexConfig: {
      `vector.dimensions`: 512,
      `vector.similarity_function`: 'cosine'
    }}
    """,
]

# ─────────────────────────────────────────────────────────────────────────────
# Property schema documentation (not enforced by Neo4j OSS, documents intent)
# ─────────────────────────────────────────────────────────────────────────────

NODE_PROPERTY_SCHEMA: dict[str, dict[str, str]] = {
    "Material": {
        "node_id":         "STRING  — UUID4",
        "name":            "STRING  — canonical EN name",
        "name_pl":         "STRING  — Polish name",
        "tenant_id":       "STRING  — multi-tenant isolation",
        "material_class":  "STRING  — MaterialClass enum",
        "material_form":   "STRING  — MaterialForm enum",
        "grade":           "STRING  — e.g. S355J2, EN AW-6082",
        "sub_class":       "STRING  — taxonomy code e.g. MAT.MET.FE.CS",
        "cas_number":      "STRING? — CAS registry number",
        "hs_code":         "STRING? — Harmonized System code",
        "density_kg_m3":   "FLOAT?  — density",
        "tensile_mpa":     "FLOAT?  — tensile strength",
        "yield_mpa":       "FLOAT?  — yield strength",
        "hardness_hb":     "FLOAT?  — Brinell hardness",
        "unit":            "STRING  — base unit (kg, m, pcs, m²)",
        "min_order_qty":   "FLOAT?  — MOQ in base unit",
        "lead_time_days":  "INT?    — typical procurement lead time",
        "is_hazmat":       "BOOL",
        "is_critical":     "BOOL    — strategic / sole source",
        "reach_compliant": "BOOL",
        "rohs_compliant":  "BOOL",
        "tags":            "LIST<STRING>",
        "embedding":       "LIST<FLOAT> — 512-dim vector",
        "created_at":      "DATETIME",
        "updated_at":      "DATETIME",
    },
    "Supplier": {
        "node_id":         "STRING",
        "name":            "STRING",
        "duns":            "STRING? — D-U-N-S",
        "vat_id":          "STRING?",
        "country":         "STRING  — ISO 3166-1 alpha-2",
        "city":            "STRING",
        "quality_score":   "FLOAT?  — 0–100",
        "delivery_score":  "FLOAT?  — 0–100",
        "risk_score":      "FLOAT?  — 0–1",
        "iso_9001":        "BOOL",
        "iatf_16949":      "BOOL",
        "payment_terms":   "STRING  — NET30, etc.",
        "incoterms":       "STRING  — DAP, EXW, etc.",
        "approved":        "BOOL",
        "preferred":       "BOOL",
        "blacklisted":     "BOOL",
        "embedding":       "LIST<FLOAT>",
    },
    "Product": {
        "node_id":         "STRING",
        "name":            "STRING",
        "sku":             "STRING  — UNIQUE per tenant",
        "ean":             "STRING?",
        "product_family":  "STRING",
        "bom_level":       "INT     — 0=finished",
        "standard_cost":   "FLOAT?",
        "currency":        "STRING",
        "active":          "BOOL",
        "embedding":       "LIST<FLOAT>",
    },
    "Process": {
        "node_id":         "STRING",
        "name":            "STRING",
        "process_type":    "STRING  — ProcessType enum",
        "cycle_time_s":    "FLOAT?",
        "tolerance_mm":    "FLOAT?",
        "cost_per_hour":   "FLOAT?",
        "co2_kg_per_unit": "FLOAT?",
    },
    "Machine": {
        "node_id":         "STRING",
        "name":            "STRING",
        "machine_type":    "STRING",
        "manufacturer":    "STRING",
        "model":           "STRING",
        "power_kw":        "FLOAT?",
        "oee_target_pct":  "FLOAT?",
        "plant":           "STRING",
        "active":          "BOOL",
    },
    "Standard": {
        "node_id":         "STRING",
        "number":          "STRING  — e.g. ISO 9001:2015",
        "title":           "STRING",
        "body":            "STRING  — ISO/DIN/EN/ASTM",
        "version":         "STRING",
        "withdrawn":       "BOOL",
    },
    "Offer": {
        "node_id":         "STRING",
        "rfq_id":          "STRING?",
        "offer_number":    "STRING",
        "unit_price":      "FLOAT?",
        "currency":        "STRING",
        "unit":            "STRING",
        "valid_until":     "DATETIME?",
        "status":          "STRING  — active|expired|accepted|rejected",
        "lead_time_days":  "INT?",
    },
}

RELATIONSHIP_SCHEMA: dict[str, dict[str, str]] = {
    "MADE_OF": {
        "fraction_pct": "FLOAT?  — weight/volume fraction %",
        "optional":     "BOOL    — false = mandatory composition",
        "notes":        "STRING?",
    },
    "PROCESSED_BY": {
        "sequence":     "INT     — operation order in routing",
        "mandatory":    "BOOL",
        "setup_time_min": "FLOAT?",
        "cycle_time_s": "FLOAT?",
    },
    "SUPPLIED_BY": {
        "preferred":    "BOOL",
        "price_eur":    "FLOAT?  — last known price",
        "lead_days":    "INT?",
        "since":        "STRING? — YYYY-MM first supply",
        "contract_id":  "STRING?",
    },
    "SIMILAR_TO": {
        "weight":       "FLOAT   — similarity 0–1 (cosine / engineered)",
        "method":       "STRING  — embedding_cosine|property_match|expert",
        "grade_compat": "BOOL    — can substitute without requalification",
    },
    "USED_IN": {
        "quantity":     "FLOAT",
        "unit":         "STRING",
        "bom_position": "STRING? — BOM line number",
        "scrap_pct":    "FLOAT?  — expected scrap/waste %",
    },
    "REQUIRES": {
        "min_power_kw": "FLOAT?",
        "tooling_id":   "STRING?",
    },
    "CONFORMS_TO": {
        "since":        "STRING? — compliance date",
        "certificate":  "STRING? — certificate number",
        "audited_by":   "STRING?",
    },
    "PRICED_IN": {
        "unit_price":   "FLOAT",
        "currency":     "STRING",
        "date":         "DATE",
    },
    "OFFERED_BY": {
        "primary":      "BOOL",
    },
    "CERTIFIED_FOR": {
        "cert_number":  "STRING?",
        "valid_until":  "DATE?",
        "scope":        "STRING?",
    },
    "ALTERNATIVE_SUPPLIER": {
        "reason":       "STRING? — why alternative was added",
        "risk_level":   "STRING  — LOW|MEDIUM|HIGH",
    },
    "REPLACED_BY": {
        "since":        "DATE?",
        "reason":       "STRING?",
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Schema initializer
# ─────────────────────────────────────────────────────────────────────────────

async def apply_schema(driver: Any) -> dict[str, list[str]]:
    """
    Applies all constraints and indexes to a running Neo4j instance.
    Returns dict of {applied: [...], failed: [...]}.
    """
    applied = []
    failed  = []
    async with driver.session() as session:
        for ddl in CONSTRAINTS + INDEXES:
            stmt = " ".join(ddl.split())   # normalise whitespace
            try:
                await session.run(stmt)
                applied.append(stmt[:80])
            except Exception as exc:
                failed.append({"stmt": stmt[:80], "error": str(exc)})
    return {"applied": applied, "failed": failed}


def schema_summary() -> dict[str, Any]:
    return {
        "constraints": len(CONSTRAINTS),
        "indexes":     len(INDEXES),
        "node_labels": list(NODE_PROPERTY_SCHEMA.keys()),
        "rel_types":   list(RELATIONSHIP_SCHEMA.keys()),
    }
