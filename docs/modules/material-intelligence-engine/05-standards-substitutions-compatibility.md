# Material Intelligence Engine — Normy, Zamienniki, Kompatybilność

## 9. Material Standards — ISO, DIN, EN, ASTM

### Architektura mapowania norm

System przechowuje pełną sieć równoważności między systemami normalizacyjnymi.

```
material_standards_map
├── standard_definitions     -- definicje norm (numer, rok, organizacja)
├── material_standards       -- powiązania materiał ↔ norma
└── standard_cross_reference -- tabele odpowiedniości między systemami
```

### Tabela cross-reference norm dla stali

```sql
CREATE TABLE standard_cross_reference (
    xref_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    system_a          standard_system_enum NOT NULL,
    grade_a           VARCHAR(50) NOT NULL,
    system_b          standard_system_enum NOT NULL,
    grade_b           VARCHAR(50) NOT NULL,
    equivalence_type  equivalence_type_enum NOT NULL,
    notes             TEXT,
    CONSTRAINT uq_xref UNIQUE (system_a, grade_a, system_b, grade_b)
);
```

### Główne powiązania norm — stale węglowe

| EN (Europa) | DIN (Niemcy) | ASTM (USA) | JIS (Japonia) | Uwagi |
|-------------|--------------|------------|---------------|-------|
| S235JR | St37-2 | A36 | SS400 | Równoważne (Rm, Re) |
| S235J0 | St37-3U | — | — | Udarność 0°C |
| S235J2 | St37-3N | — | — | Udarność -20°C |
| S355JR | St52-3U | A572 Gr.50 | SM490 | Równoważne |
| S355J2 | St52-3N | — | — | Udarność -20°C |
| DC01 | St12 | — | SPCC | Blacha zimnowalcowana |
| DC04 | St14 | — | SPCD | Głębokotłoczna |
| DX51D+Z | — | A653 CS Type B | SGCC | Ocynkowana |

### Główne powiązania norm — stale nierdzewne

| EN numer | EN skrót | AISI/SAE | UNS | DIN alt |
|----------|----------|----------|-----|---------|
| 1.4301 | X5CrNi18-10 | 304 | S30400 | — |
| 1.4307 | X2CrNi18-9 | 304L | S30403 | — |
| 1.4401 | X5CrNiMo17-12-2 | 316 | S31600 | — |
| 1.4404 | X2CrNiMo17-12-2 | 316L | S31603 | — |
| 1.4016 | X6Cr17 | 430 | S43000 | — |
| 1.4462 | X2CrNiMoN22-5-3 | 2205 | S31803 | Duplex |

### Główne powiązania norm — aluminium

| EN | ISO | AA (Alcoa) | Opis |
|----|-----|------------|------|
| EN AW-1050A | Al 99.5 | 1050 | Czyste aluminium |
| EN AW-5052 | AlMg2.5 | 5052 | Stop Mg |
| EN AW-6061 | AlMgSi1 | 6061 | Stop Mg+Si |
| EN AW-6082 | AlSi1MgMn | 6082 | Europejski 6061 |
| EN AW-7075 | AlZnMgCu1.5 | 7075 | Lotnicze |

### Główne powiązania norm — tworzywa

| Materiał | ISO | DIN | ASTM |
|----------|-----|-----|------|
| ABS | ISO 2580 | — | ASTM D4673 |
| PC | ISO 7391 | — | ASTM D3935 |
| PA6 | ISO 1874-1 | DIN 7728 | ASTM D5510 |
| PA66 | ISO 1874-1 | — | ASTM D5510 |
| POM | ISO 9988 | — | ASTM D4181 |
| PE | ISO 1872 | DIN 16776 | ASTM D4976 |
| PP | ISO 1873 | DIN 16774 | ASTM D4101 |
| PET | ISO 7792 | — | ASTM D5927 |

---

## 10. Material Substitutions — Algorytmy zamienników

### Model decyzyjny substytucji

```
SubstitutionEngine
├── SubstitutionScorer        -- oblicza zgodność techniczną
├── CostImpactCalculator      -- kalkuluje zmianę kosztu
├── AvailabilityChecker       -- sprawdza dostępność u dostawców
├── ProcessCompatibilityCheck -- weryfikuje kompatybilność z procesami
└── SubstitutionRanker        -- sortuje wyniki końcowe
```

