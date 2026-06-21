# Supplier Offer Parser — Sekcje 5–8

## 5. Unit Conversion Engine

### 5.1 Domain model jednostek

```python
from enum import Enum
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional


class UnitDimension(str, Enum):
    MASS        = "MASS"
    LENGTH      = "LENGTH"
    AREA        = "AREA"
    VOLUME      = "VOLUME"
    COUNT       = "COUNT"
    TIME        = "TIME"
    CURRENCY    = "CURRENCY"
    PACKAGING   = "PACKAGING"
    DIMENSIONLESS = "DIMENSIONLESS"


@dataclass
class Unit:
    code: str                        # canonical code: "kg", "pcs", "EUR"
    aliases: list[str]               # all accepted forms
    dimension: UnitDimension
    to_base_factor: Decimal          # 1 unit = N base units
    base_unit: str                   # base unit of dimension
    per_quantity: Optional[Decimal]  # for "per 100 pcs" pricing: 100


@dataclass
class ConversionResult:
    original_value: Decimal
    original_unit: str
    converted_value: Decimal
    target_unit: str
    factor: Decimal
    is_exact: bool
    confidence: float
    warning: Optional[str] = None
```

### 5.2 Unit Registry

```python
from decimal import Decimal


UNIT_REGISTRY: list[Unit] = [

    # ── Mass ────────────────────────────────────────────────────────────────
    Unit("kg",  ["kg", "kilogram", "kilogramm", "kilo", "KG"],
         UnitDimension.MASS,    Decimal("1"),       "kg",  None),
    Unit("g",   ["g", "gram", "gramm"],
         UnitDimension.MASS,    Decimal("0.001"),   "kg",  None),
    Unit("mg",  ["mg", "milligram"],
         UnitDimension.MASS,    Decimal("0.000001"),"kg",  None),
    Unit("t",   ["t", "tonne", "ton", "metric ton", "mt"],
         UnitDimension.MASS,    Decimal("1000"),    "kg",  None),
    Unit("lb",  ["lb", "lbs", "pound", "pound(s)"],
         UnitDimension.MASS,    Decimal("0.453592"),"kg",  None),
    Unit("oz",  ["oz", "ounce"],
         UnitDimension.MASS,    Decimal("0.028350"),"kg",  None),

    # ── Length ──────────────────────────────────────────────────────────────
    Unit("m",   ["m", "meter", "metre"],
         UnitDimension.LENGTH,  Decimal("1"),       "m",   None),
    Unit("mm",  ["mm", "millimeter"],
         UnitDimension.LENGTH,  Decimal("0.001"),   "m",   None),
    Unit("cm",  ["cm", "centimeter"],
         UnitDimension.LENGTH,  Decimal("0.01"),    "m",   None),
    Unit("km",  ["km", "kilometer"],
         UnitDimension.LENGTH,  Decimal("1000"),    "m",   None),
    Unit("in",  ["in", "inch", "inches", "\""],
         UnitDimension.LENGTH,  Decimal("0.0254"),  "m",   None),
    Unit("ft",  ["ft", "foot", "feet", "'"],
         UnitDimension.LENGTH,  Decimal("0.3048"),  "m",   None),

    # ── Area ────────────────────────────────────────────────────────────────
    Unit("m2",  ["m2", "m²", "sqm", "sq.m", "square meter"],
         UnitDimension.AREA,    Decimal("1"),       "m2",  None),
    Unit("cm2", ["cm2", "cm²"],
         UnitDimension.AREA,    Decimal("0.0001"),  "m2",  None),
    Unit("ft2", ["ft2", "sqft", "sq ft"],
         UnitDimension.AREA,    Decimal("0.092903"),"m2",  None),

    # ── Volume ──────────────────────────────────────────────────────────────
    Unit("l",   ["l", "liter", "litre"],
         UnitDimension.VOLUME,  Decimal("0.001"),   "m3",  None),
    Unit("ml",  ["ml", "milliliter"],
         UnitDimension.VOLUME,  Decimal("0.000001"),"m3",  None),
    Unit("m3",  ["m3", "m³", "cbm", "cubic meter"],
         UnitDimension.VOLUME,  Decimal("1"),       "m3",  None),
    Unit("gal", ["gal", "gallon"],
         UnitDimension.VOLUME,  Decimal("0.003785"),"m3",  None),

    # ── Count ───────────────────────────────────────────────────────────────
    Unit("pcs", ["pcs", "pc", "piece", "pieces", "stück", "stk", "szt",
                 "ea", "each", "unit", "units", "st", "nos", "no"],
         UnitDimension.COUNT,   Decimal("1"),       "pcs", None),
    Unit("set", ["set", "sets", "satz", "komplet"],
         UnitDimension.COUNT,   Decimal("1"),       "pcs", None),
    Unit("pair",["pair", "pairs", "paar"],
         UnitDimension.COUNT,   Decimal("2"),       "pcs", None),
    Unit("doz", ["doz", "dozen"],
         UnitDimension.COUNT,   Decimal("12"),      "pcs", None),

    # ── Packaging (pricing per N items) ─────────────────────────────────────
    Unit("per100", ["per 100", "per100", "/100", "C", "C-price", "per c"],
         UnitDimension.PACKAGING, Decimal("1"),     "per100", Decimal("100")),
    Unit("per1000",["per 1000", "per1000", "/1000", "M", "per m", "mille"],
         UnitDimension.PACKAGING, Decimal("1"),     "per1000", Decimal("1000")),
    Unit("reel", ["reel", "rolle", "szpula"],
         UnitDimension.PACKAGING, Decimal("1"),     "pcs",  None),  # quantity varies

    # ── Time ────────────────────────────────────────────────────────────────
    Unit("day",  ["day", "days", "tag", "tage", "dzień", "dni", "jour", "jours"],
         UnitDimension.TIME,    Decimal("1"),       "days", None),
    Unit("week", ["week", "weeks", "woche", "wochen", "tydzień", "tygodnie", "semaine"],
         UnitDimension.TIME,    Decimal("7"),       "days", None),
    Unit("month",["month", "months", "monat", "monate", "miesiąc", "miesiące"],
         UnitDimension.TIME,    Decimal("30"),      "days", None),
]

# Build alias index
_ALIAS_INDEX: dict[str, Unit] = {}
for _u in UNIT_REGISTRY:
    _ALIAS_INDEX[_u.code.lower()] = _u
    for _alias in _u.aliases:
        _ALIAS_INDEX[_alias.lower().strip()] = _u
```

