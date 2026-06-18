# Manufacturing Process Engine — Taksonomia, Hierarchia, Parametry

## 2. Process Taxonomy

### Pełna taksonomia procesów produkcyjnych

```
ROOT — Procesy Produkcyjne
│
├── CUT — Cięcie i oddzielanie materiału
│   ├── CUT.TH  — Termiczne
│   │   ├── CUT.TH.LC    — Cięcie laserowe
│   │   │   ├── CUT.TH.LC.CO2   — Laser CO₂ (do 25mm stale, tworzywa, drewno)
│   │   │   ├── CUT.TH.LC.FIB   — Laser światłowodowy (metale do 30mm)
│   │   │   └── CUT.TH.LC.NdYAG — Laser Nd:YAG (cienkie blachy, precyzja)
│   │   ├── CUT.TH.PL    — Cięcie plazmą
│   │   │   ├── CUT.TH.PL.STD   — Plasma standard (>6mm)
│   │   │   └── CUT.TH.PL.FIN   — Plasma fine (lepsza jakość krawędzi)
│   │   └── CUT.TH.OX    — Cięcie tlenowe (acetylenowo-tlenowe, grube stale)
│   ├── CUT.ME  — Mechaniczne
│   │   ├── CUT.ME.WJ    — Cięcie wodą (waterjet)
│   │   │   ├── CUT.ME.WJ.PUR   — Pure waterjet (materiały miękkie)
│   │   │   └── CUT.ME.WJ.ABR   — Abrasive waterjet (metale, kompozyty)
│   │   ├── CUT.ME.GU    — Gilotyna (blachy, taśmy)
│   │   ├── CUT.ME.BS    — Piła taśmowa
│   │   ├── CUT.ME.CS    — Piła tarczowa / ukośnica
│   │   └── CUT.ME.ST    — Wykrawanie / stancowanie
│   │       ├── CUT.ME.ST.PUN   — Wykrawanie (punching)
│   │       ├── CUT.ME.ST.BLA   — Blanking (wykrojnik)
│   │       └── CUT.ME.ST.PER   — Perforowanie
│
├── MAC — Obróbka skrawaniem (Machining)
│   ├── MAC.TU  — Toczenie (Turning)
│   │   ├── MAC.TU.EXT   — Toczenie zewnętrzne
│   │   ├── MAC.TU.INT   — Toczenie wewnętrzne / rozwiercanie
│   │   ├── MAC.TU.FAC   — Planowanie (face turning)
│   │   ├── MAC.TU.THR   — Gwintowanie toczeniem
│   │   └── MAC.TU.PAR   — Toczenie kopiowe / profilowe
│   ├── MAC.MI  — Frezowanie (Milling)
│   │   ├── MAC.MI.FAC   — Frezowanie czołowe
│   │   ├── MAC.MI.PER   — Frezowanie obwodowe
│   │   ├── MAC.MI.SLO   — Frezowanie wpustów / rowków
│   │   ├── MAC.MI.CON   — Frezowanie konturowe 2.5D
│   │   ├── MAC.MI.3AX   — Frezowanie 3-osiowe CNC
│   │   ├── MAC.MI.5AX   — Frezowanie 5-osiowe CNC
│   │   └── MAC.MI.THR   — Gwintowanie frezowaniem
│   ├── MAC.DR  — Wiercenie i operacje otworowe
│   │   ├── MAC.DR.DRL   — Wiercenie
│   │   ├── MAC.DR.REA   — Rozwiercanie (reaming)
│   │   ├── MAC.DR.TAP   — Gwintowanie wiertłem
│   │   ├── MAC.DR.CNS   — Pogłębianie stożkowe (countersinking)
│   │   └── MAC.DR.CNB   — Pogłębianie walcowe (counterboring)
│   ├── MAC.GR  — Szlifowanie (Grinding)
│   │   ├── MAC.GR.CYL   — Szlifowanie cylindryczne
│   │   ├── MAC.GR.INT   — Szlifowanie wewnętrzne
│   │   ├── MAC.GR.FLA   — Szlifowanie płaszczyzn
│   │   └── MAC.GR.PRO   — Szlifowanie profilowe
│   └── MAC.SP  — Obróbki specjalne
│       ├── MAC.SP.EDM   — Erozja elektryczna (EDM)
│       ├── MAC.SP.ECM   — Obróbka elektrochemiczna
│       └── MAC.SP.HON   — Honowanie
│
├── FOR — Formowanie i kształtowanie
│   ├── FOR.BE  — Gięcie (Bending)
│   │   ├── FOR.BE.AIR   — Gięcie powietrzne (air bending)
│   │   ├── FOR.BE.BOT   — Gięcie do oporu (bottom bending)
│   │   ├── FOR.BE.ROL   — Rolowanie / walcowanie
│   │   └── FOR.BE.TUB   — Gięcie rur
│   ├── FOR.ST  — Tłoczenie (Stamping / Pressing)
│   │   ├── FOR.ST.DEP   — Tłoczenie głębokie (deep drawing)
│   │   ├── FOR.ST.EMB   — Wytłaczanie (embossing)
│   │   ├── FOR.ST.FIN   — Formowanie obrzeży (flanging)
│   │   └── FOR.ST.HYD   — Hydroformowanie
│   └── FOR.RO  — Obróbka plastyczna na zimno / gorąco
│       ├── FOR.RO.FOR   — Kucie (forging)
│       └── FOR.RO.EXT   — Wyciskanie (extrusion)
│
├── JOI — Łączenie (Joining)
│   ├── JOI.WE  — Spawanie (Welding)
│   │   ├── JOI.WE.MIG   — Spawanie MIG/MAG (GMAW, 135/136)
│   │   ├── JOI.WE.TIG   — Spawanie TIG (GTAW, 141)
│   │   ├── JOI.WE.MMA   — Spawanie elektrodą otuloną (111)
│   │   ├── JOI.WE.SPO   — Zgrzewanie punktowe (resistance spot)
│   │   ├── JOI.WE.LAZ   — Spawanie laserowe
│   │   ├── JOI.WE.FSW   — Spawanie tarciowe FSW (aluminium)
│   │   └── JOI.WE.PLB   — Lutowanie twarde/miękkie (brazing/soldering)
│   ├── JOI.GL  — Klejenie (Adhesive Bonding)
│   │   ├── JOI.GL.STR   — Klejenie konstrukcyjne (2-komponentowe)
│   │   ├── JOI.GL.PRS   — Klejenie z dociskiem (press bonding)
│   │   └── JOI.GL.HOT   — Klejenie termoplastyczne (hot melt)
│   └── JOI.ME  — Łączenie mechaniczne
│       ├── JOI.ME.SCR   — Skręcanie / wkrętowanie
│       ├── JOI.ME.RIV   — Nitowanie
│       └── JOI.ME.PRE   — Wciskanie (press fit)
│
├── ASS — Montaż (Assembly)
│   ├── ASS.MAN  — Montaż ręczny
│   ├── ASS.SEM  — Montaż półautomatyczny
│   ├── ASS.AUT  — Montaż automatyczny / robotyczny
│   └── ASS.PCB  — Montaż elektroniki (SMT / THT)
│
└── FIN — Wykańczanie powierzchni (Finishing)
    ├── FIN.COA  — Powlekanie (Coating)
    │   ├── FIN.COA.POW  — Malowanie proszkowe (powder coating)
    │   ├── FIN.COA.WET  — Malowanie mokre / natryskowe
    │   ├── FIN.COA.ZIN  — Cynkowanie ogniowe (hot-dip galvanizing)
    │   └── FIN.COA.ELC  — Cynkowanie elektrolityczne
    ├── FIN.ELC  — Wykańczanie elektrochemiczne
    │   ├── FIN.ELC.ANO  — Anodowanie (aluminium)
    │   │   ├── FIN.ELC.ANO.NAT — Anodowanie naturalne (bezbarwne)
    │   │   ├── FIN.ELC.ANO.COL — Anodowanie barwione
    │   │   └── FIN.ELC.ANO.HAR — Anodowanie twarde (hard anodizing)
    │   ├── FIN.ELC.GAL  — Galwanizacja (electroplating)
    │   │   ├── FIN.ELC.GAL.NI  — Niklowanie
    │   │   ├── FIN.ELC.GAL.CR  — Chromowanie
    │   │   └── FIN.ELC.GAL.CU  — Miedziowanie
    │   └── FIN.ELC.PAS  — Pasywacja (stainless steel)
    └── FIN.MEC  — Wykańczanie mechaniczne
        ├── FIN.MEC.POL  — Polerowanie
        │   ├── FIN.MEC.POL.MEC — Polerowanie mechaniczne
        │   └── FIN.MEC.POL.ELC — Polerowanie elektrolityczne
        └── FIN.MEC.BLA  — Piaskowanie / śrutowanie
            ├── FIN.MEC.BLA.SAN — Piaskowanie (sand blasting)
            ├── FIN.MEC.BLA.SHO — Śrutowanie (shot blasting)
            └── FIN.MEC.BLA.GRI — Śrutowanie kulkami szklanymi
```