### Algorytm scoring zamienników

```python
class SubstitutionScorer:
    """
    Composite scoring for material substitution.
    Score: 0-100, higher = better substitute.
    """

    WEIGHTS = {
        'mechanical_compatibility': 0.30,
        'process_compatibility':    0.25,
        'cost_impact':              0.20,
        'availability':             0.15,
        'regulatory_compliance':    0.10,
    }

    def score(self, source: Material, candidate: Material,
              context: SubstitutionContext) -> SubstitutionScore:

        scores = {
            'mechanical_compatibility': self._score_mechanical(source, candidate),
            'process_compatibility':    self._score_process(source, candidate, context.processes),
            'cost_impact':              self._score_cost(source, candidate),
            'availability':             self._score_availability(candidate),
            'regulatory_compliance':    self._score_regulatory(source, candidate, context),
        }

        total = sum(s * self.WEIGHTS[k] for k, s in scores.items())
        return SubstitutionScore(
            total=round(total, 2),
            breakdown=scores,
            go_nogo=self._determine_go_nogo(scores, context)
        )

    def _score_mechanical(self, src: Material, cand: Material) -> float:
        """
        Compares key mechanical properties.
        Returns 0-100 based on how close candidate is to source.
        Critical properties (Rm, Re) are hard constraints.
        """
        src_props = src.mechanical_properties
        cand_props = cand.mechanical_properties

        if not cand_props.tensile_strength_mpa >= src_props.tensile_strength_mpa * 0.95:
            return 0.0  # Hard constraint: min. Rm within 5%

        deltas = []
        if src_props.yield_strength_mpa:
            delta_re = abs(cand_props.yield_strength_mpa - src_props.yield_strength_mpa)
            deltas.append(max(0, 100 - (delta_re / src_props.yield_strength_mpa * 100)))

        if src_props.density_kg_m3 and cand_props.density_kg_m3:
            delta_density = abs(cand_props.density_kg_m3 - src_props.density_kg_m3)
            deltas.append(max(0, 100 - (delta_density / src_props.density_kg_m3 * 100 * 2)))

        return sum(deltas) / len(deltas) if deltas else 75.0

    def _score_cost(self, src: Material, cand: Material) -> float:
        """Higher score = cheaper or equal candidate."""
        src_price = get_current_price(src.material_id)
        cand_price = get_current_price(cand.material_id)
        if not src_price or not cand_price:
            return 50.0  # Neutral if no price data

        ratio = cand_price / src_price
        if ratio <= 0.90:  return 100.0   # >10% cheaper
        elif ratio <= 1.0:  return 80.0   # up to same price
        elif ratio <= 1.10: return 60.0   # up to 10% more expensive
        elif ratio <= 1.25: return 40.0   # up to 25% more expensive
        else:               return 20.0   # >25% more expensive

    def _determine_go_nogo(self, scores: dict, context: SubstitutionContext) -> str:
        """
        GO:         total >= 70 AND mechanical >= 60 AND process >= 50
        CONDITIONAL: total >= 50 AND (mechanical >= 50 OR process_change_needed)
        NOGO:       total < 50 OR mechanical == 0
        """
        if scores['mechanical_compatibility'] == 0:
            return 'NOGO'
        total = sum(s * self.WEIGHTS[k] for k, s in scores.items())
        if total >= 70 and scores['mechanical_compatibility'] >= 60:
            return 'GO'
        elif total >= 50:
            return 'CONDITIONAL'
        return 'NOGO'
```

### Klasyfikacja zamienników

| Typ | Definicja | Wymagania |
|-----|-----------|-----------|
| DIRECT | Bezpośrednia zamiana bez zmian | Brak działań dodatkowych |
| CONDITIONAL | Zamiana z warunkami | Weryfikacja inżynierska |
| PARTIAL | Tylko w części zastosowań | Określone procesy/produkty |
| PROCESS_CHANGE_NEEDED | Wymaga zmiany procesu | Zmiana parametrów technologicznych |

### Reguły automatycznej substytucji (dane statyczne)