### 5.3 Unit Conversion Engine

```python
import re
from decimal import Decimal, ROUND_HALF_UP


class UnitConversionEngine:
    """
    Resolves raw unit strings, normalizes to canonical form,
    converts "per 100 pcs" pricing to "per pcs" unit prices,
    and flags ambiguous or unknown units.
    """

    # Patterns for per-quantity pricing
    PER_QTY_PATTERN = re.compile(
        r"per\s*(?P<qty>\d+)\s*(?P<unit>pcs?|stück|szt|units?|kg|g|m\b)",
        re.IGNORECASE,
    )
    PER_ABBR_PATTERN = re.compile(
        r"/\s*(?P<qty>\d+)\s*(?P<unit>pcs?|stück|szt)?",
        re.IGNORECASE,
    )

    def resolve_unit(self, raw_unit: str) -> Optional[Unit]:
        if not raw_unit:
            return None
        key = raw_unit.lower().strip()
        if key in _ALIAS_INDEX:
            return _ALIAS_INDEX[key]
        # Partial match
        for alias, unit in _ALIAS_INDEX.items():
            if key.startswith(alias) or alias.startswith(key):
                return unit
        return None

    async def resolve(self, candidate: "PriceCandidate", target_currency: str = "EUR") -> "PriceCandidate":
        """
        Converts candidate price to per-unit price in target currency.
        Handles: per-100-pcs pricing, per-kg pricing, per-meter pricing.
        """
        per_unit_raw = candidate.per_unit_raw or ""
        unit = self.resolve_unit(per_unit_raw)

        if unit and unit.per_quantity:
            # Price given per N items (e.g. per 100 pcs) → convert to per-1-pcs
            candidate.unit_price_eur = candidate.numeric_value / unit.per_quantity
        else:
            # Standard: price is per 1 unit
            candidate.unit_price_eur = candidate.numeric_value

        # Currency conversion
        if candidate.currency_normalized != target_currency:
            rate = await FXRateService.get_rate(candidate.currency_normalized, target_currency)
            if rate:
                candidate.unit_price_eur = candidate.unit_price_eur * rate
            else:
                candidate.confidence = max(0, candidate.confidence - 0.10)
                candidate.unit_price_eur = None

        return candidate

    def normalize_uom(self, raw: str) -> str:
        """Returns canonical UOM code or original if unrecognized."""
        unit = self.resolve_unit(raw)
        return unit.code if unit else raw.lower().strip()

    def convert(self, value: Decimal, from_unit: str, to_unit: str) -> Optional[ConversionResult]:
        src = self.resolve_unit(from_unit)
        tgt = self.resolve_unit(to_unit)
        if not src or not tgt:
            return None
        if src.dimension != tgt.dimension:
            return None
        base_value = value * src.to_base_factor
        converted = base_value / tgt.to_base_factor
        factor = src.to_base_factor / tgt.to_base_factor
        return ConversionResult(
            original_value=value,
            original_unit=from_unit,
            converted_value=converted.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP),
            target_unit=tgt.code,
            factor=factor,
            is_exact=True,
            confidence=0.99,
        )

    def parse_lead_time_days(self, raw: str) -> Optional[int]:
        """Converts 'X weeks', 'X days', 'X months' → days."""
        m = re.search(
            r"(\d+(?:\s*-\s*\d+)?)\s*(day|week|month|tag|woche|monat|dzień|tydzień|miesiąc)s?",
            raw, re.IGNORECASE,
        )
        if not m:
            return None
        value_str = m.group(1)
        unit_str  = m.group(2).lower()
        # Handle range "6-8" → take upper bound
        nums = re.findall(r"\d+", value_str)
        value = int(nums[-1]) if nums else 0
        unit = self.resolve_unit(unit_str)
        if unit and unit.dimension == UnitDimension.TIME:
            return int(value * float(unit.to_base_factor))
        return None


class FXRateService:
    """
    Foreign exchange rate provider.
    Primary: ECB daily rates (free, official).
    Fallback: cached last-known rate with staleness warning.
    Rates cached in Redis for 1 hour.
    """
    ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
    CACHE_TTL_S = 3600

    _cache: dict[str, Decimal] = {}   # "USD_EUR" → rate

    @classmethod
    async def get_rate(cls, from_ccy: str, to_ccy: str) -> Optional[Decimal]:
        if from_ccy == to_ccy:
            return Decimal("1")
        key = f"{from_ccy}_{to_ccy}"
        if key in cls._cache:
            return cls._cache[key]
        await cls._refresh()
        return cls._cache.get(key)

    @classmethod
    async def _refresh(cls):
        import httpx
        import xml.etree.ElementTree as ET
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(cls.ECB_URL)
                resp.raise_for_status()
            root = ET.fromstring(resp.text)
            ns = {"ecb": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref"}
            rates: dict[str, Decimal] = {"EUR": Decimal("1")}
            for cube in root.iter("{http://www.ecb.int/vocabulary/2002-08-01/eurofxref}Cube"):
                currency = cube.get("currency")
                rate_str = cube.get("rate")
                if currency and rate_str:
                    rates[currency] = Decimal(rate_str)
            # Build cross rates (all vs EUR, then cross)
            for from_ccy, from_rate in rates.items():
                for to_ccy, to_rate in rates.items():
                    cross_key = f"{from_ccy}_{to_ccy}"
                    if from_ccy != to_ccy:
                        cls._cache[cross_key] = to_rate / from_rate
            cls._cache["EUR_EUR"] = Decimal("1")
        except Exception as e:
            log.warning("fx_rate_refresh_failed", error=str(e))
```

---

## 6. Supplier Mapping