---

## 3. Process Hierarchy

### Model hierarchii procesu

```
Level 1: Process Class         (CUT, MAC, FOR, JOI, ASS, FIN)
Level 2: Process Family        (CUT.TH, MAC.TU, FOR.BE, JOI.WE, ...)
Level 3: Process Type          (CUT.TH.LC, MAC.MI.5AX, JOI.WE.MIG, ...)
Level 4: Process Variant       (CUT.TH.LC.FIB, FIN.ELC.ANO.HAR, ...)
Level 5: Process Instance      (konkretna operacja na konkretnej maszynie)
```

### Relacja hierarchia → encje systemu

```
ProcessClass (level 1)
    └── ProcessFamily (level 2)
            └── ProcessType (level 3) ←── CORE ENTITY
                    ├── ProcessVariant (level 4)
                    └── ProcessOperation (level 5)
                            ├── assigned to: Machine
                            ├── uses: Tool[]
                            ├── requires: Operator (skill)
                            └── has: CostModel
```

### Reguły dziedziczenia parametrów

Parametry dziedziczone są w dół hierarchii z możliwością nadpisania:

```
ProcessType.default_feed_rate = 3000 mm/min
    └── ProcessVariant.feed_rate = inherited (3000) unless overridden
            └── ProcessOperation.feed_rate = machine-specific override (2800)
```

