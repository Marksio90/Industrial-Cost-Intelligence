# BOM Engine — Sekcje 1–5: Domain Model, Multi-level BOM, Variant Management, Material Substitution, Cost Roll-up

---

## 1. BOM Domain Model

### 1.1 Koncepcja i zakres

Bill of Materials Engine (BOME) zarządza hierarchiczną strukturą materiałową produktów
na potrzeby wyceny kosztów w platformie Industrial Cost Intelligence. BOME jest źródłem
prawdy o składzie produktu — każda zmiana struktury propaguje się do CEE (Cost Estimation
Engine) i CLS (Continuous Learning System).

```
┌──────────────────────────────────────────────────────────────────────┐
│                         BOM Engine (BOME)                            │
│                                                                      │
│  BOMHeader                                                           │
│  ├── bom_id (UUID)                                                   │
│  ├── product_code (VARCHAR)          ← Part number / drawing number  │
│  ├── revision (VARCHAR)              ← np. "A", "B", "03"           │
│  ├── bom_type (ENUM)                 ← ENGINEERING / MFG / SERVICE  │
│  ├── status (ENUM)                   ← DRAFT/RELEASED/OBSOLETE      │
│  ├── effective_from / effective_to   ← temporal validity            │
│  └── BOMLine[]                                                       │
│       ├── line_id (UUID)                                             │
│       ├── position (INTEGER)         ← sort order                   │
│       ├── item_code (VARCHAR)        ← material master key          │
│       ├── quantity (NUMERIC)                                         │
│       ├── unit_of_measure (VARCHAR)  ← kg, pc, m, m², L            │
│       ├── reference_designator       ← np. "R1,R2,C5"              │
│       ├── find_number (VARCHAR)      ← balloon number in drawing    │
│       ├── phantom (BOOLEAN)          ← phantom assembly flag        │
│       ├── BOMLineType (ENUM)         ← COMPONENT/SUBASSEMBLY/       │
│       │                                 FASTENER/RAWMAT/SEMI/TOOLING│
│       ├── alternates[]               ← MaterialSubstitute[]         │
│       └── cost_override (NUMERIC)    ← manual cost override         │
└──────────────────────────────────────────────────────────────────────┘
```

### 1.2 Typy BOM

| BOM Type | Opis | Producent |
|----------|------|-----------|
| `ENGINEERING_BOM` (EBOM) | Projektowa — CAD/PLM | Inżynieria / CAD |
| `MANUFACTURING_BOM` (MBOM) | Produkcyjna — rozwinięta, z operacjami | Technolog |
| `SERVICE_BOM` (SBOM) | Serwisowa — FRU (Field Replaceable Units) | Serwis |
| `COSTING_BOM` (CBOM) | Kosztowa — syntetyczna, do wyceny CEE | BOME (auto) |
| `PLANNING_BOM` (PBOM) | Planistyczna — z % udziałem wariantów | Planowanie |

### 1.3 Kluczowe klasy domenowe

```python
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid

UTC = timezone.utc


class BOMType(str, Enum):
    ENGINEERING = "ENGINEERING"
    MANUFACTURING = "MANUFACTURING"
    SERVICE = "SERVICE"
    COSTING = "COSTING"
    PLANNING = "PLANNING"


class BOMStatus(str, Enum):
    DRAFT = "DRAFT"
    IN_REVIEW = "IN_REVIEW"
    RELEASED = "RELEASED"
    FROZEN = "FROZEN"       # zamrożony do celów kosztowych (nie zmienia się)
    OBSOLETE = "OBSOLETE"
    SUPERSEDED = "SUPERSEDED"  # zastąpiony nową rewizją


class BOMLineType(str, Enum):
    COMPONENT = "COMPONENT"         # gotowy komponent kupowany
    SUBASSEMBLY = "SUBASSEMBLY"     # podzespół (ma swój BOM)
    RAW_MATERIAL = "RAW_MATERIAL"   # surowiec (stal, aluminium...)
    SEMI_FINISHED = "SEMI_FINISHED" # półprodukt
    FASTENER = "FASTENER"           # śruba, nit, nakrętka
    PACKAGING = "PACKAGING"         # opakowanie
    TOOLING = "TOOLING"             # oprzyrządowanie (amortyzowane)
    PHANTOM = "PHANTOM"             # phantom assembly (rozwijany automatycznie)
    REFERENCE = "REFERENCE"         # dokument referencyjny (bez kosztu)


class UnitOfMeasure(str, Enum):
    PC = "PC"       # sztuka
    KG = "KG"       # kilogram
    M = "M"         # metr liniowy
    M2 = "M2"       # metr kwadratowy
    M3 = "M3"       # metr sześcienny
    L = "L"         # litr
    SET = "SET"     # komplet
    LOT = "LOT"     # partia


@dataclass
class MaterialSubstitute:
    """Alternatywny materiał/komponent dla pozycji BOM."""
    substitute_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    item_code: str = ""
    priority: int = 1               # 1 = primary substitute
    interchangeable: bool = True    # True = drop-in replacement, False = wymaga zmiany procesu
    quantity_factor: Decimal = Decimal("1.0")  # mnożnik ilości (inny gramatura?)
    cost_delta_pct: Optional[Decimal] = None   # różnica kosztowa vs oryginał
    valid_from: Optional[date] = None
    valid_until: Optional[date] = None
    approval_status: str = "PENDING"  # PENDING / APPROVED / REJECTED
    approved_by: Optional[str] = None
    note: Optional[str] = None


@dataclass
class BOMLine:
    """Pojedyncza pozycja BOM."""
    line_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    bom_id: str = ""
    parent_line_id: Optional[str] = None      # None = pozycja root level
    position: int = 10                         # krok 10 (SAP-style)
    item_code: str = ""                        # klucz w material master
    item_description: Optional[str] = None
    line_type: BOMLineType = BOMLineType.COMPONENT
    quantity: Decimal = Decimal("1")
    uom: UnitOfMeasure = UnitOfMeasure.PC
    scrap_factor_pct: Decimal = Decimal("0")   # % odpadu (0–100)
    lead_time_days: Optional[int] = None
    make_or_buy: str = "BUY"                   # BUY / MAKE / CONSIGNED
    reference_designator: Optional[str] = None
    find_number: Optional[str] = None
    phantom: bool = False
    critical_item: bool = False                # long lead-time / single source
    cost_override_eur: Optional[Decimal] = None  # ręczne nadpisanie kosztu
    alternates: list[MaterialSubstitute] = field(default_factory=list)
    # CAD link
    cad_reference: Optional[str] = None        # np. PLM item ID
    # Computed (filled by cost roll-up)
    rolled_unit_cost_eur: Optional[Decimal] = None
    rolled_total_cost_eur: Optional[Decimal] = None
    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def effective_quantity(self) -> Decimal:
        """Ilość z uwzględnieniem odpadu: qty / (1 - scrap_factor/100)."""
        if self.scrap_factor_pct > 0:
            return self.quantity / (1 - self.scrap_factor_pct / 100)
        return self.quantity


@dataclass
class BOMHeader:
    """Nagłówek BOM — reprezentuje jeden produkt w jednej rewizji."""
    bom_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    product_code: str = ""
    product_description: Optional[str] = None
    revision: str = "A"
    bom_type: BOMType = BOMType.ENGINEERING
    status: BOMStatus = BOMStatus.DRAFT
    effective_from: date = field(default_factory=date.today)
    effective_to: Optional[date] = None
    production_location: Optional[str] = None  # docelowa lokalizacja produkcji
    annual_volume: Optional[int] = None         # planowany wolumen roczny
    base_quantity: Decimal = Decimal("1")       # ilość bazowa (1 szt, 100 szt...)
    currency: str = "EUR"
    source_bom_id: Optional[str] = None         # parent (EBOM → MBOM derivation)
    cad_document_id: Optional[str] = None       # PLM doc ID
    lines: list[BOMLine] = field(default_factory=list)
    # Change management
    change_order_id: Optional[str] = None
    released_by: Optional[str] = None
    released_at: Optional[datetime] = None
    # Metadata
    created_by: str = "system"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def get_top_level_lines(self) -> list[BOMLine]:
        return [l for l in self.lines if l.parent_line_id is None]

    def get_children(self, parent_line_id: str) -> list[BOMLine]:
        return [l for l in self.lines if l.parent_line_id == parent_line_id]
```