### 6.1 Supplier domain model

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SupplierProfile:
    supplier_id: str
    canonical_name: str
    aliases: list[str]              # trade names, abbreviations, former names
    domains: list[str]              # email domains: ["acme.com", "acme.de"]
    vat_numbers: list[str]          # "DE123456789", "PL1234567890"
    duns: Optional[str]             # DUNS-9
    eori: Optional[str]             # EU customs
    country_iso: str                # "DE", "PL", "CN"
    preferred_currency: str
    preferred_language: str
    payment_terms_default: str      # "NET 30"
    incoterm_default: str           # "DDP"
    is_approved: bool
    tier: int                       # 1 = strategic, 2 = preferred, 3 = spot
    categories: list[str]           # "STEEL", "ELECTRONICS", "PLASTICS"
    rfq_email: Optional[str]
    portal_url: Optional[str]
    api_key_ref: Optional[str]      # vault reference for portal API key


@dataclass
class BOMMapping:
    mapping_id: str
    supplier_id: str
    supplier_part_number: str       # as the supplier writes it
    customer_part_number: str       # our internal part number
    bom_line_id: Optional[str]      # specific BOM line
    description_supplier: str
    description_internal: str
    uom_supplier: str
    uom_internal: str
    uom_conversion_factor: Decimal  # supplier_qty * factor = internal_qty
    last_price_eur: Optional[Decimal]
    last_seen: Optional[str]
    confidence: float
    match_method: str               # EXACT / FUZZY / MANUAL / AI
```

### 6.2 Supplier Mapper

```python
import re
from difflib import SequenceMatcher
from typing import Optional


class SupplierMapper:
    """
    Identifies supplier from offer document using:
    1. Email domain match
    2. VAT number / DUNS match
    3. Canonical name / alias fuzzy match
    4. Sender address lookup
    """

    FUZZY_THRESHOLD = 0.75

    def __init__(self, supplier_repo: "SupplierRepository"):
        self.repo = supplier_repo

    async def identify(self, doc: OfferDocument) -> Optional[SupplierProfile]:
        # 1. Email domain
        if doc.supplier_hint:
            supplier = await self.repo.find_by_domain(doc.supplier_hint)
            if supplier:
                return supplier

        # 2. VAT number from text
        vat = self._extract_vat(doc.text_content)
        if vat:
            supplier = await self.repo.find_by_vat(vat)
            if supplier:
                return supplier

        # 3. DUNS
        duns = self._extract_duns(doc.text_content)
        if duns:
            supplier = await self.repo.find_by_duns(duns)
            if supplier:
                return supplier

        # 4. Fuzzy name match
        candidate_name = self._extract_company_name(doc.text_content)
        if candidate_name:
            return await self.repo.fuzzy_find(candidate_name, threshold=self.FUZZY_THRESHOLD)

        return None

    def _extract_vat(self, text: str) -> Optional[str]:
        # EU VAT patterns
        m = re.search(
            r"\b(?:VAT|UID|MwSt|NIP|IČ|TVA|BTW|IVA|ΑΦΜ)\s*[:\s]*"
            r"([A-Z]{2}[0-9A-Z]{8,12})",
            text, re.IGNORECASE,
        )
        if m:
            return m.group(1).upper()
        # Fallback: standalone EU VAT format
        m = re.search(r"\b([A-Z]{2}[0-9]{8,11})\b", text)
        return m.group(1) if m else None

    def _extract_duns(self, text: str) -> Optional[str]:
        m = re.search(r"\b(?:DUNS|D-U-N-S)[:\s#]*(\d{2}-?\d{3}-?\d{4})\b", text, re.IGNORECASE)
        return m.group(1).replace("-", "") if m else None

    def _extract_company_name(self, text: str) -> Optional[str]:
        # Take the largest-font-like header (heuristic: first non-empty line or largest token cluster)
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        for line in lines[:10]:
            if re.search(r"\b(GmbH|AG|Ltd|S\.A\.|Inc\.|Corp\.|Sp\.z\.o\.o\.|A/S|B\.V\.)\b", line):
                return line
        return None