Implementacja:

```python
class ProcessParameterResolver:
    """
    Resolves effective parameter value using inheritance chain.
    Priority: Operation > Variant > Type > Family > Class > Global Default
    """
    def resolve(self, operation: ProcessOperation, param_key: str) -> ParameterValue:
        chain = [
            operation,
            operation.variant,
            operation.process_type,
            operation.process_type.family,
            operation.process_type.family.process_class,
        ]
        for node in chain:
            val = node.parameters.get(param_key)
            if val is not None and not val.is_inherited:
                return val
        return GlobalDefaults.get(param_key)
```

---

## 4. Process Parameters

### Parametry wspólne (wszystkie procesy)

| Parametr | Klucz | Typ | Jednostka | Opis |
|----------|-------|-----|-----------|------|
| Nazwa operacji | `operation_name` | STRING | — | Opis operacji |
| Czas nastawu | `setup_time_min` | NUMERIC | min | Czas przygotowania |
| Czas jednostkowy | `cycle_time_sec` | NUMERIC | s | Czas cyklu na 1 szt. |
| Min. wielkość partii | `min_batch_size` | INTEGER | szt. | MOQ dla operacji |
| Maks. wielkość partii | `max_batch_size` | INTEGER | szt. | |
| Moc zainstalowana | `installed_power_kw` | NUMERIC | kW | Moc maszyny |
| Moc efektywna | `effective_power_kw` | NUMERIC | kW | Moc w trakcie obróbki |
| Wymagany poziom operatora | `operator_skill_level` | ENUM | — | BASIC/STANDARD/EXPERT |
| Wskaźnik odpadów | `scrap_rate_pct` | NUMERIC | % | Domyślny wskaźnik |
| Dokładność wymiarowa | `dimensional_accuracy_mm` | NUMERIC | mm | Tolerancja IT |
| Chropowatość Ra | `surface_roughness_ra_um` | NUMERIC | µm | Wynikowa Ra |

### Parametry — Cięcie laserowe (CUT.TH.LC)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Moc lasera | `laser_power_kw` | NUMERIC | kW | 1–20 |
| Prędkość cięcia | `cutting_speed_m_min` | NUMERIC | m/min | 0.5–30 |
| Gaz procesowy | `assist_gas` | ENUM | — | N2, O2, AIR |
| Ciśnienie gazu | `gas_pressure_bar` | NUMERIC | bar | 5–25 |
| Ogniskowanie | `focus_position_mm` | NUMERIC | mm | -2.0 – +2.0 |
| Grubość materiału | `material_thickness_mm` | NUMERIC | mm | 0.5–30 |
| Szerokość cięcia (kerf) | `kerf_width_mm` | NUMERIC | mm | 0.1–0.5 |
| Jakość krawędzi | `edge_quality_class` | ENUM | — | ISO9013: 1–5 |
| Prędkość przebicia | `pierce_speed_m_min` | NUMERIC | m/min | 0.1–2.0 |
| Czas przebicia | `pierce_time_sec` | NUMERIC | s | 0.1–5.0 |
| Efektywność nestingu | `nesting_efficiency_pct` | NUMERIC | % | 60–92 |
| Czas akceleracji | `acceleration_time_sec` | NUMERIC | s | 0.05–0.5 |
| Głowica | `laser_head_type` | STRING | — | np. Precitec, BEO |

