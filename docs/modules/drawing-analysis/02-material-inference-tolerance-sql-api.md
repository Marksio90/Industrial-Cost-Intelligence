# Drawing Analysis Engine — Sekcje 6–9

## 6. Material Inference

### 6.1 Domain model materiałów technicznych

```python
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum
import re


class MaterialFamily(str, Enum):
    STEEL_CARBON       = "STEEL_CARBON"
    STEEL_ALLOY        = "STEEL_ALLOY"
    STEEL_STAINLESS    = "STEEL_STAINLESS"
    STEEL_TOOL         = "STEEL_TOOL"
    CAST_IRON_GREY     = "CAST_IRON_GREY"
    CAST_IRON_NODULAR  = "CAST_IRON_NODULAR"
    ALUMINUM_WROUGHT   = "ALUMINUM_WROUGHT"
    ALUMINUM_CAST      = "ALUMINUM_CAST"
    COPPER_ALLOY       = "COPPER_ALLOY"
    TITANIUM           = "TITANIUM"
    NICKEL_SUPERALLOY  = "NICKEL_SUPERALLOY"
    PLASTIC_THERMO     = "PLASTIC_THERMO"
    PLASTIC_THERMOSET  = "PLASTIC_THERMOSET"
    RUBBER_ELASTOMER   = "RUBBER_ELASTOMER"
    COMPOSITE_CFRP     = "COMPOSITE_CFRP"
    COMPOSITE_GFRP     = "COMPOSITE_GFRP"
    CERAMIC            = "CERAMIC"
    WOOD               = "WOOD"
    UNKNOWN            = "UNKNOWN"


class MaterialConfidence(str, Enum):
    HIGH        = "HIGH"        # ≥ 0.85 — explicit designation in title block
    MEDIUM      = "MEDIUM"      # 0.65–0.84 — partial match or context inference
    LOW         = "LOW"         # 0.40–0.64 — feature-based guess
    INDICATIVE  = "INDICATIVE"  # < 0.40 — very uncertain


@dataclass
class MaterialCandidate:
    designation: str               # e.g. "S235JR", "1.4301", "6061-T6"
    family: MaterialFamily
    standard: str                  # "EN 10025", "DIN 17175", "ASTM A36"
    density_kg_m3: float
    yield_strength_mpa: Optional[float]
    tensile_strength_mpa: Optional[float]
    hardness_hb: Optional[float]
    machinability_index: float     # 1.0 = free machining steel reference
    cost_index: float              # relative cost vs S235JR=1.0
    confidence: float


@dataclass
class MaterialInferenceResult:
    best_candidate: MaterialCandidate
    alternatives: list[MaterialCandidate]
    confidence: float
    confidence_level: MaterialConfidence
    inference_sources: list[str]   # "TITLE_BLOCK" / "FEATURE_ANALYSIS" / "TOLERANCE_ANALYSIS" / "NLP_MODEL"
    raw_material_string: Optional[str]
    warnings: list[str] = field(default_factory=list)
```

### 6.2 Material Knowledge Base

```python
# Curated database — simplified representative entries
MATERIAL_DATABASE: list[dict] = [
    # Structural steels
    {"designation": "S235JR", "aliases": ["Fe360", "A36", "St37"],
     "family": MaterialFamily.STEEL_CARBON, "standard": "EN 10025",
     "density": 7850, "yield_mpa": 235, "tensile_mpa": 360,
     "hardness_hb": 120, "machinability": 0.65, "cost_index": 1.0},
    {"designation": "S355J2", "aliases": ["Fe510", "St52"],
     "family": MaterialFamily.STEEL_CARBON, "standard": "EN 10025",
     "density": 7850, "yield_mpa": 355, "tensile_mpa": 510,
     "hardness_hb": 160, "machinability": 0.60, "cost_index": 1.3},
    # Alloy steels
    {"designation": "42CrMo4", "aliases": ["SCM440", "4140", "1.7225"],
     "family": MaterialFamily.STEEL_ALLOY, "standard": "EN 10083",
     "density": 7850, "yield_mpa": 650, "tensile_mpa": 900,
     "hardness_hb": 265, "machinability": 0.55, "cost_index": 2.1},
    {"designation": "16MnCr5", "aliases": ["5115", "1.7131"],
     "family": MaterialFamily.STEEL_ALLOY, "standard": "EN 10084",
     "density": 7850, "yield_mpa": 390, "tensile_mpa": 590,
     "hardness_hb": 180, "machinability": 0.60, "cost_index": 1.7},
    # Stainless steels
    {"designation": "1.4301", "aliases": ["304", "X5CrNi18-10", "AISI 304"],
     "family": MaterialFamily.STEEL_STAINLESS, "standard": "EN 10088",
     "density": 7900, "yield_mpa": 210, "tensile_mpa": 520,
     "hardness_hb": 150, "machinability": 0.45, "cost_index": 4.5},
    {"designation": "1.4404", "aliases": ["316L", "X2CrNiMo17-12-2"],
     "family": MaterialFamily.STEEL_STAINLESS, "standard": "EN 10088",
     "density": 7980, "yield_mpa": 200, "tensile_mpa": 520,
     "hardness_hb": 150, "machinability": 0.40, "cost_index": 5.5},
    # Cast irons
    {"designation": "GG-25", "aliases": ["EN-GJL-250", "HT250"],
     "family": MaterialFamily.CAST_IRON_GREY, "standard": "EN 1561",
     "density": 7250, "yield_mpa": None, "tensile_mpa": 250,
     "hardness_hb": 210, "machinability": 0.70, "cost_index": 0.8},
    {"designation": "GGG-40", "aliases": ["EN-GJS-400-15", "QT400"],
     "family": MaterialFamily.CAST_IRON_NODULAR, "standard": "EN 1563",
     "density": 7100, "yield_mpa": 250, "tensile_mpa": 400,
     "hardness_hb": 160, "machinability": 0.65, "cost_index": 0.9},
    # Aluminium
    {"designation": "6061-T6", "aliases": ["AlMg1SiCu", "AW-6061"],
     "family": MaterialFamily.ALUMINUM_WROUGHT, "standard": "ASTM B221",
     "density": 2700, "yield_mpa": 276, "tensile_mpa": 310,
     "hardness_hb": 95, "machinability": 2.0, "cost_index": 3.2},
    {"designation": "7075-T6", "aliases": ["AlZnMgCu1.5", "AW-7075"],
     "family": MaterialFamily.ALUMINUM_WROUGHT, "standard": "ASTM B209",
     "density": 2810, "yield_mpa": 503, "tensile_mpa": 572,
     "hardness_hb": 150, "machinability": 1.8, "cost_index": 5.8},
    # Titanium
    {"designation": "Ti-6Al-4V", "aliases": ["Grade 5", "3.7165"],
     "family": MaterialFamily.TITANIUM, "standard": "AMS 4928",
     "density": 4430, "yield_mpa": 880, "tensile_mpa": 950,
     "hardness_hb": 330, "machinability": 0.20, "cost_index": 18.0},
    # Plastics
    {"designation": "PA66-GF30", "aliases": ["Nylon 66 GF30", "Zytel"],
     "family": MaterialFamily.PLASTIC_THERMO, "standard": "ISO 1874",
     "density": 1360, "yield_mpa": 160, "tensile_mpa": 185,
     "hardness_hb": None, "machinability": 1.5, "cost_index": 2.8},
]

# Build lookup index
_MAT_INDEX: dict[str, dict] = {}
for _m in MATERIAL_DATABASE:
    _MAT_INDEX[_m["designation"].upper()] = _m
    for _alias in _m.get("aliases", []):
        _MAT_INDEX[_alias.upper()] = _m
```