class BOMMapper:
    """
    Maps supplier offer line items to internal BOM lines.
    Strategy: part number exact → part number fuzzy → description embedding similarity.
    """

    EXACT_CONFIDENCE    = 1.00
    FUZZY_CONFIDENCE    = 0.75
    EMBEDDING_THRESHOLD = 0.70
    MIN_FUZZY_RATIO     = 0.80

    def __init__(self, bom_repo: "BOMRepository", embedding_model: Optional["EmbeddingModel"] = None):
        self.bom_repo       = bom_repo
        self.embedding_model = embedding_model

    async def map_line_item(self, item: OfferLineItem, supplier_id: str) -> Optional[BOMMapping]:
        # 1. Exact supplier part number match
        if item.part_number_supplier:
            mapping = await self.bom_repo.find_by_supplier_part(
                supplier_id, item.part_number_supplier
            )
            if mapping:
                return mapping

        # 2. Exact customer part number (supplier sometimes puts ours)
        m = re.search(r"(?:customer|buyer|your)\s*part\s*(?:no|nr)[:\s]*([A-Z0-9\-_.]+)",
                      item.description, re.IGNORECASE)
        if m:
            customer_pn = m.group(1).strip()
            mapping = await self.bom_repo.find_by_customer_part(customer_pn)
            if mapping:
                return mapping

        # 3. Fuzzy part number match (handles minor typos)
        if item.part_number_supplier:
            candidates = await self.bom_repo.search_supplier_parts(supplier_id, limit=20)
            best_ratio = 0.0
            best_mapping = None
            for candidate in candidates:
                ratio = SequenceMatcher(
                    None,
                    item.part_number_supplier.upper(),
                    candidate.supplier_part_number.upper()
                ).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_mapping = candidate
            if best_mapping and best_ratio >= self.MIN_FUZZY_RATIO:
                best_mapping.confidence = best_ratio * self.FUZZY_CONFIDENCE
                best_mapping.match_method = "FUZZY"
                return best_mapping

        # 4. Description embedding similarity (optional)
        if self.embedding_model and item.description:
            return await self._embedding_match(item, supplier_id)

        return None

    async def _embedding_match(self, item: OfferLineItem, supplier_id: str) -> Optional[BOMMapping]:
        query_vec = await self.embedding_model.encode(item.description)
        candidates = await self.bom_repo.get_all_descriptions(supplier_id)
        best_score = 0.0
        best_mapping = None
        for candidate, vec in candidates:
            score = self._cosine_similarity(query_vec, vec)
            if score > best_score:
                best_score = score
                best_mapping = candidate
        if best_mapping and best_score >= self.EMBEDDING_THRESHOLD:
            best_mapping.confidence = best_score
            best_mapping.match_method = "AI"
            return best_mapping
        return None

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        import math
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = math.sqrt(sum(x * x for x in a))
        norm_b = math.sqrt(sum(y * y for y in b))
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
```

### 6.3 Material Matcher

```python
class MaterialMatcher:
    """
    Maps supplier material descriptions to internal material designations.
    Handles aliases, grade equivalences, and NLP normalization.
    """

    MATERIAL_ALIASES: dict[str, str] = {
        # Steel
        "steel": "STEEL_CARBON",
        "stahl": "STEEL_CARBON",
        "carbon steel": "STEEL_CARBON",
        "mild steel": "STEEL_CARBON",
        "s235": "S235JR",
        "s235jr": "S235JR",
        "fe360": "S235JR",
        "st37": "S235JR",
        "a36": "S235JR",
        "s355": "S355J2",
        "s355j2": "S355J2",
        "fe510": "S355J2",
        "st52": "S355J2",
        "42crmo4": "42CrMo4",
        "42crmo": "42CrMo4",
        "scm440": "42CrMo4",
        "4140": "42CrMo4",
        "chromoly": "42CrMo4",
        "16mncr5": "16MnCr5",
        "5115": "16MnCr5",
        # Stainless
        "304": "1.4301",
        "ss304": "1.4301",
        "aisi 304": "1.4301",
        "1.4301": "1.4301",
        "x5crni18-10": "1.4301",
        "316l": "1.4404",
        "aisi 316l": "1.4404",
        "1.4404": "1.4404",
        "inox": "STEEL_STAINLESS",
        # Aluminium
        "al": "ALUMINUM",
        "alu": "ALUMINUM",
        "aluminium": "ALUMINUM",
        "aluminum": "ALUMINUM",
        "6061": "6061-T6",
        "6061-t6": "6061-T6",
        "7075": "7075-T6",
        "7075-t6": "7075-T6",
        # Copper
        "cu": "COPPER",
        "brass": "CuZn37",
        "bronze": "CuSn8",
        "cuzn37": "CuZn37",
        # Plastics
        "pa66": "PA66",
        "nylon": "PA66",
        "pa6": "PA6",
        "nylon 6": "PA6",
        "pp": "PP",
        "polypropylene": "PP",
        "pe": "PE",
        "polyethylene": "PE",
        "abs": "ABS",
        "pc": "PC",
        "polycarbonate": "PC",
        "pom": "POM",
        "delrin": "POM",
        "ptfe": "PTFE",
        "teflon": "PTFE",
    }

    async def match(self, raw_material: str) -> Optional[dict]:
        if not raw_material:
            return None
        key = raw_material.lower().strip()
        # Exact match
        if key in self.MATERIAL_ALIASES:
            return {"designation": self.MATERIAL_ALIASES[key], "confidence": 0.95, "method": "EXACT"}
        # Partial match
        for alias, designation in self.MATERIAL_ALIASES.items():
            if alias in key or key in alias:
                return {"designation": designation, "confidence": 0.75, "method": "PARTIAL"}
        return {"designation": raw_material, "confidence": 0.40, "method": "UNKNOWN"}
```

### 6.4 Line Item Grouper

```python
class LineItemGrouper:
    """
    Groups price candidates and entities into coherent OfferLineItems.
    For unstructured text: uses spatial proximity and sentence boundaries.
    For structured: delegates to StructuredOfferExtractor.
    """

    PROXIMITY_CHARS = 200   # max chars between price and part number to be "same item"

    def __init__(self, structured_extractor: StructuredOfferExtractor):
        self.structured = structured_extractor

    async def group(
        self,
        price_candidates: list[PriceCandidate],
        entities: list[ExtractedEntity],
        doc: OfferDocument,
    ) -> list[OfferLineItem]:
        # Structured formats first
        if doc.structured_data:
            items = await self.structured.extract(doc)
            if items:
                return items

        # Text-based grouping
        return self._group_from_text(price_candidates, entities)

    def _group_from_text(self, prices: list[PriceCandidate], entities: list[ExtractedEntity]) -> list[OfferLineItem]:
        unit_prices = [p for p in prices if p.price_type == "UNIT"]
        part_entities = [e for e in entities if e.entity_type == EntityType.PART_NUMBER]
        qty_entities  = [e for e in entities if e.entity_type == EntityType.QUANTITY]
        lead_entities = [e for e in entities if e.entity_type == EntityType.LEAD_TIME]
        moq_entities  = [e for e in entities if e.entity_type == EntityType.MOQ]

        items = []
        used_part_idxs = set()
        used_qty_idxs  = set()

        for price in unit_prices:
            # Find nearest part number
            part = self._nearest_entity(price.start, part_entities, used_part_idxs)
            qty  = self._nearest_entity(price.start, qty_entities,  used_qty_idxs)
            lead = self._nearest_entity(price.start, lead_entities, set())
            moq  = self._nearest_entity(price.start, moq_entities,  set())

            if part:
                used_part_idxs.add(id(part))
            if qty:
                used_qty_idxs.add(id(qty))

            quantity = 1.0
            if qty:
                try:
                    quantity = float((qty.normalized_value or "1").replace(",", ""))
                except ValueError:
                    pass

            lead_days = None
            if lead and lead.normalized_value:
                from ..unit_conversion import UnitConversionEngine
                lead_days = UnitConversionEngine().parse_lead_time_days(lead.normalized_value)

            moq_val = None
            if moq and moq.normalized_value:
                try:
                    moq_val = float(moq.normalized_value.replace(",", ""))
                except ValueError:
                    pass

            items.append(OfferLineItem(
                line_id=_new_id(),
                position=str(len(items) + 1),
                part_number_supplier=part.normalized_value if part else "",
                description=price.context[:200],
                quantity=quantity,
                uom=price.per_unit_raw or "pcs",
                unit_price_raw=float(price.numeric_value),
                currency_raw=price.currency_normalized,
                unit_price_eur=float(price.unit_price_eur) if price.unit_price_eur else None,
                lead_time_days=lead_days,
                moq=moq_val,
                confidence=price.confidence,
                source="TEXT_NLP",
            ))

        return items

    def _nearest_entity(self, pivot: int, entities: list, used: set) -> Optional[ExtractedEntity]:
        best = None
        best_dist = self.PROXIMITY_CHARS + 1
        for ent in entities:
            if id(ent) in used:
                continue
            dist = abs(ent.start - pivot)
            if dist < best_dist:
                best_dist = dist
                best = ent
        return best if best_dist <= self.PROXIMITY_CHARS else None