### Parametry — Frezowanie CNC (MAC.MI.3AX / 5AX)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Prędkość obrotowa | `spindle_speed_rpm` | NUMERIC | RPM | 100–30 000 |
| Prędkość posuwu | `feed_rate_mm_min` | NUMERIC | mm/min | 50–20 000 |
| Posuw na ostrze | `feed_per_tooth_mm` | NUMERIC | mm/ostrze | 0.01–0.5 |
| Głębokość skrawania osiowa | `axial_depth_mm` | NUMERIC | mm | 0.1–50 |
| Głębokość skrawania promieniowa | `radial_depth_mm` | NUMERIC | mm | 0.1–50 |
| Naddatek wykończeniowy | `finish_allowance_mm` | NUMERIC | mm | 0.1–1.0 |
| Liczba osi | `axis_count` | INTEGER | — | 3, 4, 5 |
| Chłodziwo | `coolant_type` | ENUM | — | DRY, MQL, FLOOD, AIR |
| Strategia obróbki | `toolpath_strategy` | ENUM | — | RASTER, CONTOUR, TROCHOIDAL, ADAPTIVE |
| Materiał narzędzia | `tool_material` | ENUM | — | HSS, HM, CBN, PCD |
| Średnica freza | `cutter_diameter_mm` | NUMERIC | mm | 1–250 |
| Liczba zębów | `tooth_count` | INTEGER | — | 2–12 |
| Tolerancja IT | `tolerance_grade` | ENUM | — | IT6–IT14 |

### Parametry — Toczenie CNC (MAC.TU)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Prędkość skrawania | `cutting_speed_m_min` | NUMERIC | m/min | 20–500 |
| Posuw | `feed_mm_rev` | NUMERIC | mm/obr. | 0.05–2.0 |
| Głębokość skrawania | `depth_of_cut_mm` | NUMERIC | mm | 0.1–20 |
| Średnica wejściowa | `input_diameter_mm` | NUMERIC | mm | |
| Średnica wyjściowa | `output_diameter_mm` | NUMERIC | mm | |
| Długość toczenia | `turning_length_mm` | NUMERIC | mm | |
| Rodzaj uchwytu | `chuck_type` | ENUM | — | 3JAW, 4JAW, COLLET, FACE |
| Podtrzymka | `tailstock_required` | BOOLEAN | — | |
| Typ wkładki | `insert_type` | STRING | — | np. CNMG120408 |

### Parametry — Wiercenie (MAC.DR.DRL)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Prędkość obrotowa | `spindle_speed_rpm` | NUMERIC | RPM | 50–15 000 |
| Posuw | `feed_mm_rev` | NUMERIC | mm/obr. | 0.01–0.5 |
| Średnica wiertła | `drill_diameter_mm` | NUMERIC | mm | 0.1–80 |
| Głębokość otworu | `hole_depth_mm` | NUMERIC | mm | |
| Typ otworu | `hole_type` | ENUM | — | THROUGH, BLIND, STEPPED |
| Tolerancja otworu | `hole_tolerance` | STRING | — | H7, H8, H11 |
| Strategia | `drill_strategy` | ENUM | — | STANDARD, PECK, DEEP |
| Chłodziwo | `coolant_type` | ENUM | — | DRY, MQL, THROUGH |
| Przebicie | `through_coolant` | BOOLEAN | — | |

### Parametry — Gięcie prasą krawędziową (FOR.BE.AIR)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Siła gięcia | `bending_force_kn` | NUMERIC | kN | 10–3000 |
| Długość gięcia | `bending_length_mm` | NUMERIC | mm | |
| Kąt gięcia | `bend_angle_deg` | NUMERIC | ° | 0–180 |
| Promień gięcia | `bend_radius_mm` | NUMERIC | mm | |
| Grubość materiału | `material_thickness_mm` | NUMERIC | mm | 0.5–20 |
| Typ matrycy | `die_type` | ENUM | — | V_DIE, U_DIE, ACUTE |
| Szerokość matrycy V | `v_die_width_mm` | NUMERIC | mm | 6–120 |
| Wytrzymałość materiału | `material_tensile_mpa` | NUMERIC | MPa | |
| Sprężynowanie | `springback_angle_deg` | NUMERIC | ° | 0–10 |
| Naddatek gięcia | `bend_allowance_mm` | NUMERIC | mm | |
| Powtarzalność | `repeatability_mm` | NUMERIC | mm | ±0.01–±0.1 |