### 1.4 Material Master (uproszczony, referencja)

```python
@dataclass
class MaterialMaster:
    """Uproszczony rekord materiału — pełny w ERP/PDM."""
    item_code: str
    description: str
    material_group: str        # np. "STEEL_FLAT", "ALUMINUM_EXTRUSION"
    base_uom: UnitOfMeasure
    weight_kg_per_uom: Optional[Decimal] = None
    density_g_cm3: Optional[Decimal] = None
    list_price_eur: Optional[Decimal] = None   # cennik zakupowy
    last_po_price_eur: Optional[Decimal] = None
    preferred_supplier: Optional[str] = None
    lead_time_days: Optional[int] = None
    min_order_qty: Optional[Decimal] = None
    is_critical: bool = False
    is_hazardous: bool = False
    rohs_compliant: bool = True
    reach_compliant: bool = True
```

---

## 2. Multi-level BOM Structure

### 2.1 Reprezentacja drzewa

BOM Engine przechowuje strukturę wielopoziomową jako **adjacency list** w PostgreSQL
(parent_line_id → line_id), z rekurencyjnymi CTE do rozwijania i `ltree` dla szybkiego
traversal.

```
Przykład: Obudowa aluminiowa (3 poziomy)

BOM: HOUSING-001 rev.B  [MBOM, RELEASED]
├── POS 10: FRAME-001         (Subassembly, MAKE, qty=1)
│   ├── POS 10: ALU-PLATE-3mm (Raw Material, BUY, qty=0.85 m²)
│   ├── POS 20: BOLT-M6x20   (Fastener, BUY, qty=8 PC)
│   └── POS 30: GASKET-EPDM  (Component, BUY, qty=1 PC)
├── POS 20: PCB-CTRL-v4       (Component, BUY, qty=1 PC)
│   └── [phantom BOM → rozwijany tylko dla kosztów]
├── POS 30: COVER-001         (Subassembly, MAKE, qty=1)
│   ├── POS 10: ALU-PLATE-2mm (Raw Material, BUY, qty=0.40 m²)
│   └── POS 20: SCREW-M4x8   (Fastener, BUY, qty=4 PC)
└── POS 40: LABEL-001         (Reference, qty=1)  ← brak kosztu
```

### 2.2 BOMTreeService