```

---

## 7. SQL Schema

```sql
-- PostgreSQL 16, schema: sop (Supplier Offer Parser)

CREATE SCHEMA IF NOT EXISTS sop;

-- ── ENUMs ────────────────────────────────────────────────────────────────────

CREATE TYPE sop.offer_format AS ENUM (
    'EMAIL_HTML', 'EMAIL_TEXT', 'PDF', 'EXCEL', 'CSV', 'WORD',
    'EDI_X12', 'EDI_EDIFACT', 'PUNCHOUT_XML', 'JSON_API', 'ERP_IDOC', 'MANUAL_FORM'
);

CREATE TYPE sop.ingestion_channel AS ENUM (
    'EMAIL_IMAP', 'EMAIL_API', 'SFTP', 'HTTP_WEBHOOK',
    'S3_DROP', 'MANUAL_UPLOAD', 'EDI_AS2', 'API_POLL'
);

CREATE TYPE sop.offer_status AS ENUM (
    'RECEIVED', 'PARSING', 'PARSED', 'VALIDATED',
    'MAPPED', 'REJECTED', 'NEEDS_REVIEW', 'ARCHIVED'
);

CREATE TYPE sop.entity_type AS ENUM (
    'PRICE', 'CURRENCY', 'QUANTITY', 'UNIT', 'MATERIAL', 'PART_NUMBER',
    'LEAD_TIME', 'VALIDITY', 'INCOTERM', 'PAYMENT_TERM', 'SUPPLIER_NAME',
    'CONTACT_PERSON', 'RFQ_REF', 'DISCOUNT', 'MOQ', 'PACKAGING',
    'TOOLING_COST', 'SURFACE_FINISH', 'CERTIFICATION'
);

CREATE TYPE sop.match_method AS ENUM ('EXACT', 'FUZZY', 'MANUAL', 'AI', 'UNMATCHED');

CREATE TYPE sop.price_type AS ENUM ('UNIT', 'TOTAL', 'TOOLING', 'SETUP', 'OUTLIER');

-- ── RAW OFFERS ───────────────────────────────────────────────────────────────

