"""
Section 1 — Ontologia + modele danych

Węzły (Node types):
  Material   — surowce, półprodukty, komponenty
  Process    — operacje produkcyjne, obróbka
  Supplier   — dostawcy, producenci
  Product    — wyroby gotowe, SKU
  Machine    — maszyny, urządzenia, linie
  Standard   — normy ISO/DIN/EN/ASTM
  Offer      — oferty handlowe (RFQ → Quote)

Relacje (Edge types):
  MADE_OF              Material → Material        (skład, zawartość)
  PROCESSED_BY         Material/Product → Process (obróbka)
  SUPPLIED_BY          Material/Product → Supplier (dostawca)
  CONFORMS_TO          Material/Product/Process → Standard
  SIMILAR_TO           Material ↔ Material        (substytuty)
  USED_IN              Material → Product          (BOM)
  REQUIRES             Process → Machine           (zasoby)
  PRODUCES             Process → Product/Material  (wynik)
  PRICED_IN            Offer → Material/Product    (cena)
  OFFERED_BY           Offer → Supplier
  COMPATIBLE_WITH      Machine ↔ Machine
  CERTIFIED_FOR        Supplier → Standard
  ALTERNATIVE_SUPPLIER Supplier ↔ Supplier        (alternatywa)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Node type enum
# ─────────────────────────────────────────────────────────────────────────────

class NodeType(str, Enum):
    MATERIAL  = "Material"
    PROCESS   = "Process"
    SUPPLIER  = "Supplier"
    PRODUCT   = "Product"
    MACHINE   = "Machine"
    STANDARD  = "Standard"
    OFFER     = "Offer"


class RelationType(str, Enum):
    MADE_OF              = "MADE_OF"
    PROCESSED_BY         = "PROCESSED_BY"
    SUPPLIED_BY          = "SUPPLIED_BY"
    CONFORMS_TO          = "CONFORMS_TO"
    SIMILAR_TO           = "SIMILAR_TO"
    USED_IN              = "USED_IN"
    REQUIRES             = "REQUIRES"
    PRODUCES             = "PRODUCES"
    PRICED_IN            = "PRICED_IN"
    OFFERED_BY           = "OFFERED_BY"
    COMPATIBLE_WITH      = "COMPATIBLE_WITH"
    CERTIFIED_FOR        = "CERTIFIED_FOR"
    ALTERNATIVE_SUPPLIER = "ALTERNATIVE_SUPPLIER"
    REPLACED_BY          = "REPLACED_BY"
    PART_OF              = "PART_OF"


# ─────────────────────────────────────────────────────────────────────────────
# Material ontology — klasy materiałów
# ─────────────────────────────────────────────────────────────────────────────

class MaterialClass(str, Enum):
    # Metale
    FERROUS_METAL      = "ferrous_metal"       # stale, żeliwa
    NON_FERROUS_METAL  = "non_ferrous_metal"   # aluminium, miedź, cynk
    PRECIOUS_METAL     = "precious_metal"
    # Tworzywa
    THERMOPLASTIC      = "thermoplastic"
    THERMOSET          = "thermoset"
    ELASTOMER          = "elastomer"
    # Drewno i papier
    SOLID_WOOD         = "solid_wood"
    ENGINEERED_WOOD    = "engineered_wood"
    PAPER_BOARD        = "paper_board"
    # Ceramika i szkło
    CERAMIC            = "ceramic"
    GLASS              = "glass"
    # Kompozyty
    COMPOSITE          = "composite"
    # Chemikalia
    CHEMICAL           = "chemical"
    ADHESIVE           = "adhesive"
    COATING            = "coating"
    # Opakowania
    PACKAGING          = "packaging"
    # Surowce energetyczne
    ENERGY_CARRIER     = "energy_carrier"


class MaterialForm(str, Enum):
    SHEET       = "sheet"
    COIL        = "coil"
    BAR         = "bar"
    TUBE        = "tube"
    WIRE        = "wire"
    POWDER      = "powder"
    GRANULE     = "granule"
    LIQUID      = "liquid"
    PROFILE     = "profile"
    CASTING     = "casting"
    FORGING     = "forging"
    PLATE       = "plate"
    STRIP       = "strip"
    BLANK       = "blank"


class ProcessType(str, Enum):
    # Obróbka skrawaniem
    TURNING      = "turning"
    MILLING      = "milling"
    DRILLING     = "drilling"
    GRINDING     = "grinding"
    HONING       = "honing"
    # Obróbka plastyczna
    STAMPING     = "stamping"
    FORGING      = "forging"
    DEEP_DRAWING = "deep_drawing"
    BENDING      = "bending"
    ROLLING      = "rolling"
    # Łączenie
    WELDING      = "welding"
    BRAZING      = "brazing"
    ADHESIVE_BONDING = "adhesive_bonding"
    RIVETING     = "riveting"
    # Obróbka cieplna
    HARDENING    = "hardening"
    ANNEALING    = "annealing"
    TEMPERING    = "tempering"
    NITRIDING    = "nitriding"
    # Powłoki
    COATING      = "coating"
    PAINTING     = "painting"
    GALVANIZING  = "galvanizing"
    ANODIZING    = "anodizing"
    # Odlewanie
    CASTING      = "casting"
    INJECTION_MOLDING = "injection_molding"
    # Addytywne
    ADDITIVE_MFG = "additive_manufacturing"
    # Montaż
    ASSEMBLY     = "assembly"
    TESTING      = "testing"
    INSPECTION   = "inspection"


class StandardBody(str, Enum):
    ISO   = "ISO"
    DIN   = "DIN"
    EN    = "EN"
    ASTM  = "ASTM"
    ASME  = "ASME"
    ANSI  = "ANSI"
    JIS   = "JIS"
    GB    = "GB"
    PN    = "PN"
    BS    = "BS"
    REACH = "REACH"
    ROHS  = "RoHS"


# ─────────────────────────────────────────────────────────────────────────────
# Node dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GraphNode:
    node_id:    str = field(default_factory=lambda: str(uuid4()))
    node_type:  NodeType = NodeType.MATERIAL
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tenant_id:  str = ""
    embedding:  list[float] | None = None   # vector (512-dim)
    metadata:   dict[str, Any] = field(default_factory=dict)


@dataclass
class MaterialNode(GraphNode):
    node_type:       NodeType = NodeType.MATERIAL
    # Identyfikacja
    name:            str = ""
    name_pl:         str = ""          # Polish name
    cas_number:      str | None = None # Chemical Abstracts Service
    ean:             str | None = None
    material_number: str | None = None # Internal ERP number
    # Klasyfikacja
    material_class:  MaterialClass | None = None
    material_form:   MaterialForm  | None = None
    sub_class:       str = ""          # e.g. "carbon_steel_low", "PA6", "PP-GF30"
    # Specyfikacja techniczna
    grade:           str = ""          # e.g. "S235JR", "EN AW-6082", "C45"
    density_kg_m3:   float | None = None
    tensile_mpa:     float | None = None
    yield_mpa:       float | None = None
    hardness_hb:     float | None = None
    melting_point_c: float | None = None
    conductivity_wm: float | None = None   # thermal W/(m·K)
    # Wymiary / postać
    thickness_mm:    float | None = None
    width_mm:        float | None = None
    length_mm:       float | None = None
    # Handel
    unit:            str = "kg"
    min_order_qty:   float | None = None
    lead_time_days:  int | None = None
    hs_code:         str | None = None    # Harmonized System tariff code
    # Flagi
    is_hazmat:       bool = False
    is_critical:     bool = False       # sole source / strategiczny
    is_recycled:     bool = False
    reach_compliant: bool = True
    rohs_compliant:  bool = True
    # Tagi wolnotekstowe
    tags:            list[str] = field(default_factory=list)


@dataclass
class ProcessNode(GraphNode):
    node_type:      NodeType = NodeType.PROCESS
    name:           str = ""
    process_type:   ProcessType | None = None
    description:    str = ""
    # Parametry procesu
    cycle_time_s:   float | None = None
    setup_time_min: float | None = None
    tolerance_mm:   float | None = None   # achievable tolerance
    surface_ra_um:  float | None = None   # roughness
    temp_min_c:     float | None = None
    temp_max_c:     float | None = None
    # Ekonomika
    cost_per_hour:  float | None = None
    cost_currency:  str = "EUR"
    # Środowisko
    co2_kg_per_unit: float | None = None
    energy_kwh_unit: float | None = None
    # Wymagania
    requires_certification: bool = False
    certification_body: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class SupplierNode(GraphNode):
    node_type:      NodeType = NodeType.SUPPLIER
    name:           str = ""
    legal_name:     str = ""
    duns:           str | None = None   # D-U-N-S number
    vat_id:         str | None = None
    # Adres
    country:        str = ""
    city:           str = ""
    postal_code:    str = ""
    address:        str = ""
    # Kontakt
    contact_email:  str = ""
    contact_phone:  str = ""
    website:        str = ""
    # Ocena
    quality_score:  float | None = None   # 0–100
    delivery_score: float | None = None
    price_score:    float | None = None
    risk_score:     float | None = None   # 0–1 (higher = riskier)
    # Certyfikaty
    iso_9001:       bool = False
    iso_14001:      bool = False
    iatf_16949:     bool = False
    iso_45001:      bool = False
    # Handlowe
    payment_terms:  str = ""   # "NET30", "2/10 NET30"
    currency:       str = "EUR"
    incoterms:      str = "DAP"
    min_order_eur:  float | None = None
    lead_time_days: int | None = None
    # Status
    approved:       bool = False
    blacklisted:    bool = False
    preferred:      bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class ProductNode(GraphNode):
    node_type:      NodeType = NodeType.PRODUCT
    name:           str = ""
    sku:            str = ""
    ean:            str | None = None
    description:    str = ""
    # Klasyfikacja
    product_family: str = ""
    product_line:   str = ""
    # Wymiary
    weight_kg:      float | None = None
    length_mm:      float | None = None
    width_mm:       float | None = None
    height_mm:      float | None = None
    # Ekonomika
    standard_cost:  float | None = None
    list_price:     float | None = None
    currency:       str = "EUR"
    # BOM
    bom_level:      int = 0     # 0 = finished, 1+ = sub-assembly
    # Status
    active:         bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class MachineNode(GraphNode):
    node_type:       NodeType = NodeType.MACHINE
    name:            str = ""
    machine_type:    str = ""    # lathe, milling_center, press, robot, etc.
    manufacturer:    str = ""
    model:           str = ""
    serial_number:   str | None = None
    year_of_mfg:     int | None = None
    # Parametry
    max_force_kn:    float | None = None
    max_rpm:         float | None = None
    travel_x_mm:     float | None = None
    travel_y_mm:     float | None = None
    travel_z_mm:     float | None = None
    power_kw:        float | None = None
    # Lokalizacja
    plant:           str = ""
    hall:            str = ""
    # OEE
    oee_target_pct:  float | None = None
    mtbf_hours:      float | None = None
    mttr_hours:      float | None = None
    # Status
    active:          bool = True
    tags: list[str] = field(default_factory=list)


@dataclass
class StandardNode(GraphNode):
    node_type:       NodeType = NodeType.STANDARD
    number:          str = ""    # e.g. "ISO 9001", "DIN EN 10025"
    title:           str = ""
    body:            StandardBody | None = None
    version:         str = ""    # publication year or revision
    scope:           str = ""    # brief scope description
    supersedes:      str | None = None   # replaced standard
    withdrawn:       bool = False
    tags: list[str] = field(default_factory=list)


@dataclass
class OfferNode(GraphNode):
    node_type:       NodeType = NodeType.OFFER
    rfq_id:          str | None = None
    offer_number:    str = ""
    # Cena
    unit_price:      float | None = None
    currency:        str = "EUR"
    unit:            str = "kg"
    min_qty:         float | None = None
    # Ważność
    valid_from:      datetime | None = None
    valid_until:     datetime | None = None
    # Warunki
    incoterms:       str = "DAP"
    payment_terms:   str = "NET30"
    lead_time_days:  int | None = None
    # Status
    status:          str = "active"   # active | expired | accepted | rejected
    tags: list[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Edge dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GraphEdge:
    edge_id:       str = field(default_factory=lambda: str(uuid4()))
    source_id:     str = ""
    target_id:     str = ""
    relation_type: RelationType = RelationType.SIMILAR_TO
    weight:        float = 1.0      # similarity / strength 0–1
    confidence:    float = 1.0      # how confident we are in this edge
    created_at:    datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    # Opcjonalne atrybuty relacji
    properties:    dict[str, Any] = field(default_factory=dict)
    # Przykład: MADE_OF → {fraction_pct: 85.0, optional: false}
    # PROCESSED_BY → {sequence: 1, mandatory: true}
    # SIMILAR_TO   → {similarity_score: 0.87, method: "embedding_cosine"}
    # SUPPLIED_BY  → {preferred: true, since: "2021-01"}
    # PRICED_IN    → {price: 4.20, currency: "EUR", unit: "kg", date: "2024-01-15"}


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API schemas
# ─────────────────────────────────────────────────────────────────────────────

class NodeCreate(BaseModel):
    node_type:  str
    name:       str
    tenant_id:  str = "tenant-demo"
    properties: dict[str, Any] = Field(default_factory=dict)


class EdgeCreate(BaseModel):
    source_id:     str
    target_id:     str
    relation_type: str
    weight:        float = 1.0
    properties:    dict[str, Any] = Field(default_factory=dict)


class GraphSearchRequest(BaseModel):
    query:         str
    node_types:    list[str] = Field(default_factory=list)
    limit:         int = Field(default=20, ge=1, le=200)
    use_semantic:  bool = True
    use_fulltext:  bool = True
    filters:       dict[str, Any] = Field(default_factory=dict)


class PathRequest(BaseModel):
    from_id:       str
    to_id:         str
    max_depth:     int = Field(default=5, ge=1, le=10)
    relation_types: list[str] = Field(default_factory=list)


class RecommendRequest(BaseModel):
    node_id:       str
    relation_type: str = "SIMILAR_TO"
    limit:         int = Field(default=10, ge=1, le=50)
    strategy:      str = "hybrid"   # "embedding" | "graph" | "hybrid"
    filters:       dict[str, Any] = Field(default_factory=dict)


class NodeOut(BaseModel):
    node_id:    str
    node_type:  str
    name:       str
    properties: dict[str, Any] = Field(default_factory=dict)
    score:      float | None = None


class PathOut(BaseModel):
    path:        list[dict[str, Any]]
    length:      int
    total_weight: float


class RecommendationOut(BaseModel):
    node_id:    str
    node_type:  str
    name:       str
    score:      float
    reason:     str
    path:       list[str] | None = None