```python
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional, Iterator
import asyncpg


@dataclass
class BOMNode:
    """Węzeł drzewa BOM z dziećmi."""
    line: BOMLine
    children: list[BOMNode] = field(default_factory=list)
    depth: int = 0
    path: str = ""          # ltree path: "10.20.30"

    @property
    def is_leaf(self) -> bool:
        return not self.children

    @property
    def is_phantom(self) -> bool:
        return self.line.phantom

    def iter_all(self) -> Iterator[BOMNode]:
        """DFS iterator przez całe poddrzewo."""
        yield self
        for child in self.children:
            yield from child.iter_all()

    def iter_leaves(self) -> Iterator[BOMNode]:
        """Tylko liście (rzeczywiste komponenty do zakupu/produkcji)."""
        if self.is_leaf and not self.is_phantom:
            yield self
        for child in self.children:
            yield from child.iter_leaves()


class BOMTreeService:
    """Buduje i odpytuje wielopoziomowe drzewo BOM."""

    MAX_DEPTH = 20   # zabezpieczenie przed cyklami
    MAX_NODES = 5000

    def __init__(self, db_pool: asyncpg.Pool):
        self.db = db_pool

    async def build_tree(self, bom_id: str, expand_phantoms: bool = True) -> list[BOMNode]:
        """
        Buduje pełne drzewo BOM za pomocą rekurencyjnego CTE.
        expand_phantoms: jeśli True, phantom assemblies są rozwijane in-place.
        """
        rows = await self._fetch_all_lines(bom_id)
        if not rows:
            raise BOMNotFoundError(f"BOM {bom_id} not found or empty")

        nodes_by_id: dict[str, BOMNode] = {}
        for row in rows:
            line = self._row_to_bom_line(row)
            node = BOMNode(
                line=line,
                depth=row["depth"],
                path=row["path"],
            )
            nodes_by_id[line.line_id] = node

        # Buduj drzewo
        roots: list[BOMNode] = []
        for node in nodes_by_id.values():
            parent_id = node.line.parent_line_id
            if parent_id is None:
                roots.append(node)
            elif parent_id in nodes_by_id:
                nodes_by_id[parent_id].children.append(node)

        # Sortuj dzieci po pozycji
        for node in nodes_by_id.values():
            node.children.sort(key=lambda n: n.line.position)

        if expand_phantoms:
            await self._expand_phantoms(roots, depth=0)

        return sorted(roots, key=lambda n: n.line.position)

    async def _expand_phantoms(self, nodes: list[BOMNode], depth: int) -> None:
        """Zastąp phantom nodes ich dziećmi."""
        if depth >= self.MAX_DEPTH:
            return
        for node in nodes:
            if node.is_phantom and node.is_leaf:
                # Wczytaj sub-BOM dla phantom item
                phantom_bom = await self._find_released_bom(node.line.item_code)
                if phantom_bom:
                    subtree = await self.build_tree(phantom_bom.bom_id, expand_phantoms=False)
                    node.children = subtree
            await self._expand_phantoms(node.children, depth + 1)

    async def _fetch_all_lines(self, bom_id: str) -> list[asyncpg.Record]:
        async with self.db.acquire() as conn:
            return await conn.fetch(
                """
                WITH RECURSIVE bom_tree AS (
                    -- Base: top-level lines
                    SELECT
                        bl.*,
                        0 AS depth,
                        CAST(bl.position::TEXT AS TEXT) AS path
                    FROM bome.bom_lines bl
                    WHERE bl.bom_id = $1
                      AND bl.parent_line_id IS NULL
                      AND bl.is_active = TRUE

                    UNION ALL

                    -- Recursive: children
                    SELECT
                        child.*,
                        parent.depth + 1,
                        parent.path || '.' || child.position::TEXT
                    FROM bome.bom_lines child
                    JOIN bom_tree parent ON parent.line_id = child.parent_line_id
                    WHERE child.is_active = TRUE
                      AND parent.depth < $2
                )
                SELECT * FROM bom_tree
                ORDER BY path
                """,
                bom_id, self.MAX_DEPTH,
            )

    async def get_where_used(self, item_code: str) -> list[dict]:
        """Gdzie-jest-używany: znajdź wszystkie BOM zawierające dany item."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT
                    bh.bom_id,
                    bh.product_code,
                    bh.revision,
                    bh.bom_type,
                    bh.status,
                    bl.quantity,
                    bl.uom,
                    bl.depth_level
                FROM bome.bom_headers bh
                JOIN bome.bom_lines bl ON bl.bom_id = bh.bom_id
                WHERE bl.item_code = $1
                  AND bh.status IN ('RELEASED', 'FROZEN')
                  AND bl.is_active = TRUE
                ORDER BY bh.product_code, bh.revision
                """,
                item_code,
            )
        return [dict(r) for r in rows]

    async def get_indented_bom(self, bom_id: str) -> list[dict]:
        """Flat lista z wcięciami (do eksportu / UI)."""
        roots = await self.build_tree(bom_id)
        result = []

        def _flatten(nodes: list[BOMNode], depth: int) -> None:
            for node in nodes:
                result.append({
                    "depth": depth,
                    "indent": "  " * depth,
                    "position": node.line.position,
                    "item_code": node.line.item_code,
                    "description": node.line.item_description,
                    "quantity": float(node.line.effective_quantity),
                    "uom": node.line.uom.value,
                    "line_type": node.line.line_type.value,
                    "make_or_buy": node.line.make_or_buy,
                    "scrap_pct": float(node.line.scrap_factor_pct),
                    "path": node.path,
                })
                _flatten(node.children, depth + 1)

        _flatten(roots, 0)
        return result

    async def get_total_component_count(self, bom_id: str) -> int:
        """Całkowita liczba unikalnych komponentów (flattened)."""
        async with self.db.acquire() as conn:
            return await conn.fetchval(
                """
                WITH RECURSIVE bom_tree AS (
                    SELECT line_id, item_code, parent_line_id, 0 AS depth
                    FROM bome.bom_lines
                    WHERE bom_id = $1 AND parent_line_id IS NULL AND is_active = TRUE
                    UNION ALL
                    SELECT c.line_id, c.item_code, c.parent_line_id, p.depth + 1
                    FROM bome.bom_lines c
                    JOIN bom_tree p ON p.line_id = c.parent_line_id
                    WHERE c.is_active = TRUE AND p.depth < 20
                )
                SELECT COUNT(DISTINCT item_code) FROM bom_tree
                """,
                bom_id,
            )

    async def detect_circular_reference(self, bom_id: str, item_code: str) -> bool:
        """Sprawdź czy dodanie item_code do bom_id nie tworzy cyklu."""
        # Pobierz wszystkie BOMs nadrzędne
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                WITH RECURSIVE parent_boms AS (
                    SELECT bh.product_code
                    FROM bome.bom_headers bh
                    WHERE bh.bom_id = $1
                    UNION ALL
                    SELECT bh2.product_code
                    FROM bome.bom_headers bh2
                    JOIN bome.bom_lines bl ON bl.bom_id = bh2.bom_id
                    JOIN bome.bom_headers bh3 ON bh3.product_code = bl.item_code
                    JOIN parent_boms pb ON pb.product_code = bh3.product_code
                )
                SELECT product_code FROM parent_boms
                """,
                bom_id,
            )
        ancestor_codes = {r["product_code"] for r in rows}
        return item_code in ancestor_codes

    @staticmethod
    def _row_to_bom_line(row: asyncpg.Record) -> BOMLine:
        return BOMLine(
            line_id=str(row["line_id"]),
            bom_id=str(row["bom_id"]),
            parent_line_id=str(row["parent_line_id"]) if row["parent_line_id"] else None,
            position=row["position"],
            item_code=row["item_code"],
            item_description=row.get("item_description"),
            line_type=BOMLineType(row["line_type"]),
            quantity=Decimal(str(row["quantity"])),
            uom=UnitOfMeasure(row["uom"]),
            scrap_factor_pct=Decimal(str(row.get("scrap_factor_pct") or "0")),
            phantom=row.get("phantom", False),
            make_or_buy=row.get("make_or_buy", "BUY"),
            reference_designator=row.get("reference_designator"),
            find_number=row.get("find_number"),
            critical_item=row.get("critical_item", False),
            cost_override_eur=Decimal(str(row["cost_override_eur"])) if row.get("cost_override_eur") else None,
        )

    async def _find_released_bom(self, product_code: str) -> Optional[BOMHeader]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT bom_id FROM bome.bom_headers
                WHERE product_code = $1
                  AND status IN ('RELEASED', 'FROZEN')
                  AND (effective_to IS NULL OR effective_to >= CURRENT_DATE)
                ORDER BY effective_from DESC LIMIT 1
                """,
                product_code,
            )
        if row:
            return BOMHeader(bom_id=str(row["bom_id"]), product_code=product_code)
        return None


class BOMNotFoundError(Exception):
    pass
```