### 6.3 MaterialInferencer

```python
class MaterialInferencer:
    """
    Infers material from multiple signals with confidence scoring:
    1. Exact match from title block designation
    2. Fuzzy/alias match
    3. NLP normalization (e.g. "STAHL 1.4301" → "1.4301")
    4. Feature-based inference (hardness annotation, treatment callouts)
    5. Tolerance tightness heuristic
    """

    HEAT_TREATMENT_PATTERNS = {
        re.compile(r"HRC\s*([\d.]+)", re.I): "TOOL_STEEL_OR_ALLOY",
        re.compile(r"HB\s*([\d.]+)", re.I): "STEEL",
        re.compile(r"NITRIDED|NITRIEREN", re.I): "ALLOY_STEEL",
        re.compile(r"CARBURIZED|EINSATZGEH[AÄ]RTET", re.I): "CASE_HARDENING_STEEL",
        re.compile(r"ANODIZED|ELOXIERT", re.I): "ALUMINUM",
        re.compile(r"PASSIVATED|PASSIVIERT", re.I): "STAINLESS_STEEL",
        re.compile(r"GALVANIZED|VERZINKT", re.I): "STEEL_CARBON",
    }

    SURFACE_FINISH_FAMILY_HINTS = {
        "ANODIZE":   MaterialFamily.ALUMINUM_WROUGHT,
        "ELOX":      MaterialFamily.ALUMINUM_WROUGHT,
        "PASSIVATE": MaterialFamily.STEEL_STAINLESS,
        "NITRIDE":   MaterialFamily.STEEL_ALLOY,
        "CARBURIZE": MaterialFamily.STEEL_ALLOY,
        "GALVANIZE": MaterialFamily.STEEL_CARBON,
    }

    async def infer(
        self,
        title_block: Optional[TitleBlock],
        features: list[DetectedFeature],
        tolerances: list["Tolerance"],
        dimensions: list[ExtractedDimension],
    ) -> Optional[MaterialInferenceResult]:
        candidates: list[tuple[MaterialCandidate, str]] = []

        # Signal 1: Title block material field
        if title_block and title_block.material:
            mat = self._lookup(title_block.material)
            if mat:
                candidates.append((self._to_candidate(mat, confidence=0.92), "TITLE_BLOCK"))

        # Signal 2: Title block surface finish hints
        if title_block and title_block.surface_finish:
            for keyword, family in self.SURFACE_FINISH_FAMILY_HINTS.items():
                if keyword in (title_block.surface_finish or "").upper():
                    family_candidates = self._family_defaults(family)
                    for c in family_candidates:
                        candidates.append((c, "SURFACE_FINISH"))
                    break

        # Signal 3: Heat treatment / hardness callouts from all text
        all_text = (title_block.material or "") + " " + (title_block.surface_finish or "")
        for pattern, hint in self.HEAT_TREATMENT_PATTERNS.items():
            m = pattern.search(all_text)
            if m:
                hinted = self._infer_from_hint(hint)
                if hinted:
                    candidates.append((hinted, "HEAT_TREATMENT_ANNOTATION"))

        # Signal 4: Tolerance tightness → infers precision material
        tol_tightness = self._assess_tolerance_tightness(tolerances)
        if tol_tightness == "TIGHT" and not candidates:
            # Tight tolerances → likely alloy steel or stainless
            for c in self._family_defaults(MaterialFamily.STEEL_ALLOY):
                candidates.append((c, "TOLERANCE_ANALYSIS"))

        if not candidates:
            return None

        # Score and rank
        best_candidate, best_source = candidates[0]
        alternatives = [c for c, _ in candidates[1:5]]
        sources = list({s for _, s in candidates})

        confidence = best_candidate.confidence
        conf_level = (
            MaterialConfidence.HIGH if confidence >= 0.85 else
            MaterialConfidence.MEDIUM if confidence >= 0.65 else
            MaterialConfidence.LOW if confidence >= 0.40 else
            MaterialConfidence.INDICATIVE
        )

        return MaterialInferenceResult(
            best_candidate=best_candidate,
            alternatives=alternatives,
            confidence=confidence,
            confidence_level=conf_level,
            inference_sources=sources,
            raw_material_string=title_block.material if title_block else None,
        )

    def _lookup(self, raw: str) -> Optional[dict]:
        normalized = raw.strip().upper()
        if normalized in _MAT_INDEX:
            return _MAT_INDEX[normalized]
        # Partial match
        for key, mat in _MAT_INDEX.items():
            if key in normalized or normalized in key:
                return mat
        return None

    def _to_candidate(self, mat: dict, confidence: float) -> MaterialCandidate:
        return MaterialCandidate(
            designation=mat["designation"],
            family=mat["family"],
            standard=mat.get("standard", ""),
            density_kg_m3=mat.get("density", 7850),
            yield_strength_mpa=mat.get("yield_mpa"),
            tensile_strength_mpa=mat.get("tensile_mpa"),
            hardness_hb=mat.get("hardness_hb"),
            machinability_index=mat.get("machinability", 1.0),
            cost_index=mat.get("cost_index", 1.0),
            confidence=confidence,
        )

    def _family_defaults(self, family: MaterialFamily) -> list[MaterialCandidate]:
        return [
            self._to_candidate(m, confidence=0.55)
            for m in MATERIAL_DATABASE
            if m["family"] == family
        ][:3]

    def _infer_from_hint(self, hint: str) -> Optional[MaterialCandidate]:
        hint_map = {
            "ALLOY_STEEL":          "42CrMo4",
            "CASE_HARDENING_STEEL": "16MnCr5",
            "STAINLESS_STEEL":      "1.4301",
            "ALUMINUM":             "6061-T6",
            "STEEL":                "S355J2",
        }
        designation = hint_map.get(hint)
        if designation and designation in _MAT_INDEX:
            return self._to_candidate(_MAT_INDEX[designation], confidence=0.65)
        return None

    def _assess_tolerance_tightness(self, tolerances: list["Tolerance"]) -> str:
        if not tolerances:
            return "NORMAL"
        it_grades = [t.it_grade for t in tolerances if t.it_grade]
        if not it_grades:
            return "NORMAL"
        min_grade = min(int(g.replace("IT", "")) for g in it_grades if g.startswith("IT"))
        return "TIGHT" if min_grade <= 7 else "NORMAL"
```