```yaml
# Predefiniowane reguły substytucji w systemie
substitution_rules:

  - source: S235JR
    substitutes:
      - grade: S355JR
        type: DIRECT
        note: "S355 > S235 — zawsze zastępowalny, wyższy koszt ~15-25%"
        score: 85
      - grade: A36
        type: DIRECT
        note: "Odpowiednik ASTM, identyczne właściwości"
        score: 90

  - source: DC01
    substitutes:
      - grade: DC03
        type: DIRECT
        note: "DC03 wyższe tłoczenie, bezpośredni zamiennik"
        score: 88
      - grade: DC04
        type: DIRECT
        note: "DC04 najwyższe tłoczenie, wyższy koszt"
        score: 75

  - source: "304"
    substitutes:
      - grade: "316"
        type: CONDITIONAL
        note: "316 wyższy Mo — lepsza korozja, ~20% droższy"
        score: 70
        conditions:
          apply_when: "media chlorkowe lub morskie"
      - grade: "304L"
        type: DIRECT
        note: "L — niższy C, spawanie bez wyżarzania, nieznacznie tańszy"
        score: 92

  - source: ABS
    substitutes:
      - grade: PC+ABS
        type: CONDITIONAL
        note: "Lepsze właściwości term., droższy, wymaga weryfikacji narzędzi"
        score: 65
      - grade: HIPS
        type: CONDITIONAL
        note: "Tańszy, niższe właściwości udarnościowe"
        score: 55

  - source: PA6
    substitutes:
      - grade: PA66
        type: CONDITIONAL
        note: "PA66 wyższa temp. Vicat, nieznacznie droższy"
        score: 78
      - grade: "PA6-GF30"
        type: CONDITIONAL
        note: "Wyższy moduł, masa +5%, inne parametry wtrysku"
        score: 55
```

---

## 11. Compatibility Engine — Proces ↔ Materiał

### Architektura silnika kompatybilności

```
CompatibilityEngine
├── ProcessRegistry         -- rejestr procesów produkcyjnych
├── CompatibilityMatrix     -- macierz materiał ↔ proces
├── ParameterResolver       -- wyznacza parametry dla kombinacji
├── ConstraintValidator     -- waliduje ograniczenia (grubość, twardość)
└── RecommendationBuilder   -- buduje rekomendacje procesowe
```

### ProcessRegistry — procesy produkcyjne

```
CUTTING
├── LASER_CO2        -- Cięcie laserem CO₂
├── LASER_FIBER      -- Cięcie laserem światłowodowym
├── PLASMA_CUT       -- Cięcie plazmą
├── WATERJET         -- Cięcie wodą
├── GUILLOTINE       -- Gilotyna (blachy)
├── BAND_SAW         -- Piła taśmowa
└── CIRCULAR_SAW     -- Piła tarczowa

FORMING
├── PRESS_BRAKE      -- Gięcie w prasie krawędziowej
├── ROLL_FORMING     -- Rollforming (profilowanie)
├── DEEP_DRAW        -- Tłoczenie głębokie
├── STAMPING         -- Tłoczenie
├── HYDROFORM        -- Hydroformowanie
└── TUBE_BENDING     -- Gięcie rur

WELDING
├── MIG_MAG          -- Spawanie MIG/MAG (135/136)
├── TIG              -- Spawanie TIG (141)
├── SPOT_WELD        -- Zgrzewanie punktowe
├── LASER_WELD       -- Spawanie laserowe
├── PLASMA_WELD      -- Spawanie plazmowe
└── FRICTION_STIR    -- Spawanie FSW (aluminium)

MACHINING
├── TURNING          -- Toczenie CNC
├── MILLING          -- Frezowanie CNC
├── DRILLING         -- Wiercenie
├── GRINDING         -- Szlifowanie
└── EDM              -- Erozja elektryczna

COATING
├── POWDER_COAT      -- Malowanie proszkowe
├── WET_PAINT        -- Malowanie mokre
├── GALVANIZE_HOT    -- Cynkowanie ogniowe
├── GALVANIZE_ELEC   -- Cynkowanie elektrolityczne
├── ANODIZE          -- Anodowanie (Al)
└── PVD              -- PVD/CVD

MOLDING
├── INJECTION        -- Wtrysk
├── EXTRUSION        -- Wytłaczanie
├── BLOW_MOLDING     -- Wytłaczanie z rozdmuchiwaniem
├── COMPRESSION      -- Prasowanie (kompozyty)
└── RTM              -- Resin Transfer Molding
```