---

## 3. Variant Management

### 3.1 Model wariantów

```
Product Family: HOUSING-SERIES-X
├── VARIANT: HOUSING-X-SMALL  (100×80×50mm)
│   └── BOM: HOUSING-X-SMALL-001 rev.A
├── VARIANT: HOUSING-X-MEDIUM (150×120×80mm)
│   └── BOM: HOUSING-X-MEDIUM-001 rev.B
└── VARIANT: HOUSING-X-LARGE  (200×160×100mm)
    └── BOM: HOUSING-X-LARGE-001 rev.A

Podejście: Generic BOM + Feature/Option (150% BOM)
```

### 3.2 Typy wariantów

| Typ | Opis | Przykład |
|-----|------|---------|
| **Discrete variant** | Odrębny BOM per wariant | HOUSING-S, HOUSING-M, HOUSING-L |
| **Configurable BOM** | 150% BOM z regułami selekcji | Opcje: kolor, materiał, certyfikat |
| **Modular BOM** | Moduły kombinowane wg zamówienia | Base + Option A + Option B |
| **Planning BOM** | % split między wariantami | 60% S, 30% M, 10% L |

### 3.3 ConfigurableBOM

```python
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
import uuid


@dataclass
class VariantOption:
    """Pojedyncza opcja konfiguracyjna (Feature Value)."""
    option_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    feature_name: str = ""      # np. "COLOR", "MATERIAL_GRADE", "CERTIFICATION"
    option_code: str = ""       # np. "RAL9005", "GRADE_A", "ATEX"
    option_description: str = ""
    is_default: bool = False
    is_mandatory: bool = False


@dataclass
class VariantRule:
    """
    Reguła selekcji pozycji BOM dla danej konfiguracji.
    condition_expr: wyrażenie Python-safe evaluowane na dict opcji.
    Przykład: "COLOR == 'BLACK' and MATERIAL_GRADE in ('A', 'B')"
    """
    rule_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    line_id: str = ""               # dotyczy której pozycji BOM
    condition_expr: str = "True"    # domyślnie zawsze aktywna
    quantity_expr: Optional[str] = None  # ilość zależna od config, np. "1.2 if SIZE=='L' else 1.0"
    item_override: Optional[str] = None  # podmień item_code na inny


@dataclass
class ProductFamily:
    """Rodzina produktów z wariantami."""
    family_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    family_code: str = ""
    family_description: str = ""
    features: list[str] = field(default_factory=list)      # ["SIZE", "COLOR", "GRADE"]
    options: list[VariantOption] = field(default_factory=list)
    generic_bom_id: Optional[str] = None    # 150% BOM
    rules: list[VariantRule] = field(default_factory=list)
    variant_boms: dict[str, str] = field(default_factory=dict)  # config_hash → bom_id


class VariantConfigurator:
    """
    Konfiguruje BOM dla konkretnej kombinacji opcji.
    Używany przez CEE do wyceny wariantu bez tworzenia odrębnego BOM.
    """

    def __init__(self, tree_service: BOMTreeService):
        self.tree_service = tree_service

    async def configure(
        self,
        family: ProductFamily,
        selected_options: dict[str, str],  # feature → option_code
    ) -> list[BOMNode]:
        """
        Zwraca drzewo BOM po zastosowaniu reguł dla wybranych opcji.
        selected_options: {"SIZE": "L", "COLOR": "BLACK", "GRADE": "A"}
        """
        if not family.generic_bom_id:
            raise ValueError(f"Family {family.family_code} has no generic BOM")

        all_nodes = await self.tree_service.build_tree(family.generic_bom_id)
        return self._apply_rules(all_nodes, family.rules, selected_options)

    def _apply_rules(
        self,
        nodes: list[BOMNode],
        rules: list[VariantRule],
        options: dict[str, str],
    ) -> list[BOMNode]:
        rules_by_line = {r.line_id: r for r in rules}
        result = []
        for node in nodes:
            rule = rules_by_line.get(node.line.line_id)
            if rule:
                try:
                    include = eval(rule.condition_expr, {}, options)  # noqa: S307
                except Exception:
                    include = True
                if not include:
                    continue
                if rule.quantity_expr:
                    try:
                        node.line.quantity = Decimal(str(eval(rule.quantity_expr, {}, options)))  # noqa: S307
                    except Exception:
                        pass
                if rule.item_override:
                    node.line.item_code = rule.item_override

            node.children = self._apply_rules(node.children, rules, options)
            result.append(node)
        return result

    def get_config_hash(self, options: dict[str, str]) -> str:
        """Deterministyczny hash konfiguracji (do cache'owania)."""
        import hashlib, json
        key = json.dumps(sorted(options.items()), sort_keys=True)
        return hashlib.sha256(key.encode()).hexdigest()[:16]
```