---

## 7. Tolerance Parsing

### 7.1 Tolerance domain model

```python
from decimal import Decimal


class ToleranceType(str, Enum):
    DIMENSIONAL    = "DIMENSIONAL"      # ±0.05, +0.1/-0.0
    GD_T_FORM      = "GD_T_FORM"       # flatness, roundness, cylindricity, straightness
    GD_T_ORIENTATION = "GD_T_ORIENTATION"  # perpendicularity, parallelism, angularity
    GD_T_LOCATION  = "GD_T_LOCATION"   # position, concentricity, symmetry
    GD_T_RUNOUT    = "GD_T_RUNOUT"     # circular runout, total runout
    GD_T_PROFILE   = "GD_T_PROFILE"    # profile of a line / surface
    SURFACE_FINISH = "SURFACE_FINISH"  # Ra, Rz, Rmax
    THREAD         = "THREAD"          # tolerance class 6H, 6g
    ANGULAR        = "ANGULAR"         # ±0.5°
    GENERAL        = "GENERAL"         # ISO 2768 general tolerance class


class GDTSymbol(str, Enum):
    FLATNESS          = "⏥"
    STRAIGHTNESS      = "⏤"
    ROUNDNESS         = "○"
    CYLINDRICITY      = "⌭"
    PERPENDICULARITY  = "⊥"
    PARALLELISM       = "∥"
    ANGULARITY        = "∠"
    POSITION          = "⊕"
    CONCENTRICITY     = "◎"
    SYMMETRY          = "⌯"
    CIRCULAR_RUNOUT   = "↗"
    TOTAL_RUNOUT      = "⌰"
    PROFILE_LINE      = "⌒"
    PROFILE_SURFACE   = "⌓"


ISO_2768_CLASS = {
    "f": {"linear_mm": 0.05, "angular_deg": 0.25},   # fine
    "m": {"linear_mm": 0.10, "angular_deg": 0.50},   # medium
    "c": {"linear_mm": 0.20, "angular_deg": 1.00},   # coarse
    "v": {"linear_mm": 0.50, "angular_deg": 2.00},   # very coarse
}

# ISO 286 IT grade tolerance widths (μm) for 10–18mm range
IT_GRADE_UM = {
    "IT01": 0.5, "IT0": 0.8, "IT1": 1.2, "IT2": 2.0, "IT3": 3.0,
    "IT4": 5.0, "IT5": 8.0, "IT6": 11.0, "IT7": 18.0, "IT8": 27.0,
    "IT9": 43.0, "IT10": 70.0, "IT11": 110.0, "IT12": 180.0,
}


@dataclass
class Tolerance:
    tol_type: ToleranceType
    nominal: Optional[float]          # nominal dimension (mm)
    upper_dev: Optional[float]        # upper deviation (mm)
    lower_dev: Optional[float]        # lower deviation (mm)
    gdt_symbol: Optional[GDTSymbol]
    gdt_value: Optional[float]        # tolerance zone width (mm)
    datum_refs: list[str]             # e.g. ["A", "B"]
    it_grade: Optional[str]           # "IT7", "H7", "h6"
    surface_ra: Optional[float]       # Ra μm
    surface_rz: Optional[float]       # Rz μm
    iso_2768_class: Optional[str]     # "m", "c"
    text_raw: str
    confidence: float
    source: str                       # OCR_REGEX / DXF_ENTITY / GDT_FRAME_PARSER
    page: int = 0
```

### 7.2 Tolerance Parser