CREATE TABLE sop.raw_offers (
    offer_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    channel             sop.ingestion_channel NOT NULL,
    format              sop.offer_format NOT NULL,
    raw_content         BYTEA NOT NULL,
    content_type        TEXT NOT NULL,
    filename            TEXT,
    sender_email        TEXT,
    sender_domain       TEXT,
    subject             TEXT,
    file_size_bytes     BIGINT NOT NULL,
    checksum_sha256     TEXT NOT NULL,
    received_at         TIMESTAMPTZ NOT NULL,
    ingested_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    metadata            JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_raw_offers_checksum     ON sop.raw_offers (checksum_sha256);
CREATE INDEX idx_raw_offers_sender       ON sop.raw_offers (sender_domain) WHERE sender_domain IS NOT NULL;
CREATE INDEX idx_raw_offers_received     ON sop.raw_offers (received_at DESC);

-- ── OFFER DOCUMENTS ──────────────────────────────────────────────────────────

CREATE TABLE sop.offer_documents (
    document_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_offer_id        UUID NOT NULL REFERENCES sop.raw_offers (offer_id) ON DELETE CASCADE,
    status              sop.offer_status NOT NULL DEFAULT 'RECEIVED',
    format              sop.offer_format NOT NULL,
    channel             sop.ingestion_channel NOT NULL,
    language            CHAR(2) NOT NULL DEFAULT 'en',
    encoding            TEXT NOT NULL DEFAULT 'utf-8',
    pages               SMALLINT NOT NULL DEFAULT 1,
    rfq_ref             TEXT,
    rfq_id              UUID,          -- FK to rfq schema (nullable — may not match)
    supplier_id         UUID,          -- FK to sop.suppliers after mapping
    currency_hint       CHAR(3),
    extraction_quality  NUMERIC(5,4),
    parsing_duration_ms INTEGER,
    line_item_count     INTEGER,
    total_value_eur     NUMERIC(14,4),
    overall_confidence  NUMERIC(5,4),
    warnings            TEXT[],
    errors              TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_at           TIMESTAMPTZ,
    mapped_at           TIMESTAMPTZ,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_docs_status        ON sop.offer_documents (status);
CREATE INDEX idx_docs_rfq_ref       ON sop.offer_documents (rfq_ref) WHERE rfq_ref IS NOT NULL;
CREATE INDEX idx_docs_supplier      ON sop.offer_documents (supplier_id) WHERE supplier_id IS NOT NULL;
CREATE INDEX idx_docs_rfq_id        ON sop.offer_documents (rfq_id) WHERE rfq_id IS NOT NULL;
CREATE INDEX idx_docs_received      ON sop.offer_documents (created_at DESC);

-- ── SUPPLIERS ────────────────────────────────────────────────────────────────

CREATE TABLE sop.suppliers (
    supplier_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    canonical_name          TEXT NOT NULL,
    aliases                 TEXT[] NOT NULL DEFAULT '{}',
    domains                 TEXT[] NOT NULL DEFAULT '{}',
    vat_numbers             TEXT[] NOT NULL DEFAULT '{}',
    duns                    TEXT,
    eori                    TEXT,
    country_iso             CHAR(2) NOT NULL,
    preferred_currency      CHAR(3) NOT NULL DEFAULT 'EUR',
    preferred_language      CHAR(2) NOT NULL DEFAULT 'en',
    payment_terms_default   TEXT,
    incoterm_default        TEXT,
    is_approved             BOOLEAN NOT NULL DEFAULT FALSE,
    tier                    SMALLINT NOT NULL DEFAULT 3,
    categories              TEXT[] NOT NULL DEFAULT '{}',
    rfq_email               TEXT,
    portal_url              TEXT,
    api_key_ref             TEXT,      -- HashiCorp Vault path
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_supplier_name     ON sop.suppliers (LOWER(canonical_name));
CREATE INDEX idx_supplier_domains        ON sop.suppliers USING GIN (domains);
CREATE INDEX idx_supplier_vat            ON sop.suppliers USING GIN (vat_numbers);
CREATE INDEX idx_supplier_categories     ON sop.suppliers USING GIN (categories);

-- ── OFFER LINE ITEMS ─────────────────────────────────────────────────────────

CREATE TABLE sop.offer_line_items (
    line_id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id             UUID NOT NULL REFERENCES sop.offer_documents (document_id) ON DELETE CASCADE,
    position                TEXT,
    part_number_supplier    TEXT,
    part_number_customer    TEXT,
    description             TEXT,
    quantity                NUMERIC(14,4),
    uom_raw                 TEXT,
    uom_normalized          TEXT,
    unit_price_raw          NUMERIC(14,6) NOT NULL,
    currency_raw            CHAR(3) NOT NULL,
    unit_price_eur          NUMERIC(14,6),
    total_price_eur         NUMERIC(14,4)
        GENERATED ALWAYS AS (
            CASE WHEN unit_price_eur IS NOT NULL AND quantity IS NOT NULL
                 THEN unit_price_eur * quantity ELSE NULL END
        ) STORED,
    lead_time_days          SMALLINT,
    moq                     NUMERIC(14,4),
    tooling_cost_eur        NUMERIC(12,4),
    discount_pct            NUMERIC(6,4),
    incoterm                TEXT,
    validity_date           DATE,
    material_raw            TEXT,
    material_designation    TEXT,
    material_family         TEXT,
    material_confidence     NUMERIC(5,4),
    bom_line_id             UUID,           -- mapped BOME line
    bom_item_code           TEXT,           -- resolved internal item code
    match_method            sop.match_method NOT NULL DEFAULT 'UNMATCHED',
    match_confidence        NUMERIC(5,4),
    price_type              sop.price_type NOT NULL DEFAULT 'UNIT',
    quantity_break          NUMERIC(14,4),  -- for price-break lines
    source                  TEXT NOT NULL,   -- STRUCTURED / TEXT_NLP / EDI
    confidence              NUMERIC(5,4) NOT NULL,
    needs_review            BOOLEAN NOT NULL DEFAULT FALSE,
    review_notes            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_line_items_doc          ON sop.offer_line_items (document_id);
CREATE INDEX idx_line_items_part_supp    ON sop.offer_line_items (part_number_supplier)
    WHERE part_number_supplier IS NOT NULL;
CREATE INDEX idx_line_items_part_cust    ON sop.offer_line_items (part_number_customer)
    WHERE part_number_customer IS NOT NULL;
CREATE INDEX idx_line_items_bom          ON sop.offer_line_items (bom_line_id)
    WHERE bom_line_id IS NOT NULL;
CREATE INDEX idx_line_items_material     ON sop.offer_line_items (material_designation)
    WHERE material_designation IS NOT NULL;
CREATE INDEX idx_line_items_price_eur    ON sop.offer_line_items (unit_price_eur)
    WHERE unit_price_eur IS NOT NULL;

-- ── EXTRACTED ENTITIES ───────────────────────────────────────────────────────

CREATE TABLE sop.extracted_entities (
    entity_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id         UUID NOT NULL REFERENCES sop.offer_documents (document_id) ON DELETE CASCADE,
    entity_type         sop.entity_type NOT NULL,
    text_raw            TEXT NOT NULL,
    normalized_value    TEXT,
    char_start          INTEGER,
    char_end            INTEGER,
    confidence          NUMERIC(5,4) NOT NULL,
    source              TEXT NOT NULL,   -- NER_MODEL / REGEX / STRUCTURED
    metadata            JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_entities_doc        ON sop.extracted_entities (document_id);
CREATE INDEX idx_entities_type       ON sop.extracted_entities (entity_type);

-- ── BOM MAPPINGS ─────────────────────────────────────────────────────────────

CREATE TABLE sop.bom_mappings (
    mapping_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    supplier_id             UUID NOT NULL REFERENCES sop.suppliers (supplier_id),
    supplier_part_number    TEXT NOT NULL,
    customer_part_number    TEXT NOT NULL,
    bom_line_id             UUID,
    description_supplier    TEXT,
    description_internal    TEXT,
    uom_supplier            TEXT NOT NULL,
    uom_internal            TEXT NOT NULL,
    uom_conversion_factor   NUMERIC(14,8) NOT NULL DEFAULT 1,
    last_price_eur          NUMERIC(14,6),
    last_seen               DATE,
    confidence              NUMERIC(5,4) NOT NULL DEFAULT 1.0,
    match_method            sop.match_method NOT NULL,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_bom_mapping
    ON sop.bom_mappings (supplier_id, supplier_part_number)
    WHERE is_active;
CREATE INDEX idx_bom_mapping_supplier ON sop.bom_mappings (supplier_id);
CREATE INDEX idx_bom_mapping_customer ON sop.bom_mappings (customer_part_number);

-- ── FX RATES ─────────────────────────────────────────────────────────────────

CREATE TABLE sop.fx_rates (
    rate_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    from_currency       CHAR(3) NOT NULL,
    to_currency         CHAR(3) NOT NULL,
    rate                NUMERIC(18,8) NOT NULL,
    source              TEXT NOT NULL DEFAULT 'ECB',
    valid_date          DATE NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_fx_rate ON sop.fx_rates (from_currency, to_currency, valid_date);
CREATE INDEX idx_fx_rates_date ON sop.fx_rates (valid_date DESC);

-- ── OUTBOX ───────────────────────────────────────────────────────────────────

CREATE TABLE sop.outbox_events (
    event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic               TEXT NOT NULL,
    key                 TEXT NOT NULL,
    payload             JSONB NOT NULL,
    headers             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at        TIMESTAMPTZ,
    retry_count         SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_sop_outbox_unpublished ON sop.outbox_events (created_at)
    WHERE published_at IS NULL;

-- ── TRIGGERS ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION sop.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$;

CREATE TRIGGER trg_docs_updated_at
    BEFORE UPDATE ON sop.offer_documents
    FOR EACH ROW EXECUTE FUNCTION sop.set_updated_at();

CREATE TRIGGER trg_suppliers_updated_at
    BEFORE UPDATE ON sop.suppliers
    FOR EACH ROW EXECUTE FUNCTION sop.set_updated_at();

CREATE OR REPLACE FUNCTION sop.publish_offer_parsed()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('PARSED', 'MAPPED', 'REJECTED')
       AND OLD.status NOT IN ('PARSED', 'MAPPED', 'REJECTED') THEN
        INSERT INTO sop.outbox_events (topic, key, payload)
        VALUES (
            'sop.offer.' || LOWER(NEW.status::TEXT),
            NEW.document_id::TEXT,
            jsonb_build_object(
                'document_id',      NEW.document_id,
                'rfq_ref',          NEW.rfq_ref,
                'rfq_id',           NEW.rfq_id,
                'supplier_id',      NEW.supplier_id,
                'status',           NEW.status,
                'line_item_count',  NEW.line_item_count,
                'total_value_eur',  NEW.total_value_eur,
                'overall_confidence', NEW.overall_confidence,
                'parsed_at',        now()
            )
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_offer_status_change
    AFTER UPDATE OF status ON sop.offer_documents
    FOR EACH ROW EXECUTE FUNCTION sop.publish_offer_parsed();

-- ── VIEWS ────────────────────────────────────────────────────────────────────

CREATE VIEW sop.v_offer_summary AS
SELECT
    d.document_id,
    d.rfq_ref,
    d.status,
    d.format,
    d.language,
    d.overall_confidence,
    s.canonical_name        AS supplier_name,
    s.country_iso           AS supplier_country,
    s.tier                  AS supplier_tier,
    d.line_item_count,
    d.total_value_eur,
    COUNT(li.line_id)       AS items_mapped,
    COUNT(li.line_id) FILTER (WHERE li.needs_review) AS items_needing_review,
    AVG(li.confidence)      AS avg_line_confidence,
    d.created_at,
    d.parsed_at,
    d.mapped_at
FROM sop.offer_documents d
LEFT JOIN sop.suppliers          s  ON s.supplier_id  = d.supplier_id
LEFT JOIN sop.offer_line_items   li ON li.document_id = d.document_id
GROUP BY d.document_id, s.supplier_id;

CREATE VIEW sop.v_price_history AS
SELECT
    li.bom_item_code,
    li.material_designation,
    s.canonical_name  AS supplier_name,
    s.country_iso     AS supplier_country,
    li.unit_price_eur,
    li.currency_raw,
    li.uom_normalized,
    li.lead_time_days,
    li.moq,
    d.created_at      AS offer_date
FROM sop.offer_line_items li
JOIN sop.offer_documents  d ON d.document_id = li.document_id
JOIN sop.suppliers        s ON s.supplier_id  = d.supplier_id
WHERE li.bom_item_code IS NOT NULL
  AND li.unit_price_eur IS NOT NULL
  AND d.status IN ('MAPPED', 'VALIDATED')
ORDER BY li.bom_item_code, d.created_at DESC;
```

---

## 8. API

### 8.1 OpenAPI 3.1

```yaml
openapi: "3.1.0"
info:
  title: Supplier Offer Parser API
  version: "1.0.0"
  description: >
    REST API for ingesting, parsing, and querying supplier offer documents.
    Extracts prices, units, materials and maps line items to BOM.
    RBAC: SOP_VIEWER / SOP_OPERATOR / SOP_ANALYST / SOP_PROCUREMENT / SOP_ADMIN.

servers:
  - url: https://api.industrial-cost.io/sop/v1

paths:

  # ── Ingestion ────────────────────────────────────────────────────────────────

  /offers/upload:
    post:
      operationId: uploadOffer
      summary: Upload supplier offer document
      security: [{bearerAuth: []}]
      x-rbac: [SOP_OPERATOR, SOP_PROCUREMENT, SOP_ADMIN]
      requestBody:
        required: true
        content:
          multipart/form-data:
            schema:
              type: object
              required: [file]
              properties:
                file:
                  type: string
                  format: binary
                rfq_ref:
                  type: string
                supplier_id:
                  type: string
                  format: uuid
                  description: Optional hint — system will auto-detect if omitted
                priority:
                  type: integer
                  default: 5
      responses:
        "202":
          description: Offer accepted, parsing queued
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UploadResponse"
        "400":
          $ref: "#/components/responses/ValidationError"
        "409":
          description: Duplicate offer (same checksum)

  /offers/email-webhook:
    post:
      operationId: emailWebhook
      summary: Receive parsed email payload (SendGrid / MS Graph webhook)
      security: [{bearerAuth: []}]
      x-rbac: [SOP_OPERATOR, SOP_ADMIN]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/EmailWebhookPayload"
      responses:
        "202":
          description: Email accepted

  /offers/{document_id}/reparse:
    post:
      operationId: reparseOffer
      summary: Re-run parsing pipeline on an existing offer
      security: [{bearerAuth: []}]
      x-rbac: [SOP_OPERATOR, SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
      responses:
        "202":
          description: Reparse queued
        "404":
          $ref: "#/components/responses/NotFound"

  # ── Offers ──────────────────────────────────────────────────────────────────

  /offers:
    get:
      operationId: listOffers
      summary: List offer documents with filtering
      security: [{bearerAuth: []}]
      x-rbac: [SOP_VIEWER, SOP_OPERATOR, SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - name: status
          in: query
          schema:
            type: string
            enum: [RECEIVED, PARSING, PARSED, VALIDATED, MAPPED, REJECTED, NEEDS_REVIEW, ARCHIVED]
        - name: supplier_id
          in: query
          schema: {type: string, format: uuid}
        - name: rfq_ref
          in: query
          schema: {type: string}
        - name: format
          in: query
          schema: {type: string}
        - name: since
          in: query
          schema: {type: string, format: date}
        - name: limit
          in: query
          schema: {type: integer, default: 50, maximum: 200}
        - name: cursor
          in: query
          schema: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/OfferListResponse"

  /offers/{document_id}:
    get:
      operationId: getOffer
      summary: Get full offer parse result
      security: [{bearerAuth: []}]
      x-rbac: [SOP_VIEWER, SOP_OPERATOR, SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/OfferDetail"
        "404":
          $ref: "#/components/responses/NotFound"

    delete:
      operationId: deleteOffer
      summary: Delete offer and all parsed data
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
      responses:
        "204":
          description: Deleted

  /offers/{document_id}/line-items:
    get:
      operationId: getLineItems
      summary: Get extracted and mapped line items
      security: [{bearerAuth: []}]
      x-rbac: [SOP_VIEWER, SOP_OPERATOR, SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
        - name: needs_review
          in: query
          schema: {type: boolean}
        - name: match_method
          in: query
          schema: {type: string, enum: [EXACT, FUZZY, MANUAL, AI, UNMATCHED]}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/OfferLineItem"

  /offers/{document_id}/line-items/{line_id}/approve:
    post:
      operationId: approveLineItem
      summary: Manually approve / correct a line item mapping
      security: [{bearerAuth: []}]
      x-rbac: [SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
        - name: line_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/LineItemApproval"
      responses:
        "200":
          description: Line item approved/corrected

  /offers/{document_id}/entities:
    get:
      operationId: getEntities
      summary: Get all extracted NER entities
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ANALYST, SOP_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DocumentId"
        - name: entity_type
          in: query
          schema: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/ExtractedEntity"

  # ── Suppliers ────────────────────────────────────────────────────────────────

  /suppliers:
    get:
      operationId: listSuppliers
      summary: List registered suppliers
      security: [{bearerAuth: []}]
      x-rbac: [SOP_VIEWER, SOP_OPERATOR, SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - name: country
          in: query
          schema: {type: string}
        - name: tier
          in: query
          schema: {type: integer, minimum: 1, maximum: 3}
        - name: category
          in: query
          schema: {type: string}
        - name: is_approved
          in: query
          schema: {type: boolean}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/SupplierProfile"

    post:
      operationId: createSupplier
      summary: Register new supplier
      security: [{bearerAuth: []}]
      x-rbac: [SOP_PROCUREMENT, SOP_ADMIN]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/SupplierCreate"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierProfile"

  /suppliers/{supplier_id}/bom-mappings:
    get:
      operationId: getSupplierMappings
      summary: Get BOM part number mappings for supplier
      security: [{bearerAuth: []}]
      x-rbac: [SOP_VIEWER, SOP_OPERATOR, SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
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
                type: array
                items:
                  $ref: "#/components/schemas/BOMMapping"

    post:
      operationId: addBOMMapping
      summary: Manually add or update BOM part number mapping
      security: [{bearerAuth: []}]
      x-rbac: [SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - name: supplier_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/BOMMappingCreate"
      responses:
        "201":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/BOMMapping"

  # ── Analytics ─────────────────────────────────────────────────────────────

  /analytics/price-history:
    get:
      operationId: getPriceHistory
      summary: Historical prices for a BOM item across suppliers
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      parameters:
        - name: bom_item_code
          in: query
          required: true
          schema: {type: string}
        - name: since
          in: query
          schema: {type: string, format: date}
        - name: supplier_id
          in: query
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/PriceHistoryEntry"

  /analytics/supplier-comparison:
    post:
      operationId: compareSuppliers
      summary: Compare prices for selected BOM items across suppliers
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ANALYST, SOP_PROCUREMENT, SOP_ADMIN]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              properties:
                bom_item_codes:
                  type: array
                  items: {type: string}
                supplier_ids:
                  type: array
                  items: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/SupplierComparisonResult"

  /analytics/parsing-stats:
    get:
      operationId: getParsingStats
      summary: Parsing quality and throughput statistics
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ANALYST, SOP_ADMIN]
      parameters:
        - name: since
          in: query
          schema: {type: string, format: date}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ParsingStats"

  # ── FX Rates ─────────────────────────────────────────────────────────────────

  /fx-rates/refresh:
    post:
      operationId: refreshFXRates
      summary: Trigger manual FX rate refresh from ECB
      security: [{bearerAuth: []}]
      x-rbac: [SOP_ADMIN]
      responses:
        "200":
          description: Rates refreshed
          content:
            application/json:
              schema:
                type: object
                properties:
                  currencies_updated: {type: integer}
                  valid_date: {type: string, format: date}

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  parameters:
    DocumentId:
      name: document_id
      in: path
      required: true
      schema: {type: string, format: uuid}

  responses:
    NotFound:
      description: Not found
      content:
        application/json:
          schema: {$ref: "#/components/schemas/ErrorResponse"}
    ValidationError:
      description: Validation error
      content:
        application/json:
          schema: {$ref: "#/components/schemas/ErrorResponse"}

  schemas:
    UploadResponse:
      type: object
      properties:
        document_id: {type: string, format: uuid}
        status: {type: string}
        estimated_duration_ms: {type: integer}

    LineItemApproval:
      type: object
      properties:
        bom_line_id: {type: string, format: uuid}
        bom_item_code: {type: string}
        unit_price_eur: {type: number}
        uom_normalized: {type: string}
        review_notes: {type: string}

    PriceHistoryEntry:
      type: object
      properties:
        supplier_name: {type: string}
        supplier_country: {type: string}
        unit_price_eur: {type: number}
        uom: {type: string}
        lead_time_days: {type: integer}
        moq: {type: number}
        offer_date: {type: string, format: date-time}

    SupplierComparisonResult:
      type: object
      properties:
        items:
          type: array
          items:
            type: object
            properties:
              bom_item_code: {type: string}
              prices:
                type: array
                items:
                  type: object
                  properties:
                    supplier_id: {type: string}
                    supplier_name: {type: string}
                    unit_price_eur: {type: number}
                    lead_time_days: {type: integer}
                    rank: {type: integer}

    ParsingStats:
      type: object
      properties:
        total_offers: {type: integer}
        parsed_count: {type: integer}
        failed_count: {type: integer}
        avg_confidence: {type: number}
        avg_duration_ms: {type: number}
        p95_duration_ms: {type: number}
        unmapped_line_items_pct: {type: number}
        by_format:
          type: array
          items:
            type: object
            properties:
              format: {type: string}
              count: {type: integer}
              avg_confidence: {type: number}

    EmailWebhookPayload:
      type: object
      required: [from_email, subject, body_html]
      properties:
        from_email: {type: string, format: email}
        subject: {type: string}
        body_html: {type: string}
        body_text: {type: string}
        attachments:
          type: array
          items:
            type: object
            properties:
              filename: {type: string}
              content_base64: {type: string}
              content_type: {type: string}

    ErrorResponse:
      type: object
      properties:
        error: {type: string}
        detail: {type: string}
        request_id: {type: string}
```