### 3.4 PlanningBOM (percentowy split)

```python
@dataclass
class PlanningBOMEntry:
    """Wpis w Planning BOM — wariant z % udziałem."""
    item_code: str          # kod wariantu
    percentage: Decimal     # % udziału w ogólnym popycie (suma = 100%)
    bom_id: str             # docelowy BOM wariantu
    notes: Optional[str] = None


class PlanningBOMService:
    """Oblicza średni kosztu per planning BOM (ważona średnia)."""

    async def compute_weighted_cost(
        self,
        entries: list[PlanningBOMEntry],
        annual_volume: int,
        cost_rollup_service: "CostRollupService",
    ) -> Decimal:
        total = Decimal("0")
        for entry in entries:
            variant_volume = int(annual_volume * entry.percentage / 100)
            cost = await cost_rollup_service.rollup(entry.bom_id, volume=variant_volume)
            total += cost.total_cost_eur * entry.percentage / 100
        return total
```

---

## 4. Material Substitution

### 4.1 MaterialSubstitutionEngine

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Optional
import uuid

logger = logging.getLogger(__name__)


class SubstitutionReason(str, Enum):
    COST_REDUCTION = "COST_REDUCTION"       # tańszy odpowiednik
    SUPPLY_SHORTAGE = "SUPPLY_SHORTAGE"     # brak dostępności
    TECHNICAL_EQUIV = "TECHNICAL_EQUIV"     # równoważność techniczna
    REGULATORY = "REGULATORY"               # wymóg regulacyjny (RoHS, REACH)
    LEAD_TIME = "LEAD_TIME"                 # krótszy czas dostawy
    SINGLE_SOURCE = "SINGLE_SOURCE"         # eliminacja single-source ryzyka
    QUALIFICATION = "QUALIFICATION"         # kwalifikacja nowego dostawcy


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CONDITIONALLY_APPROVED = "CONDITIONALLY_APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


@dataclass
class SubstitutionRequest:
    """Wniosek o zmianę materiału (Material Deviation Request)."""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    original_item_code: str = ""
    substitute_item_code: str = ""
    reason: SubstitutionReason = SubstitutionReason.COST_REDUCTION
    affected_bom_ids: list[str] = field(default_factory=list)
    requested_by: str = ""
    requested_at: str = ""
    valid_from: Optional[str] = None
    valid_until: Optional[str] = None
    quantity_factor: Decimal = Decimal("1.0")
    cost_impact_eur: Optional[Decimal] = None
    quality_impact: str = "NONE"    # NONE / MINOR / MAJOR
    process_change_required: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    approvals: list[dict] = field(default_factory=list)
    notes: Optional[str] = None


@dataclass
class SubstitutionResult:
    """Wynik walidacji i kosztowy substitucji."""
    request_id: str
    original_cost_eur: Decimal
    substitute_cost_eur: Decimal
    cost_delta_eur: Decimal         # positive = droższy, negative = tańszy
    cost_delta_pct: Decimal
    is_interchangeable: bool        # drop-in bez zmian procesu
    affected_boms: list[str]
    warnings: list[str]
    blocking_issues: list[str]
    can_substitute: bool            # True jeśli brak blocking issues