```python
class ToleranceParser:
    """
    Parses dimensional tolerances, GD&T frames, surface finish callouts.
    Supports ISO, ANSI/ASME Y14.5, DIN standards.
    """

    # ±0.05 or +0.10/-0.05
    BILATERAL_PAT = re.compile(
        r"([\d]+(?:[.,][\d]+)?)"           # nominal
        r"\s*"
        r"(?:"
        r"±\s*([\d]+(?:[.,][\d]+)?)"       # symmetric ±
        r"|"
        r"\+\s*([\d]+(?:[.,][\d]+)?)\s*/\s*-\s*([\d]+(?:[.,][\d]+)?)"  # asymmetric
        r")"
    )

    # ISO fit codes: H7, h6, F8/h7
    ISO_FIT_PAT = re.compile(r"([A-Z][a-z]?)(\d{1,2})/([a-z][A-Z]?)(\d{1,2})|([A-Za-z])(\d{1,2})")

    # IT grade: IT7, IT6
    IT_GRADE_PAT = re.compile(r"\bIT\s*(\d{1,2})\b")

    # ISO 2768 general tolerance
    ISO_2768_PAT = re.compile(r"ISO\s*2768\s*[-–]\s*([fmcv])", re.I)

    # GD&T feature control frame patterns (text representation)
    GDT_FRAME_PAT = re.compile(
        r"([⏥⏤○⌭⊥∥∠⊕◎⌯↗⌰⌒⌓])"          # symbol
        r"\s*\|?\s*([\d.,]+)"              # tolerance value
        r"(?:\s*\|\s*([A-Z]))?",           # datum A
        re.UNICODE,
    )

    # Surface finish Ra / Rz
    SURFACE_PAT = re.compile(r"R([az])\s*(?:=|≤|max\.?)?\s*([\d.,]+)\s*(?:μm|um|µm)?", re.I)

    async def parse(
        self,
        ocr: Optional[OCRResult],
        geometry: GeometryResult,
    ) -> list[Tolerance]:
        tolerances = []
        all_words = ocr.words if ocr else []
        full_text = " ".join(w.text for w in all_words)

        tolerances += self._parse_bilateral(full_text, all_words)
        tolerances += self._parse_iso_fit(full_text)
        tolerances += self._parse_it_grade(full_text)
        tolerances += self._parse_iso_2768(full_text)
        tolerances += self._parse_gdt_frames(full_text)
        tolerances += self._parse_surface_finish(full_text)
        tolerances += self._parse_from_dimensions(geometry.dimensions)

        return tolerances

    def _parse_bilateral(self, text: str, words: list[OCRWord]) -> list[Tolerance]:
        tolerances = []
        for m in self.BILATERAL_PAT.finditer(text):
            nominal = float(m.group(1).replace(",", "."))
            if m.group(2):   # symmetric
                dev = float(m.group(2).replace(",", "."))
                upper, lower = dev, -dev
            else:            # asymmetric
                upper = float(m.group(3).replace(",", "."))
                lower = -float(m.group(4).replace(",", "."))
            tolerances.append(Tolerance(
                tol_type=ToleranceType.DIMENSIONAL,
                nominal=nominal, upper_dev=upper, lower_dev=lower,
                gdt_symbol=None, gdt_value=None, datum_refs=[],
                it_grade=None, surface_ra=None, surface_rz=None,
                iso_2768_class=None,
                text_raw=m.group(0),
                confidence=0.85, source="OCR_REGEX",
            ))
        return tolerances

    def _parse_iso_fit(self, text: str) -> list[Tolerance]:
        tolerances = []
        for m in self.ISO_FIT_PAT.finditer(text):
            raw = m.group(0)
            tolerances.append(Tolerance(
                tol_type=ToleranceType.DIMENSIONAL,
                nominal=None, upper_dev=None, lower_dev=None,
                gdt_symbol=None, gdt_value=None, datum_refs=[],
                it_grade=raw,
                surface_ra=None, surface_rz=None, iso_2768_class=None,
                text_raw=raw, confidence=0.88, source="OCR_REGEX",
            ))
        return tolerances

    def _parse_it_grade(self, text: str) -> list[Tolerance]:
        tolerances = []
        for m in self.IT_GRADE_PAT.finditer(text):
            grade = f"IT{m.group(1)}"
            tolerances.append(Tolerance(
                tol_type=ToleranceType.DIMENSIONAL,
                nominal=None, upper_dev=None, lower_dev=None,
                gdt_symbol=None, gdt_value=None, datum_refs=[],
                it_grade=grade,
                surface_ra=None, surface_rz=None, iso_2768_class=None,
                text_raw=m.group(0), confidence=0.80, source="OCR_REGEX",
            ))
        return tolerances

    def _parse_iso_2768(self, text: str) -> list[Tolerance]:
        tolerances = []
        for m in self.ISO_2768_PAT.finditer(text):
            cls = m.group(1).lower()
            tolerances.append(Tolerance(
                tol_type=ToleranceType.GENERAL,
                nominal=None,
                upper_dev=ISO_2768_CLASS.get(cls, {}).get("linear_mm"),
                lower_dev=None,
                gdt_symbol=None, gdt_value=None, datum_refs=[],
                it_grade=None,
                surface_ra=None, surface_rz=None,
                iso_2768_class=cls,
                text_raw=m.group(0), confidence=0.90, source="OCR_REGEX",
            ))
        return tolerances

    def _parse_gdt_frames(self, text: str) -> list[Tolerance]:
        tolerances = []
        GDT_SYMBOL_MAP = {v.value: v for v in GDTSymbol}
        for m in self.GDT_FRAME_PAT.finditer(text):
            symbol_char = m.group(1)
            tol_val = float(m.group(2).replace(",", "."))
            datum = m.group(3)
            symbol = GDT_SYMBOL_MAP.get(symbol_char)
            if not symbol:
                continue

            tol_type = self._gdt_to_type(symbol)
            tolerances.append(Tolerance(
                tol_type=tol_type,
                nominal=None, upper_dev=None, lower_dev=None,
                gdt_symbol=symbol, gdt_value=tol_val,
                datum_refs=[datum] if datum else [],
                it_grade=None, surface_ra=None, surface_rz=None,
                iso_2768_class=None,
                text_raw=m.group(0), confidence=0.82, source="GDT_FRAME_PARSER",
            ))
        return tolerances

    def _gdt_to_type(self, symbol: GDTSymbol) -> ToleranceType:
        form_symbols = {
            GDTSymbol.FLATNESS, GDTSymbol.STRAIGHTNESS,
            GDTSymbol.ROUNDNESS, GDTSymbol.CYLINDRICITY,
        }
        orientation_symbols = {
            GDTSymbol.PERPENDICULARITY, GDTSymbol.PARALLELISM, GDTSymbol.ANGULARITY,
        }
        location_symbols = {
            GDTSymbol.POSITION, GDTSymbol.CONCENTRICITY, GDTSymbol.SYMMETRY,
        }
        runout_symbols = {
            GDTSymbol.CIRCULAR_RUNOUT, GDTSymbol.TOTAL_RUNOUT,
        }
        if symbol in form_symbols:
            return ToleranceType.GD_T_FORM
        if symbol in orientation_symbols:
            return ToleranceType.GD_T_ORIENTATION
        if symbol in location_symbols:
            return ToleranceType.GD_T_LOCATION
        if symbol in runout_symbols:
            return ToleranceType.GD_T_RUNOUT
        return ToleranceType.GD_T_PROFILE

    def _parse_surface_finish(self, text: str) -> list[Tolerance]:
        tolerances = []
        for m in self.SURFACE_PAT.finditer(text):
            kind = m.group(1).lower()  # "a" or "z"
            val = float(m.group(2).replace(",", "."))
            tolerances.append(Tolerance(
                tol_type=ToleranceType.SURFACE_FINISH,
                nominal=None, upper_dev=None, lower_dev=None,
                gdt_symbol=None, gdt_value=None, datum_refs=[],
                it_grade=None,
                surface_ra=val if kind == "a" else None,
                surface_rz=val if kind == "z" else None,
                iso_2768_class=None,
                text_raw=m.group(0), confidence=0.88, source="OCR_REGEX",
            ))
        return tolerances

    def _parse_from_dimensions(self, dims: list[ExtractedDimension]) -> list[Tolerance]:
        tolerances = []
        for dim in dims:
            if dim.tolerance_upper is not None:
                tolerances.append(Tolerance(
                    tol_type=ToleranceType.DIMENSIONAL,
                    nominal=dim.value,
                    upper_dev=dim.tolerance_upper,
                    lower_dev=dim.tolerance_lower,
                    gdt_symbol=None, gdt_value=None, datum_refs=[],
                    it_grade=None, surface_ra=None, surface_rz=None,
                    iso_2768_class=None,
                    text_raw=dim.text_raw,
                    confidence=dim.confidence * 0.9,
                    source=dim.source,
                ))
        return tolerances
```

---

## 8. SQL Schema