### Parametry — Spawanie MIG/MAG (JOI.WE.MIG)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Natężenie prądu | `welding_current_a` | NUMERIC | A | 50–500 |
| Napięcie | `welding_voltage_v` | NUMERIC | V | 15–45 |
| Prędkość spawania | `travel_speed_mm_min` | NUMERIC | mm/min | 150–800 |
| Prędkość podawania drutu | `wire_feed_speed_m_min` | NUMERIC | m/min | 3–20 |
| Średnica drutu | `wire_diameter_mm` | NUMERIC | mm | 0.6–1.6 |
| Gaz osłonowy | `shielding_gas` | ENUM | — | CO2, Ar+CO2, Ar+O2 |
| Przepływ gazu | `gas_flow_l_min` | NUMERIC | l/min | 8–20 |
| Energia liniowa | `heat_input_kj_mm` | NUMERIC | kJ/mm | 0.3–2.0 |
| Spoina | `weld_type` | ENUM | — | BUTT, FILLET, LAP, EDGE |
| Grubość spoiny a | `weld_throat_mm` | NUMERIC | mm | |
| Długość spoiny | `weld_length_mm` | NUMERIC | mm | |
| Norma | `weld_standard` | ENUM | — | ISO 3834, EN 1090 |
| Klasa wykonania | `execution_class` | ENUM | — | EXC1, EXC2, EXC3, EXC4 |
| Współczynnik jarzenia | `arc_efficiency` | NUMERIC | — | 0.6–0.9 |

### Parametry — Malowanie proszkowe (FIN.COA.POW)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Temperatura utwardzania | `cure_temp_c` | NUMERIC | °C | 160–220 |
| Czas utwardzania | `cure_time_min` | NUMERIC | min | 10–30 |
| Grubość powłoki | `coating_thickness_um` | NUMERIC | µm | 40–120 |
| Zużycie proszku | `powder_consumption_g_m2` | NUMERIC | g/m² | 80–200 |
| Efektywność nanoszenia | `transfer_efficiency_pct` | NUMERIC | % | 50–98 |
| Preparat | `pretreatment_type` | ENUM | — | PHOSPHATE, CHROME_FREE, NONE |
| Napięcie pistoletu | `gun_voltage_kv` | NUMERIC | kV | 60–100 |
| Rodzaj proszku | `powder_type` | ENUM | — | EPOXY, POLYESTER, EPOXY_POLYESTER |
| RAL kolor | `ral_color` | STRING | — | np. RAL 9005 |
| Połysk | `gloss_level` | ENUM | — | MATT, SEMI, GLOSS |
| Norma | `coating_standard` | ENUM | — | ISO 12944, QUALICOAT |

### Parametry — Anodowanie (FIN.ELC.ANO)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Gęstość prądu | `current_density_a_dm2` | NUMERIC | A/dm² | 1–3 (standard), 3–5 (hard) |
| Napięcie | `voltage_v` | NUMERIC | V | 10–30 |
| Temperatura kąpieli | `bath_temp_c` | NUMERIC | °C | 15–22 (std), 0–5 (hard) |
| Czas anodowania | `anodizing_time_min` | NUMERIC | min | 20–60 |
| Grubość warstwy | `oxide_thickness_um` | NUMERIC | µm | 5–25 (std), 25–100 (hard) |
| Elektrolit | `electrolyte` | STRING | — | H₂SO₄ 15–20% |
| Uszczelnianie | `sealing_type` | ENUM | — | HOT_WATER, COLD, PTFE, DICHROMATE |
| Barwienie | `dyeing` | BOOLEAN | — | |
| Kolor | `anodize_color` | STRING | — | |
| Norma | `standard` | ENUM | — | ISO 7599, MIL-A-8625 |

### Parametry — Piaskowanie (FIN.MEC.BLA.SAN)

| Parametr | Klucz | Typ | Jednostka | Zakres typowy |
|----------|-------|-----|-----------|---------------|
| Ciśnienie | `blast_pressure_bar` | NUMERIC | bar | 4–8 |
| Medium | `blast_media` | ENUM | — | SAND, STEEL_SHOT, GLASS_BEAD, ALUMINA |
| Granulacja | `media_grit` | STRING | — | S110–S550, G16–G120 |
| Odległość pistoletu | `nozzle_distance_mm` | NUMERIC | mm | 100–300 |
| Kąt natrysku | `blast_angle_deg` | NUMERIC | ° | 45–90 |
| Stopień czystości | `cleanliness_grade` | ENUM | — | Sa1, Sa2, Sa2.5, Sa3 |
| Profil chropowatości | `surface_profile_um` | NUMERIC | µm | 25–100 |
| Norma | `standard` | ENUM | — | ISO 8501, SSPC |