class MaterialSubstitutionEngine:
    """Zarządza logiką walidacji i zatwierdzania substytutów materiałowych."""

    REQUIRED_APPROVERS = {
        SubstitutionReason.REGULATORY: ["quality", "engineering"],
        SubstitutionReason.TECHNICAL_EQUIV: ["engineering"],
        SubstitutionReason.COST_REDUCTION: ["procurement"],
        SubstitutionReason.SUPPLY_SHORTAGE: ["procurement", "planning"],
    }

    def __init__(self, db_pool, cee_client, sie_client):
        self.db = db_pool
        self.cee = cee_client   # CEE do przeliczenia kosztu
        self.sie = sie_client   # SIE do oceny dostawcy

    async def evaluate_substitution(
        self,
        request: SubstitutionRequest,
    ) -> SubstitutionResult:
        warnings: list[str] = []
        blocking: list[str] = []

        # 1. Sprawdź dostępność substytutu
        avail = await self._check_item_availability(request.substitute_item_code)
        if not avail["available"]:
            blocking.append(f"Substitute {request.substitute_item_code} not in material master")

        # 2. Pobierz koszty (original vs substitute)
        orig_cost = await self._get_item_cost(request.original_item_code)
        sub_cost = await self._get_item_cost(request.substitute_item_code)
        sub_cost_adj = sub_cost * request.quantity_factor
        delta_eur = sub_cost_adj - orig_cost
        delta_pct = (delta_eur / orig_cost * 100) if orig_cost else Decimal("0")

        if delta_pct > 20:
            warnings.append(f"Substitute is {delta_pct:.1f}% more expensive")

        # 3. Sprawdź zgodność regulacyjną
        reg_ok = await self._check_regulatory(request.substitute_item_code)
        if not reg_ok["rohs"]:
            blocking.append("Substitute not RoHS compliant")
        if not reg_ok["reach"]:
            warnings.append("REACH compliance unconfirmed for substitute")

        # 4. Sprawdź process impact
        if request.process_change_required:
            warnings.append(
                "Process change required — engineering review mandatory"
            )

        # 5. Sprawdź ocenę dostawcy substytutu (via SIE)
        supplier_score = await self._get_preferred_supplier_score(
            request.substitute_item_code
        )
        if supplier_score is not None and supplier_score < 0.60:
            warnings.append(
                f"Preferred supplier score = {supplier_score:.2f} (threshold 0.60)"
            )

        return SubstitutionResult(
            request_id=request.request_id,
            original_cost_eur=orig_cost,
            substitute_cost_eur=sub_cost_adj,
            cost_delta_eur=delta_eur,
            cost_delta_pct=delta_pct,
            is_interchangeable=not request.process_change_required,
            affected_boms=request.affected_bom_ids,
            warnings=warnings,
            blocking_issues=blocking,
            can_substitute=len(blocking) == 0,
        )

    async def apply_substitution(
        self,
        request: SubstitutionRequest,
        applied_by: str,
    ) -> int:
        """Zastosuj substytut do wszystkich wskazanych BOM lines."""
        if request.approval_status != ApprovalStatus.APPROVED:
            raise PermissionError("Substitution not yet approved")

        updated = 0
        async with self.db.acquire() as conn:
            async with conn.transaction():
                for bom_id in request.affected_bom_ids:
                    result = await conn.execute(
                        """
                        UPDATE bome.bom_lines SET
                            item_code = $2,
                            quantity = quantity * $3,
                            substitution_request_id = $4,
                            updated_at = NOW()
                        WHERE bom_id = $1
                          AND item_code = $5
                          AND is_active = TRUE
                        """,
                        bom_id, request.substitute_item_code,
                        request.quantity_factor, request.request_id,
                        request.original_item_code,
                    )
                    updated += int(result.split()[-1])

                # Zapisz alternate na starych liniach
                await conn.execute(
                    """
                    INSERT INTO bome.substitution_history (
                        request_id, original_item, substitute_item,
                        reason, applied_by, applied_at, affected_boms
                    ) VALUES ($1,$2,$3,$4,$5,NOW(),$6)
                    """,
                    request.request_id, request.original_item_code,
                    request.substitute_item_code, request.reason.value,
                    applied_by, request.affected_bom_ids,
                )
        return updated

    async def get_approved_substitutes(self, item_code: str) -> list[dict]:
        """Lista zatwierdzonych substytutów dla danego materiału."""
        async with self.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT
                    ms.substitute_id,
                    ms.substitute_item_code,
                    ms.priority,
                    ms.interchangeable,
                    ms.quantity_factor,
                    ms.cost_delta_pct,
                    ms.valid_until,
                    ms.note
                FROM bome.material_substitutes ms
                WHERE ms.original_item_code = $1
                  AND ms.approval_status = 'APPROVED'
                  AND (ms.valid_until IS NULL OR ms.valid_until >= CURRENT_DATE)
                ORDER BY ms.priority, ms.cost_delta_pct
                """,
                item_code,
            )
        return [dict(r) for r in rows]

    async def _check_item_availability(self, item_code: str) -> dict:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT item_code, is_active FROM bome.material_master WHERE item_code=$1",
                item_code,
            )
        return {"available": row is not None and row["is_active"]}

    async def _get_item_cost(self, item_code: str) -> Decimal:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT COALESCE(last_po_price_eur, list_price_eur, 0) AS price
                FROM bome.material_master WHERE item_code=$1
                """,
                item_code,
            )
        return Decimal(str(row["price"])) if row else Decimal("0")

    async def _check_regulatory(self, item_code: str) -> dict:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT rohs_compliant, reach_compliant FROM bome.material_master WHERE item_code=$1",
                item_code,
            )
        if row:
            return {"rohs": row["rohs_compliant"], "reach": row["reach_compliant"]}
        return {"rohs": False, "reach": False}

    async def _get_preferred_supplier_score(self, item_code: str) -> Optional[float]:
        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT preferred_supplier FROM bome.material_master WHERE item_code=$1",
                item_code,
            )
        if not row or not row["preferred_supplier"]:
            return None
        try:
            score_data = await self.sie.get_supplier_score(row["preferred_supplier"])
            return score_data.get("overall_score")
        except Exception:
            return None
```

---

## 5. Cost Roll-up

### 5.1 Algorytm roll-up

```
Cost Roll-up Algorithm (bottom-up, DFS):

Dla każdego węzła liścia:
  unit_cost = cost_override ?? material_price ?? CEE_estimate
  total_cost = unit_cost × effective_quantity

Dla każdego węzła wewnętrznego (subassembly):
  subassembly_cost = Σ(child.total_cost)
  process_cost = manufacturing_cost(subassembly)  ← z CEE
  total_cost = (subassembly_cost + process_cost) × effective_quantity

Root:
  bom_cost = Σ(root_child.total_cost)
  overhead = bom_cost × overhead_rate
  total_cost = bom_cost + overhead + tooling_amortization
```

### 5.2 CostRollupService

```python
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class LineRollupResult:
    line_id: str
    item_code: str
    line_type: str
    quantity: Decimal
    effective_quantity: Decimal
    unit_cost_eur: Decimal
    total_cost_eur: Decimal
    cost_source: str          # "OVERRIDE" / "PRICE_MASTER" / "CEE" / "ZERO"
    children_cost_eur: Decimal = Decimal("0")
    process_cost_eur: Decimal = Decimal("0")
    confidence: Optional[float] = None
    depth: int = 0