```sql
-- PostgreSQL 16, schema: dae (Drawing Analysis Engine)

CREATE SCHEMA IF NOT EXISTS dae;

-- ── ENUMs ────────────────────────────────────────────────────────────────────

CREATE TYPE dae.drawing_format AS ENUM (
    'PDF', 'DXF', 'DWG', 'STEP', 'IGES', 'SVG', 'PNG', 'TIFF', 'JPEG'
);

CREATE TYPE dae.drawing_type AS ENUM (
    'DETAIL', 'ASSEMBLY', 'SCHEMATIC', 'WELD',
    'SHEET_METAL', 'CASTING', 'MACHINED', 'GENERAL_ARRANGEMENT'
);

CREATE TYPE dae.drawing_standard AS ENUM (
    'ISO', 'ANSI', 'DIN', 'JIS', 'GB', 'GOST'
);

CREATE TYPE dae.parse_status AS ENUM (
    'QUEUED', 'PREPROCESSING', 'PARSING', 'OCR', 'GEOMETRY',
    'FEATURES', 'TOLERANCES', 'MATERIAL', 'DONE', 'FAILED', 'PARTIAL'
);

CREATE TYPE dae.feature_type AS ENUM (
    'HOLE_THRU', 'HOLE_BLIND', 'HOLE_COUNTERSINK', 'HOLE_COUNTERBORE',
    'THREAD_INTERNAL', 'THREAD_EXTERNAL', 'POCKET', 'SLOT',
    'FILLET', 'CHAMFER', 'BOSS', 'RIB', 'UNDERCUT', 'KNURL',
    'GROOVE', 'WELD_JOINT', 'BEND', 'EMBOSS', 'DRAFT_ANGLE', 'PARTING_LINE'
);

CREATE TYPE dae.tolerance_type AS ENUM (
    'DIMENSIONAL', 'GD_T_FORM', 'GD_T_ORIENTATION', 'GD_T_LOCATION',
    'GD_T_RUNOUT', 'GD_T_PROFILE', 'SURFACE_FINISH', 'THREAD', 'ANGULAR', 'GENERAL'
);

CREATE TYPE dae.material_family AS ENUM (
    'STEEL_CARBON', 'STEEL_ALLOY', 'STEEL_STAINLESS', 'STEEL_TOOL',
    'CAST_IRON_GREY', 'CAST_IRON_NODULAR', 'ALUMINUM_WROUGHT', 'ALUMINUM_CAST',
    'COPPER_ALLOY', 'TITANIUM', 'NICKEL_SUPERALLOY',
    'PLASTIC_THERMO', 'PLASTIC_THERMOSET', 'RUBBER_ELASTOMER',
    'COMPOSITE_CFRP', 'COMPOSITE_GFRP', 'CERAMIC', 'WOOD', 'UNKNOWN'
);

CREATE TYPE dae.confidence_level AS ENUM ('HIGH', 'MEDIUM', 'LOW', 'INDICATIVE');

-- ── CORE TABLES ──────────────────────────────────────────────────────────────

CREATE TABLE dae.drawings (
    drawing_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    part_number         TEXT,
    part_name           TEXT,
    revision            TEXT,
    format              dae.drawing_format NOT NULL,
    drawing_type        dae.drawing_type,
    standard            dae.drawing_standard,
    page_count          SMALLINT NOT NULL DEFAULT 1,
    dpi                 SMALLINT,
    file_size_bytes     BIGINT NOT NULL,
    checksum_sha256     TEXT NOT NULL,
    filename            TEXT NOT NULL,
    storage_uri         TEXT NOT NULL,          -- s3://bucket/key or local path
    parse_status        dae.parse_status NOT NULL DEFAULT 'QUEUED',
    overall_confidence  NUMERIC(5,4),           -- 0.0000 – 1.0000
    processing_time_ms  INTEGER,
    ocr_engine_used     TEXT,
    ocr_mean_confidence NUMERIC(5,4),
    warnings            TEXT[],
    errors              TEXT[],
    uploaded_by         TEXT NOT NULL,
    uploaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    parsed_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_drawings_part_number  ON dae.drawings (part_number) WHERE part_number IS NOT NULL;
CREATE INDEX idx_drawings_parse_status ON dae.drawings (parse_status);
CREATE INDEX idx_drawings_checksum     ON dae.drawings (checksum_sha256);
CREATE INDEX idx_drawings_uploaded_at  ON dae.drawings (uploaded_at DESC);

-- ── TITLE BLOCKS ─────────────────────────────────────────────────────────────

CREATE TABLE dae.title_blocks (
    title_block_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    part_number         TEXT,
    part_name           TEXT,
    revision            TEXT,
    material_raw        TEXT,           -- as written on drawing
    surface_finish      TEXT,
    scale               TEXT,
    projection_method   TEXT,           -- '1st angle' / '3rd angle'
    drawn_by            TEXT,
    checked_by          TEXT,
    approved_by         TEXT,
    drawing_date        DATE,
    company             TEXT,
    sheet_number        TEXT,
    mass_kg             NUMERIC(10,4),
    unit                TEXT NOT NULL DEFAULT 'mm',
    confidence          NUMERIC(5,4) NOT NULL,
    extracted_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_title_block_drawing ON dae.title_blocks (drawing_id);

-- ── DETECTED FEATURES ────────────────────────────────────────────────────────

CREATE TABLE dae.detected_features (
    feature_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    feature_type        dae.feature_type NOT NULL,
    count               SMALLINT NOT NULL DEFAULT 1,
    location_x          NUMERIC(14,6),
    location_y          NUMERIC(14,6),
    parameters          JSONB NOT NULL DEFAULT '{}',  -- {diameter, depth, pitch, angle_deg, ...}
    confidence          NUMERIC(5,4) NOT NULL,
    source              TEXT NOT NULL,               -- GEOMETRY / OCR_REGEX / CV_MODEL / STEP_TOPOLOGY
    notes               TEXT[],
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_features_drawing      ON dae.detected_features (drawing_id);
CREATE INDEX idx_features_type         ON dae.detected_features (feature_type);
CREATE INDEX idx_features_parameters   ON dae.detected_features USING GIN (parameters);

-- ── DIMENSIONS ───────────────────────────────────────────────────────────────

CREATE TABLE dae.dimensions (
    dimension_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    dim_type            TEXT NOT NULL,          -- LINEAR / ANGULAR / RADIAL / DIAMETER / ORDINATE
    value_mm            NUMERIC(14,6) NOT NULL,
    unit                TEXT NOT NULL DEFAULT 'mm',
    tolerance_upper_mm  NUMERIC(10,6),
    tolerance_lower_mm  NUMERIC(10,6),
    text_raw            TEXT NOT NULL,
    confidence          NUMERIC(5,4) NOT NULL,
    source              TEXT NOT NULL,
    bbox_x0             NUMERIC(10,3),
    bbox_y0             NUMERIC(10,3),
    bbox_x1             NUMERIC(10,3),
    bbox_y1             NUMERIC(10,3),
    page                SMALLINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_dimensions_drawing    ON dae.dimensions (drawing_id);
CREATE INDEX idx_dimensions_type       ON dae.dimensions (dim_type);

-- ── TOLERANCES ───────────────────────────────────────────────────────────────

CREATE TABLE dae.tolerances (
    tolerance_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    tol_type            dae.tolerance_type NOT NULL,
    nominal_mm          NUMERIC(14,6),
    upper_dev_mm        NUMERIC(10,6),
    lower_dev_mm        NUMERIC(10,6),
    gdt_symbol          TEXT,
    gdt_value_mm        NUMERIC(10,6),
    datum_refs          TEXT[],
    it_grade            TEXT,
    surface_ra_um       NUMERIC(8,3),
    surface_rz_um       NUMERIC(8,3),
    iso_2768_class      CHAR(1),
    text_raw            TEXT NOT NULL,
    confidence          NUMERIC(5,4) NOT NULL,
    source              TEXT NOT NULL,
    page                SMALLINT NOT NULL DEFAULT 0,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_tolerances_drawing    ON dae.tolerances (drawing_id);
CREATE INDEX idx_tolerances_type       ON dae.tolerances (tol_type);

-- ── MATERIAL INFERENCE ───────────────────────────────────────────────────────

CREATE TABLE dae.material_inferences (
    inference_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id              UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    designation             TEXT NOT NULL,          -- S235JR, 1.4301, 6061-T6
    family                  dae.material_family NOT NULL,
    standard                TEXT,
    density_kg_m3           NUMERIC(8,2),
    yield_strength_mpa      NUMERIC(8,2),
    tensile_strength_mpa    NUMERIC(8,2),
    hardness_hb             NUMERIC(8,2),
    machinability_index     NUMERIC(6,4),
    cost_index              NUMERIC(8,4),
    confidence              NUMERIC(5,4) NOT NULL,
    confidence_level        dae.confidence_level NOT NULL,
    inference_sources       TEXT[],
    raw_material_string     TEXT,
    alternatives            JSONB NOT NULL DEFAULT '[]',
    warnings                TEXT[],
    inferred_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_material_drawing ON dae.material_inferences (drawing_id);

-- ── GEOMETRY SUMMARY ─────────────────────────────────────────────────────────

CREATE TABLE dae.geometry_summaries (
    summary_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    line_count          INTEGER NOT NULL DEFAULT 0,
    arc_count           INTEGER NOT NULL DEFAULT 0,
    circle_count        INTEGER NOT NULL DEFAULT 0,
    polyline_count      INTEGER NOT NULL DEFAULT 0,
    dimension_count     INTEGER NOT NULL DEFAULT 0,
    bbox_xmin           NUMERIC(14,6),
    bbox_ymin           NUMERIC(14,6),
    bbox_xmax           NUMERIC(14,6),
    bbox_ymax           NUMERIC(14,6),
    unit                TEXT NOT NULL DEFAULT 'mm',
    confidence          NUMERIC(5,4) NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_geometry_drawing ON dae.geometry_summaries (drawing_id);

-- ── PARSE JOBS ───────────────────────────────────────────────────────────────

CREATE TABLE dae.parse_jobs (
    job_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    drawing_id          UUID NOT NULL REFERENCES dae.drawings (drawing_id) ON DELETE CASCADE,
    status              dae.parse_status NOT NULL DEFAULT 'QUEUED',
    priority            SMALLINT NOT NULL DEFAULT 5,    -- 1=highest, 10=lowest
    worker_id           TEXT,
    queued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    finished_at         TIMESTAMPTZ,
    retry_count         SMALLINT NOT NULL DEFAULT 0,
    max_retries         SMALLINT NOT NULL DEFAULT 3,
    error_message       TEXT,
    pipeline_stages     JSONB NOT NULL DEFAULT '[]'     -- per-stage timing
);

CREATE INDEX idx_parse_jobs_status   ON dae.parse_jobs (status, priority, queued_at)
    WHERE status IN ('QUEUED', 'PREPROCESSING', 'PARSING');
CREATE INDEX idx_parse_jobs_drawing  ON dae.parse_jobs (drawing_id);
CREATE INDEX idx_parse_jobs_worker   ON dae.parse_jobs (worker_id) WHERE worker_id IS NOT NULL;

-- ── OUTBOX (Kafka transactional outbox) ──────────────────────────────────────

CREATE TABLE dae.outbox_events (
    event_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic               TEXT NOT NULL,
    key                 TEXT NOT NULL,
    payload             JSONB NOT NULL,
    headers             JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    published_at        TIMESTAMPTZ,
    retry_count         SMALLINT NOT NULL DEFAULT 0
);

CREATE INDEX idx_outbox_unpublished ON dae.outbox_events (created_at)
    WHERE published_at IS NULL;

-- ── TRIGGER: updated_at ───────────────────────────────────────────────────────

CREATE OR REPLACE FUNCTION dae.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_drawings_updated_at
    BEFORE UPDATE ON dae.drawings
    FOR EACH ROW EXECUTE FUNCTION dae.set_updated_at();

-- ── TRIGGER: auto-publish parse completion event ──────────────────────────────

CREATE OR REPLACE FUNCTION dae.publish_parse_done()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.parse_status IN ('DONE', 'FAILED', 'PARTIAL')
       AND OLD.parse_status NOT IN ('DONE', 'FAILED', 'PARTIAL') THEN
        INSERT INTO dae.outbox_events (topic, key, payload)
        VALUES (
            'dae.drawing.parsed',
            NEW.drawing_id::TEXT,
            jsonb_build_object(
                'drawing_id',           NEW.drawing_id,
                'part_number',          NEW.part_number,
                'parse_status',         NEW.parse_status,
                'overall_confidence',   NEW.overall_confidence,
                'processing_time_ms',   NEW.processing_time_ms,
                'parsed_at',            now()
            )
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_drawing_parse_done
    AFTER UPDATE OF parse_status ON dae.drawings
    FOR EACH ROW EXECUTE FUNCTION dae.publish_parse_done();

-- ── VIEW: drawing summary ─────────────────────────────────────────────────────

CREATE VIEW dae.v_drawing_summary AS
SELECT
    d.drawing_id,
    d.part_number,
    d.part_name,
    d.revision,
    d.format,
    d.drawing_type,
    d.standard,
    d.parse_status,
    d.overall_confidence,
    d.processing_time_ms,
    tb.material_raw,
    tb.mass_kg,
    mi.designation          AS material_designation,
    mi.family               AS material_family,
    mi.confidence_level     AS material_confidence,
    gs.line_count,
    gs.circle_count,
    gs.dimension_count,
    COUNT(df.feature_id)    AS feature_count,
    COUNT(tol.tolerance_id) AS tolerance_count,
    d.uploaded_by,
    d.uploaded_at,
    d.parsed_at
FROM dae.drawings d
LEFT JOIN dae.title_blocks          tb  ON tb.drawing_id  = d.drawing_id
LEFT JOIN dae.material_inferences   mi  ON mi.drawing_id  = d.drawing_id
LEFT JOIN dae.geometry_summaries    gs  ON gs.drawing_id  = d.drawing_id
LEFT JOIN dae.detected_features     df  ON df.drawing_id  = d.drawing_id
LEFT JOIN dae.tolerances            tol ON tol.drawing_id = d.drawing_id
GROUP BY d.drawing_id, tb.title_block_id, mi.inference_id, gs.summary_id;
```