### Macierz kompatybilności — fragment

| Materiał | LASER_FIBER | PRESS_BRAKE | MIG_MAG | POWDER_COAT | INJECTION |
|----------|:-----------:|:-----------:|:-------:|:-----------:|:---------:|
| S235 | OPTIMAL | OPTIMAL | OPTIMAL | OPTIMAL | FORBIDDEN |
| S355 | OPTIMAL | ACCEPTABLE | OPTIMAL | OPTIMAL | FORBIDDEN |
| DC01 | OPTIMAL | OPTIMAL | OPTIMAL | OPTIMAL | FORBIDDEN |
| DX51 | ACCEPTABLE* | OPTIMAL | ACCEPTABLE** | CAUTION*** | FORBIDDEN |
| 304 | ACCEPTABLE† | ACCEPTABLE | OPTIMAL | OPTIMAL | FORBIDDEN |
| 316 | ACCEPTABLE† | ACCEPTABLE | OPTIMAL | OPTIMAL | FORBIDDEN |
| Al 6082 | OPTIMAL | OPTIMAL | NOT_REC | CAUTION | FORBIDDEN |
| Cu | ACCEPTABLE | ACCEPTABLE | NOT_REC | NOT_REC | FORBIDDEN |
| ABS | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | OPTIMAL |
| PA6 | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | OPTIMAL |
| PP | FORBIDDEN | FORBIDDEN | FORBIDDEN | FORBIDDEN | OPTIMAL |
| MDF | ACCEPTABLE | FORBIDDEN | FORBIDDEN | NOT_REC | FORBIDDEN |

*DX51 laser: możliwe odparowanie cynku — wentylacja  
**DX51 MIG: spawanie przez cynk możliwe, lecz pory — wymagane odcynkowanie  
***DX51 powder coat: wymaga pretreatmentu  
†Stainless laser: wyższy odbits — parametry niższe, N₂ jako gaz

### Parametry procesu dla kombinacji materiał–proces

```json
{
  "material_id": "mat-s355-hr-sheet",
  "process_code": "LASER_FIBER",
  "parameters": {
    "power_range_kw": [3, 12],
    "speed_factor": 1.0,
    "cutting_gas": "N2_or_O2",
    "assist_gas_pressure_bar": [12, 20],
    "focus_position_mm": [-1.0, 0.5],
    "min_thickness_mm": 0.8,
    "max_thickness_mm": 25.0,
    "kerf_width_mm": {"1mm": 0.12, "5mm": 0.25, "10mm": 0.40},
    "heat_affected_zone": "moderate",
    "edge_quality": "ISO9013_class_3_4",
    "scrap_factor_override_pct": 8.0
  }
}
```

```json
{
  "material_id": "mat-abs-natural",
  "process_code": "INJECTION",
  "parameters": {
    "melt_temp_c": [220, 260],
    "mold_temp_c": [40, 80],
    "injection_pressure_bar": [600, 1400],
    "holding_pressure_bar": [400, 800],
    "cooling_time_s": [10, 30],
    "shrinkage_pct": [0.4, 0.8],
    "draft_angle_deg_min": 1.0,
    "wall_thickness_min_mm": 1.0,
    "wall_thickness_max_mm": 4.0,
    "gate_types": ["pin_point", "edge", "submarine"],
    "runner_type": "hot_or_cold",
    "scrap_factor_override_pct": 3.0
  }
}
```

### ParameterResolver — pseudokod

```python
class ParameterResolver:
    def resolve(self, material_id: str, process_code: str,
                part_geometry: PartGeometry) -> ProcessParameters:

        compat = self.get_compatibility(material_id, process_code)
        if compat.level == 'FORBIDDEN':
            raise IncompatibleProcessError(material_id, process_code)

        base_params = self.get_base_parameters(process_code)
        mat_overrides = compat.parameter_overrides
        geo_adjustments = self.adjust_for_geometry(base_params, part_geometry)

        return ProcessParameters(
            compatibility_level=compat.level,
            speed_factor=compat.speed_factor,
            waste_factor=compat.waste_factor_override or base_params.waste_factor,
            parameters=merge(base_params, mat_overrides, geo_adjustments),
            warnings=self.collect_warnings(compat)
        )
```