@dataclass
class BOMRollupResult:
    bom_id: str
    product_code: str
    revision: str
    volume: int
    currency: str

    # Breakdown
    material_cost_eur: Decimal = Decimal("0")
    process_cost_eur: Decimal = Decimal("0")
    overhead_cost_eur: Decimal = Decimal("0")
    tooling_amortization_eur: Decimal = Decimal("0")
    total_cost_eur: Decimal = Decimal("0")
    total_cost_with_scrap_eur: Decimal = Decimal("0")

    # Metadata
    line_results: list[LineRollupResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rollup_confidence: float = 1.0
    rolled_at: Optional[str] = None
    bom_revision_used: str = ""

    @property
    def material_pct(self) -> Decimal:
        if self.total_cost_eur == 0:
            return Decimal("0")
        return self.material_cost_eur / self.total_cost_eur * 100

    @property
    def process_pct(self) -> Decimal:
        if self.total_cost_eur == 0:
            return Decimal("0")
        return self.process_cost_eur / self.total_cost_eur * 100


OVERHEAD_RATES = {
    # production_location → overhead rate
    "DE": Decimal("0.25"),
    "PL": Decimal("0.18"),
    "CZ": Decimal("0.19"),
    "RO": Decimal("0.15"),
    "CN": Decimal("0.12"),
    "IN": Decimal("0.10"),
    "MX": Decimal("0.14"),
    "US": Decimal("0.22"),
    "TR": Decimal("0.13"),
    "HU": Decimal("0.17"),
}

DEFAULT_OVERHEAD_RATE = Decimal("0.20")


class CostRollupService:
    """Bottom-up cost roll-up przez całe drzewo BOM."""

    def __init__(self, tree_service: BOMTreeService, cee_client, price_service):
        self.tree_service = tree_service
        self.cee = cee_client           # CEE API do szacowania procesu
        self.price_service = price_service  # pobieranie cen materiałów

    async def rollup(
        self,
        bom_id: str,
        volume: int = 1,
        include_tooling: bool = True,
        use_cee_for_process: bool = True,
    ) -> BOMRollupResult:
        """Główna metoda roll-up. Zwraca BOMRollupResult."""
        from datetime import datetime, timezone

        header = await self._get_bom_header(bom_id)
        roots = await self.tree_service.build_tree(bom_id, expand_phantoms=True)

        overhead_rate = OVERHEAD_RATES.get(
            header.get("production_location") or "",
            DEFAULT_OVERHEAD_RATE,
        )

        result = BOMRollupResult(
            bom_id=bom_id,
            product_code=header["product_code"],
            revision=header["revision"],
            volume=volume,
            currency="EUR",
            bom_revision_used=header["revision"],
            rolled_at=datetime.now(timezone.utc).isoformat(),
        )

        # Równoległa prefetch cen dla wszystkich liści
        all_leaves = [n for root in roots for n in root.iter_leaves()]
        await self._prefetch_prices([n.line.item_code for n in all_leaves])

        # Rekurencyjny roll-up
        line_results: list[LineRollupResult] = []
        total_material = Decimal("0")
        total_process = Decimal("0")

        for root in roots:
            lr = await self._rollup_node(root, volume, use_cee_for_process, result.warnings, depth=0)
            line_results.append(lr)
            # Podział na material/process
            total_material += self._extract_material_cost(lr)
            total_process += self._extract_process_cost(lr)

        # Tooling amortization
        tooling_cost = Decimal("0")
        if include_tooling:
            tooling_cost = await self._compute_tooling_amortization(bom_id, volume)

        raw_total = sum(lr.total_cost_eur for lr in line_results)
        overhead = raw_total * overhead_rate

        result.material_cost_eur = total_material
        result.process_cost_eur = total_process
        result.overhead_cost_eur = overhead
        result.tooling_amortization_eur = tooling_cost
        result.total_cost_eur = raw_total + overhead + tooling_cost
        result.total_cost_with_scrap_eur = result.total_cost_eur  # scrap już w effective_qty
        result.line_results = line_results
        result.rollup_confidence = self._compute_confidence(line_results)

        # Persist do bome.cost_rollups
        await self._persist_rollup(result)
        return result

    async def _rollup_node(
        self,
        node: BOMNode,
        volume: int,
        use_cee: bool,
        warnings: list[str],
        depth: int,
    ) -> LineRollupResult:
        line = node.line

        # Skip reference items
        if line.line_type == BOMLineType.REFERENCE:
            return LineRollupResult(
                line_id=line.line_id, item_code=line.item_code,
                line_type=line.line_type.value,
                quantity=line.quantity, effective_quantity=line.effective_quantity,
                unit_cost_eur=Decimal("0"), total_cost_eur=Decimal("0"),
                cost_source="ZERO", depth=depth,
            )

        if node.is_leaf or line.line_type in (BOMLineType.COMPONENT, BOMLineType.RAW_MATERIAL, BOMLineType.FASTENER):
            # Liść — pobierz cenę materiału
            unit_cost, source = await self._get_unit_cost(line, warnings)
            total = unit_cost * line.effective_quantity
            return LineRollupResult(
                line_id=line.line_id, item_code=line.item_code,
                line_type=line.line_type.value,
                quantity=line.quantity, effective_quantity=line.effective_quantity,
                unit_cost_eur=unit_cost, total_cost_eur=total,
                cost_source=source, depth=depth,
            )
        else:
            # Subassembly — recursive
            child_tasks = [
                self._rollup_node(child, volume, use_cee, warnings, depth + 1)
                for child in node.children
            ]
            child_results = await asyncio.gather(*child_tasks)
            children_cost = sum(lr.total_cost_eur for lr in child_results)

            # Process cost dla subassembly (z CEE)
            process_cost = Decimal("0")
            if use_cee and line.make_or_buy == "MAKE":
                try:
                    process_cost = await self._get_process_cost_from_cee(
                        line.item_code, volume
                    )
                except Exception as e:
                    warnings.append(f"CEE unavailable for {line.item_code}: {e}")

            unit_cost = children_cost + process_cost
            total = unit_cost * line.effective_quantity

            lr = LineRollupResult(
                line_id=line.line_id, item_code=line.item_code,
                line_type=line.line_type.value,
                quantity=line.quantity, effective_quantity=line.effective_quantity,
                unit_cost_eur=unit_cost, total_cost_eur=total,
                cost_source="ROLLUP", depth=depth,
                children_cost_eur=children_cost,
                process_cost_eur=process_cost,
            )
            # Append children results
            for cr in child_results:
                lr  # line_results dołączone na wyższym poziomie
            return lr

    async def _get_unit_cost(
        self, line: BOMLine, warnings: list[str]
    ) -> tuple[Decimal, str]:
        if line.cost_override_eur is not None:
            return line.cost_override_eur, "OVERRIDE"

        price = await self.price_service.get_price(
            line.item_code, line.uom.value
        )
        if price is not None:
            return price, "PRICE_MASTER"

        # Fallback do CEE estimate
        try:
            cee_price = await self.cee.estimate_material_cost(line.item_code)
            return Decimal(str(cee_price)), "CEE"
        except Exception:
            warnings.append(
                f"No price for {line.item_code} — defaulting to 0"
            )
            return Decimal("0"), "ZERO"

    async def _get_process_cost_from_cee(self, item_code: str, volume: int) -> Decimal:
        result = await self.cee.estimate_process_cost(
            item_code=item_code, annual_volume=volume
        )
        return Decimal(str(result.get("process_cost_eur", 0)))

    async def _compute_tooling_amortization(self, bom_id: str, volume: int) -> Decimal:
        async with self.tree_service.db.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT t.tooling_cost_eur, t.amortization_qty
                FROM bome.tooling_items t
                WHERE t.bom_id = $1 AND t.is_active = TRUE
                """,
                bom_id,
            )
        total = Decimal("0")
        for row in rows:
            amort_qty = row["amortization_qty"] or 1
            per_unit = Decimal(str(row["tooling_cost_eur"])) / Decimal(str(amort_qty))
            total += per_unit
        return total

    @staticmethod
    def _compute_confidence(line_results: list[LineRollupResult]) -> float:
        if not line_results:
            return 1.0
        sources = [lr.cost_source for lr in line_results]
        zero_count = sources.count("ZERO")
        cee_count = sources.count("CEE")
        total = len(sources)
        confidence = 1.0 - (zero_count * 0.5 + cee_count * 0.15) / total
        return max(0.0, min(1.0, confidence))

    @staticmethod
    def _extract_material_cost(lr: LineRollupResult) -> Decimal:
        if lr.line_type in ("COMPONENT", "RAW_MATERIAL", "FASTENER", "SEMI_FINISHED", "PACKAGING"):
            return lr.total_cost_eur
        return lr.children_cost_eur

    @staticmethod
    def _extract_process_cost(lr: LineRollupResult) -> Decimal:
        return lr.process_cost_eur

    async def _prefetch_prices(self, item_codes: list[str]) -> None:
        await self.price_service.prefetch(list(set(item_codes)))

    async def _get_bom_header(self, bom_id: str) -> dict:
        async with self.tree_service.db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM bome.bom_headers WHERE bom_id=$1", bom_id
            )
        if not row:
            raise BOMNotFoundError(bom_id)
        return dict(row)

    async def _persist_rollup(self, result: BOMRollupResult) -> None:
        import json
        async with self.tree_service.db.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO bome.cost_rollups (
                    bom_id, volume, material_cost_eur, process_cost_eur,
                    overhead_cost_eur, tooling_amortization_eur, total_cost_eur,
                    rollup_confidence, warnings, rolled_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,NOW())
                ON CONFLICT (bom_id, volume)
                DO UPDATE SET
                    material_cost_eur = EXCLUDED.material_cost_eur,
                    total_cost_eur = EXCLUDED.total_cost_eur,
                    rollup_confidence = EXCLUDED.rollup_confidence,
                    rolled_at = NOW()
                """,
                result.bom_id, result.volume,
                result.material_cost_eur, result.process_cost_eur,
                result.overhead_cost_eur, result.tooling_amortization_eur,
                result.total_cost_eur, result.rollup_confidence,
                json.dumps(result.warnings),
            )