---

## 9. API

### 9.1 OpenAPI 3.1 — REST endpoints

```yaml
openapi: "3.1.0"
info:
  title: Drawing Analysis Engine API
  version: "1.0.0"
  description: >
    REST API for uploading, parsing, and querying technical drawing analysis results.
    All endpoints require JWT RS256. RBAC: DAE_VIEWER / DAE_OPERATOR / DAE_ANALYST / DAE_ADMIN.

servers:
  - url: https://api.industrial-cost.io/dae/v1

paths:

  # ── Upload & Submit ─────────────────────────────────────────────────────────

  /drawings/upload:
    post:
      operationId: uploadDrawing
      summary: Upload drawing file and queue parsing
      security: [{bearerAuth: []}]
      x-rbac: [DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
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
                  description: Drawing file (PDF/DXF/DWG/STEP/IGES/PNG/TIFF/JPEG)
                part_number:
                  type: string
                priority:
                  type: integer
                  minimum: 1
                  maximum: 10
                  default: 5
      responses:
        "202":
          description: Drawing accepted, parse job queued
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/UploadResponse"
        "400":
          $ref: "#/components/responses/ValidationError"
        "413":
          description: File too large

  /drawings/{drawing_id}/reparse:
    post:
      operationId: reparseDrawing
      summary: Re-queue parsing for existing drawing
      security: [{bearerAuth: []}]
      x-rbac: [DAE_OPERATOR, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "202":
          description: Reparse queued
        "404":
          $ref: "#/components/responses/NotFound"

  # ── Drawings ────────────────────────────────────────────────────────────────

  /drawings:
    get:
      operationId: listDrawings
      summary: List drawings with filtering
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - name: part_number
          in: query
          schema: {type: string}
        - name: parse_status
          in: query
          schema:
            type: string
            enum: [QUEUED, PREPROCESSING, PARSING, OCR, GEOMETRY, FEATURES, TOLERANCES, MATERIAL, DONE, FAILED, PARTIAL]
        - name: format
          in: query
          schema:
            type: string
            enum: [PDF, DXF, DWG, STEP, IGES, SVG, PNG, TIFF, JPEG]
        - name: confidence_min
          in: query
          schema: {type: number, minimum: 0, maximum: 1}
        - name: limit
          in: query
          schema: {type: integer, default: 50, maximum: 200}
        - name: cursor
          in: query
          schema: {type: string}
      responses:
        "200":
          description: Paginated drawing list
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DrawingListResponse"

  /drawings/{drawing_id}:
    get:
      operationId: getDrawing
      summary: Get drawing details and parse results
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DrawingDetail"
        "404":
          $ref: "#/components/responses/NotFound"

    delete:
      operationId: deleteDrawing
      summary: Delete drawing and all extracted data
      security: [{bearerAuth: []}]
      x-rbac: [DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "204":
          description: Deleted

  # ── Parse Results ───────────────────────────────────────────────────────────

  /drawings/{drawing_id}/features:
    get:
      operationId: getFeatures
      summary: Get detected manufacturing features
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
        - name: feature_type
          in: query
          schema: {type: string}
        - name: confidence_min
          in: query
          schema: {type: number, minimum: 0, maximum: 1}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/DetectedFeature"

  /drawings/{drawing_id}/dimensions:
    get:
      operationId: getDimensions
      summary: Get extracted dimensions
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/ExtractedDimension"

  /drawings/{drawing_id}/tolerances:
    get:
      operationId: getTolerances
      summary: Get parsed tolerances (dimensional + GD&T)
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
        - name: tol_type
          in: query
          schema: {type: string}
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  $ref: "#/components/schemas/Tolerance"

  /drawings/{drawing_id}/material:
    get:
      operationId: getMaterial
      summary: Get material inference result
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/MaterialInference"
        "404":
          $ref: "#/components/responses/NotFound"

  /drawings/{drawing_id}/title-block:
    get:
      operationId: getTitleBlock
      summary: Get extracted title block data
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - $ref: "#/components/parameters/DrawingId"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/TitleBlock"

  # ── Jobs ────────────────────────────────────────────────────────────────────

  /jobs/{job_id}:
    get:
      operationId: getJob
      summary: Get parse job status
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      parameters:
        - name: job_id
          in: path
          required: true
          schema: {type: string, format: uuid}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ParseJob"

  # ── Search ──────────────────────────────────────────────────────────────────

  /search:
    post:
      operationId: searchDrawings
      summary: Search drawings by extracted attributes
      security: [{bearerAuth: []}]
      x-rbac: [DAE_VIEWER, DAE_OPERATOR, DAE_ANALYST, DAE_ADMIN]
      requestBody:
        required: true
        content:
          application/json:
            schema:
              $ref: "#/components/schemas/DrawingSearchRequest"
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/DrawingListResponse"

  # ── Analytics ───────────────────────────────────────────────────────────────

  /analytics/material-distribution:
    get:
      operationId: getMaterialDistribution
      summary: Material family distribution across parsed drawings
      security: [{bearerAuth: []}]
      x-rbac: [DAE_ANALYST, DAE_ADMIN]
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    family: {type: string}
                    count: {type: integer}
                    avg_cost_index: {type: number}

  /analytics/feature-frequency:
    get:
      operationId: getFeatureFrequency
      summary: Most common manufacturing features across all drawings
      security: [{bearerAuth: []}]
      x-rbac: [DAE_ANALYST, DAE_ADMIN]
      responses:
        "200":
          content:
            application/json:
              schema:
                type: array
                items:
                  type: object
                  properties:
                    feature_type: {type: string}
                    total_count: {type: integer}
                    drawing_count: {type: integer}

  /analytics/confidence-report:
    get:
      operationId: getConfidenceReport
      summary: OCR and overall parse confidence statistics
      security: [{bearerAuth: []}]
      x-rbac: [DAE_ANALYST, DAE_ADMIN]
      parameters:
        - name: since
          in: query
          schema: {type: string, format: date}
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/ConfidenceReport"

  # ── Admin ────────────────────────────────────────────────────────────────────

  /admin/queue-stats:
    get:
      operationId: getQueueStats
      summary: Parse job queue statistics
      security: [{bearerAuth: []}]
      x-rbac: [DAE_ADMIN]
      responses:
        "200":
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/QueueStats"

components:
  securitySchemes:
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT

  parameters:
    DrawingId:
      name: drawing_id
      in: path
      required: true
      schema:
        type: string
        format: uuid

  responses:
    NotFound:
      description: Resource not found
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorResponse"
    ValidationError:
      description: Validation error
      content:
        application/json:
          schema:
            $ref: "#/components/schemas/ErrorResponse"

  schemas:
    UploadResponse:
      type: object
      properties:
        drawing_id: {type: string, format: uuid}
        job_id: {type: string, format: uuid}
        status: {type: string}
        estimated_duration_ms: {type: integer}

    DrawingSearchRequest:
      type: object
      properties:
        material_family:
          type: string
        feature_types:
          type: array
          items: {type: string}
        min_tolerance_it_grade:
          type: string
        has_gdt:
          type: boolean
        standard:
          type: string
        drawing_type:
          type: string
        confidence_min:
          type: number
        limit:
          type: integer
          default: 50

    ConfidenceReport:
      type: object
      properties:
        period_start: {type: string, format: date}
        total_drawings: {type: integer}
        avg_overall_confidence: {type: number}
        avg_ocr_confidence: {type: number}
        pct_high_confidence: {type: number}
        pct_failed: {type: number}
        by_format:
          type: array
          items:
            type: object
            properties:
              format: {type: string}
              count: {type: integer}
              avg_confidence: {type: number}

    QueueStats:
      type: object
      properties:
        queued: {type: integer}
        processing: {type: integer}
        done_24h: {type: integer}
        failed_24h: {type: integer}
        avg_processing_ms: {type: number}
        p95_processing_ms: {type: number}

    ErrorResponse:
      type: object
      properties:
        error: {type: string}
        detail: {type: string}
        request_id: {type: string}
```