```

### 5.3 Progi jakości roll-up

| Źródło kosztu | Waga konfidencji | Opis |
|---------------|-----------------|------|
| `OVERRIDE` | 1.00 | Ręcznie wprowadzony koszt |
| `PRICE_MASTER` | 0.95 | Cena z master danych (ostatnie PO / cennik) |
| `CEE` | 0.75 | Szacunek z Cost Estimation Engine |
| `ZERO` | 0.00 | Brak ceny — wpływa krytycznie na konfidencję |

**Progi:**
- `confidence ≥ 0.90` → HIGH — wiarygodna wycena
- `0.70–0.89` → MEDIUM — użyteczna, ale z zastrzeżeniami
- `0.50–0.69` → LOW — wiele pozycji bez cen
- `< 0.50` → INDICATIVE — wycena orientacyjna

### 5.4 Raport roll-up (przykład)

```
BOM: HOUSING-001 rev.B  [Volume: 500 pc/year, Location: PL]
─────────────────────────────────────────────────────────
  Component                    Qty    UoM   Unit €   Total €   Source
  ├── FRAME-001 (SUBASSEMBLY)   1     PC   148.20   148.20    ROLLUP
  │   ├── ALU-PLATE-3mm         0.85  m²    42.00    39.97    PRICE_MASTER
  │   ├── BOLT-M6x20            8     PC     0.18     1.44    PRICE_MASTER
  │   ├── GASKET-EPDM           1     PC     3.20     3.20    PRICE_MASTER
  │   └── [Process cost MAKE]                        103.59   CEE
  ├── PCB-CTRL-v4               1     PC    85.00    85.00    PRICE_MASTER
  └── COVER-001 (SUBASSEMBLY)   1     PC    52.40    52.40    ROLLUP
      ├── ALU-PLATE-2mm         0.40  m²    38.00    16.15    PRICE_MASTER
      ├── SCREW-M4x8            4     PC     0.06     0.24    PRICE_MASTER
      └── [Process cost MAKE]                         36.01   CEE
─────────────────────────────────────────────────────────
  Material cost:               144.00 EUR   50.2%
  Process cost:                139.60 EUR   48.7%
  Overhead (18%):               51.91 EUR
  Tooling (amort. /500):         4.00 EUR
─────────────────────────────────────────────────────────
  TOTAL:                       339.51 EUR/pc
  Confidence: HIGH (0.93)
```