### 9.2 FastAPI implementation — upload endpoint

```python
import uuid
import hashlib
import aiofiles
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, BackgroundTasks
from fastapi.security import HTTPBearer

router = APIRouter(prefix="/dae/v1", tags=["Drawing Analysis Engine"])
security = HTTPBearer()

MAX_FILE_SIZE = 500 * 1024 * 1024   # 500 MB


@router.post("/drawings/upload", status_code=202)
async def upload_drawing(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    part_number: Optional[str] = Form(None),
    priority: int = Form(5),
    db: asyncpg.Connection = Depends(get_db),
    current_user: dict = Depends(require_roles(["DAE_OPERATOR", "DAE_ANALYST", "DAE_ADMIN"])),
):
    # Validate file size
    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_FILE_SIZE // 1024 // 1024}MB limit")

    # Validate format
    fmt = _detect_format_from_filename(file.filename or "")
    if fmt is None:
        raise HTTPException(status_code=400, detail=f"Unsupported file format: {file.filename}")

    # Deduplicate by checksum
    checksum = hashlib.sha256(content).hexdigest()
    existing = await db.fetchrow(
        "SELECT drawing_id FROM dae.drawings WHERE checksum_sha256 = $1", checksum
    )
    if existing:
        return {"drawing_id": str(existing["drawing_id"]), "job_id": None,
                "status": "DUPLICATE", "message": "Drawing already parsed"}

    # Store file
    drawing_id = uuid.uuid4()
    storage_uri = await _store_file(drawing_id, content, file.filename or "drawing")

    # Insert drawing record
    await db.execute(
        """
        INSERT INTO dae.drawings
            (drawing_id, part_number, format, file_size_bytes, checksum_sha256,
             filename, storage_uri, parse_status, uploaded_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7, 'QUEUED', $8)
        """,
        drawing_id, part_number, fmt.value, len(content),
        checksum, file.filename, storage_uri, current_user["sub"],
    )

    # Queue parse job
    job_id = uuid.uuid4()
    await db.execute(
        """
        INSERT INTO dae.parse_jobs (job_id, drawing_id, priority)
        VALUES ($1, $2, $3)
        """,
        job_id, drawing_id, priority,
    )

    return {
        "drawing_id": str(drawing_id),
        "job_id": str(job_id),
        "status": "QUEUED",
        "estimated_duration_ms": _estimate_duration(fmt, len(content)),
    }


def _detect_format_from_filename(filename: str) -> Optional[DrawingFormat]:
    ext = Path(filename).suffix.lower().lstrip(".")
    mapping = {
        "pdf": DrawingFormat.PDF, "dxf": DrawingFormat.DXF,
        "dwg": DrawingFormat.DWG, "step": DrawingFormat.STEP,
        "stp": DrawingFormat.STEP, "iges": DrawingFormat.IGES,
        "igs": DrawingFormat.IGES, "svg": DrawingFormat.SVG,
        "png": DrawingFormat.PNG, "tiff": DrawingFormat.TIFF,
        "tif": DrawingFormat.TIFF, "jpg": DrawingFormat.JPEG,
        "jpeg": DrawingFormat.JPEG,
    }
    return mapping.get(ext)


def _estimate_duration(fmt: DrawingFormat, size_bytes: int) -> int:
    base = {
        DrawingFormat.PDF: 8000, DrawingFormat.DXF: 3000,
        DrawingFormat.DWG: 3000, DrawingFormat.STEP: 15000,
        DrawingFormat.PNG: 12000, DrawingFormat.TIFF: 12000,
        DrawingFormat.JPEG: 10000,
    }.get(fmt, 10000)
    size_factor = max(1, size_bytes // (5 * 1024 * 1024))
    return base * size_factor


async def _store_file(drawing_id: uuid.UUID, content: bytes, filename: str) -> str:
    import os
    storage_dir = Path(os.environ.get("DAE_STORAGE_PATH", "/data/drawings"))
    target = storage_dir / str(drawing_id) / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "wb") as f:
        await f.write(content)
    return str(target)
```
