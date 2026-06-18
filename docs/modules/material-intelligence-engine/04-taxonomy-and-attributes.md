# Material Intelligence Engine — Taksonomia i Atrybuty

## 6. Material Taxonomy — Pełna Hierarchia

```
ROOT
├── MET — Metale
│   ├── MET.CS — Stale węglowe i konstrukcyjne
│   │   ├── MET.CS.HR — Hot-rolled (walcowane na gorąco)
│   │   │   ├── MET.CS.HR.S235   — S235JR/J0/J2
│   │   │   ├── MET.CS.HR.S355   — S355JR/J0/J2/K2
│   │   │   └── MET.CS.HR.S275   — S275JR
│   │   ├── MET.CS.CR — Cold-rolled (walcowane na zimno)
│   │   │   ├── MET.CS.CR.DC01   — DC01 (miękka blacha)
│   │   │   └── MET.CS.CR.DC03   — DC03 (głębokotłoczna)
│   │   └── MET.CS.GI — Ocynkowane ogniowo
│   │       ├── MET.CS.GI.DX51D  — DX51D (ogólne przeznaczenie)
│   │       └── MET.CS.GI.DX53D  — DX53D (tłoczna)
│   ├── MET.SS — Stale nierdzewne
│   │   ├── MET.SS.A — Austenityczne
│   │   │   ├── MET.SS.A.304   — 1.4301 / AISI 304
│   │   │   ├── MET.SS.A.316   — 1.4401 / AISI 316
│   │   │   └── MET.SS.A.316L  — 1.4404 / AISI 316L
│   │   ├── MET.SS.F — Ferrytyczne
│   │   │   └── MET.SS.F.430   — 1.4016 / AISI 430
│   │   └── MET.SS.D — Duplex
│   │       └── MET.SS.D.2205  — 1.4462
│   ├── MET.AL — Aluminium i stopy
│   │   ├── MET.AL.1XXX — Aluminium czyste (≥99%)
│   │   ├── MET.AL.5XXX — Stop z magnezem (5052, 5083)
│   │   ├── MET.AL.6XXX — Stop z Mg+Si (6061, 6063, 6082)
│   │   └── MET.AL.7XXX — Stop z cynkiem (7075)
│   ├── MET.CU — Miedź i stopy
│   │   ├── MET.CU.PU — Miedź czysta (Cu-ETP, Cu-OF)
│   │   ├── MET.CU.BR — Mosiądz (CuZn37, CuZn39Pb3)
│   │   └── MET.CU.BZ — Brąz (CuSn8, CuAl10)
│   └── MET.SP — Stale specjalne
│       ├── MET.SP.HS — Narzędziowe (HSS)
│       └── MET.SP.ST — Nierdzewne kwasoodporne (317L, 904L)
│
├── POL — Tworzywa sztuczne
│   ├── POL.TP — Termoplastyczne
│   │   ├── POL.TP.STY — Na bazie styrenu
│   │   │   ├── POL.TP.STY.ABS  — Akrylonitryl-butadien-styren
│   │   │   └── POL.TP.STY.PS   — Polistyren
│   │   ├── POL.TP.PC  — Poliwęglan
│   │   ├── POL.TP.PA  — Poliamidy
│   │   │   ├── POL.TP.PA.PA6   — Poliamid 6
│   │   │   └── POL.TP.PA.PA66  — Poliamid 66
│   │   ├── POL.TP.POM — Polioksymetylen (acetal)
│   │   ├── POL.TP.PO  — Poliolefiny
│   │   │   ├── POL.TP.PO.PE    — Polietylen (LDPE/HDPE/UHMWPE)
│   │   │   └── POL.TP.PO.PP    — Polipropylen
│   │   └── POL.TP.PET — Politereftalan etylenu
│   └── POL.TS — Termoutwardzalne
│       ├── POL.TS.EP  — Żywice epoksydowe
│       └── POL.TS.PU  — Poliuretan
│
├── WOD — Drewno i pochodne
│   ├── WOD.HB — Płyty drewnopochodne
│   │   ├── WOD.HB.MDF — MDF (medium density fibreboard)
│   │   ├── WOD.HB.HDF — HDF (high density fibreboard)
│   │   └── WOD.HB.PLY — Sklejka (plywood)
│   └── WOD.SW — Drewno lite (solid wood)
│       ├── WOD.SW.PIN — Sosna
│       ├── WOD.SW.OAK — Dąb
│       └── WOD.SW.BEE — Buk
│
├── PKG — Opakowania
│   ├── PKG.CB — Tektury i kartony
│   │   ├── PKG.CB.CF — Tektura falista (corrugated)
│   │   │   ├── PKG.CB.CF.E  — Fala E
│   │   │   ├── PKG.CB.CF.B  — Fala B
│   │   │   ├── PKG.CB.CF.C  — Fala C
│   │   │   └── PKG.CB.CF.BC — Fala BC (dwuścienna)
│   │   ├── PKG.CB.SB — Tektura lita (solid board)
│   │   └── PKG.CB.KR — Karton (kraft/liner)
│   └── PKG.PA — Papier
│       ├── PKG.PA.KR — Kraft
│       ├── PKG.PA.RE — Powlekany
│       └── PKG.PA.TP — Papier do tłoczenia (tissue)
│
├── CMP — Kompozyty
│   ├── CMP.GF — Włókno szklane (GFRP)
│   │   ├── CMP.GF.WV — Tkaniny szklane (woven)
│   │   ├── CMP.GF.UD — Jednokierunkowe (UD)
│   │   └── CMP.GF.CSM — Mat chopped strand mat
│   └── CMP.CF — Włókno węglowe (CFRP)
│       ├── CMP.CF.WV — Tkaniny węglowe
│       ├── CMP.CF.UD — Jednokierunkowe
│       └── CMP.CF.PRE — Prepreg
│
└── SPC — Materiały specjalne
    ├── SPC.FM — Pianki
    │   ├── SPC.FM.PU — Pianka poliuretanowa
    │   ├── SPC.FM.PE — Pianka polietylenowa
    │   └── SPC.FM.EPS — Styropian (EPS)
    ├── SPC.RB — Gumy i elastomery
    │   ├── SPC.RB.NR — Kauczuk naturalny
    │   ├── SPC.RB.NBR — Nitrilowa
    │   ├── SPC.RB.EPDM — EPDM
    │   └── SPC.RB.SI  — Silikonowa
    └── SPC.IN — Izolacje
        ├── SPC.IN.TH — Termiczne
        └── SPC.IN.AC — Akustyczne
```

---

## 7. Material Attributes — Atrybuty per klasa

### 7.1 Metale — Stale węglowe (MET.CS)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Skład chemiczny C | % | Zawartość węgla |
| Skład chemiczny Mn | % | Zawartość manganu |
| Skład chemiczny Si | % | Zawartość krzemu |
| Skład chemiczny P | % | Zawartość fosforu (maks.) |
| Skład chemiczny S | % | Zawartość siarki (maks.) |
| Wytrzymałość Rm | MPa | Min. wytrzymałość na rozciąganie |
| Granica plastyczności Re | MPa | Min. granica plastyczności |
| Wydłużenie A5 | % | Min. wydłużenie |
| Udarność KV | J | Energia uderzenia |
| Temperatura udaru | °C | Temperatura badania udarności |
| Twardość | HB | Twardość Brinella |
| Stan dostawy | — | HR, CR, normalized, quenched |
| Grubość | mm | Zakres grubości |
| Szerokość | mm | Standardowa szerokość blachy/taśmy |
| Powłoka | — | Bez, cynk, organiczna |
| Grubość cynku | g/m² | Dla gatunków ocynkowanych |
| Norma EN | — | np. EN 10025-2 |
| Spawalność | — | Dobra/Ograniczona/Zła |
| Spawanie CEV | — | Równoważnik węglowy |

### 7.2 Metale — Stale nierdzewne (MET.SS)

Wszystkie atrybuty jak MET.CS, plus:

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Zawartość Cr | % | Chrom |
| Zawartość Ni | % | Nikiel |
| Zawartość Mo | % | Molibden |
| PREN | — | Odporność na korozję wżerową |
| Odporność kwasowa | — | pH min |
| Wykończenie powierzchni | — | 2B, BA, 4, 6, 8 |
| Numer EN materiałowy | — | np. 1.4301 |
| Odpowiednik AISI | — | np. 304 |

### 7.3 Aluminium (MET.AL)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Stop | — | Skład stopowy (Al-Mg, Al-Si-Mg) |
| Stan (temper) | — | O, H14, H32, T4, T6, T651 |
| Wytrzymałość Rm | MPa | |
| Granica plastyczności Rp0.2 | MPa | |
| Wydłużenie | % | |
| Przewodność cieplna | W/m·K | |
| Przewodność elektryczna | %IACS | |
| Anodowanie | — | Możliwe/Nie |
| Spawalność | — | |

### 7.4 Miedź i stopy (MET.CU)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Czystość Cu | % | Zawartość miedzi |
| Zawartość Zn | % | Cynk (mosiądz) |
| Zawartość Sn | % | Cyna (brąz) |
| Przewodność elektryczna | %IACS | |
| Twardość | HV | |
| Obrabialność | % | Względna (mosiądz 100%) |

### 7.5 Tworzywa termoplastyczne (POL.TP)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| MFI (Melt Flow Index) | g/10min | Płynność stopu |
| Temperatura przetwórstwa | °C | Zakres |
| Temperatura zeszklenia Tg | °C | |
| Temperatura Vicat | °C | |
| Wytrzymałość na rozciąganie | MPa | |
| Wydłużenie przy zerwaniu | % | |
| Moduł Younga | MPa | |
| Moduł zginania | MPa | |
| Udarność Charpy (z karbem) | kJ/m² | |
| Gęstość | g/cm³ | |
| Absorpcja wody 24h | % | |
| Skurcz przetwórczy | % | |
| Klasa palności | — | UL94 V0/V1/V2/HB |
| Temperatura max pracy | °C | |
| RoHS | — | Zgodny/Niezgodny |
| Zawartość napełniacza | % | GF, CF, mineralny |
| Kolor | — | Natural/RAL |
| Forma dostawy | — | Granulat, płyta, pręt |

### 7.6 ABS — specyficzne

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Odporność UV | — | Dobra/Ograniczona |
| Galwanizacja | — | Tak/Nie |
| Klasa impact | — | Standard/High/Extra High |

### 7.7 PA6 / PA66 — specyficzne

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Kondycja normowania | — | Suchy/Kondycjonowany |
| Absorpcja wody (równowaga) | % | |
| Temperatura pracy ciągłej | °C | |
| Zawartość GF | % | 0/15/25/30/35/50 |

### 7.8 Drewno i płyty (WOD)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Gęstość nominalna | kg/m³ | |
| Grubość nominalna | mm | Seria: 6,9,12,16,18,22,25 |
| Tolerancja grubości | mm | |
| Wilgotność | % | |
| Klasa emisji HCHO | — | E0, E1, E2 |
| Klasa ochrony wilgoci | — | V20, V100 (MDF) |
| Moduł sprężystości E1 | N/mm² | |
| Wytrzymałość na zginanie | N/mm² | |
| Wytrzymałość na wyrywanie wkrętów | N | Z boku / z czoła |
| Norma EN | — | EN 622-5 (MDF), EN 314 (sklejka) |
| Certyfikat FSC/PEFC | — | |
| Klasa użytkowania | — | 1,2,3 |

### 7.9 Opakowania tekturowe (PKG.CB)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Gramatura | g/m² | |
| Rodzaj fali | — | E, B, C, BC |
| ECT (Edge Crush Test) | kN/m | |
| BCT (Box Compression Test) | N | |
| Gramatura linera zewnętrzny | g/m² | |
| Gramatura linera wewnętrzny | g/m² | |
| Gramatura flutingu | g/m² | |
| Wilgotność | % | |
| Klasa wilgocioodporności | — | |
| Certyfikat recyklingu | — | |
| Zawartość makulatury | % | |

### 7.10 Kompozyty (CMP)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Rodzaj włókna | — | E-glass, S-glass, T300, T700 |
| Tex (masa liniowa) | g/1000m | |
| Gramatura tkaniny | g/m² | |
| Splot | — | Plain, Twill 2/2, UD |
| Apretowanie | — | Typ zgodny z żywicą |
| Wytrzymałość włókna Rm | MPa | |
| Moduł Younga włókna | GPa | |
| Zawartość włókna (Vf) | % | |

### 7.11 Pianki (SPC.FM)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Gęstość pozorna | kg/m³ | |
| Wytrzymałość na ściskanie 10% | kPa | |
| Współczynnik przewodzenia ciepła λ | W/m·K | |
| Współczynnik sprężystości | % | |
| Temperatura pracy max | °C | |
| Absorpcja wody | % | |
| Klasa palności | — | B1, B2, B3 / UL94 |
| Grubość standardowa | mm | |
| Komórki | — | Otwarte/Zamknięte |

### 7.12 Gumy i elastomery (SPC.RB)

| Atrybut | Jednostka | Opis |
|---------|-----------|------|
| Twardość Shore A | Sh | |
| Wydłużenie | % | |
| Wytrzymałość na rozciąganie | MPa | |
| Temperatura pracy od/do | °C | |
| Odporność na oleje | — | |
| Odporność na ozon | — | |
| Odporność na UV | — | |
| Ścisłość | — | |
| Norma | — | ASTM D2000 klasa |

---

## 8. Density Library — Architektura

### Struktura bazy gęstości

```
density_library
├── reference_densities        -- wartości referencyjne (normy, literatura)
├── measured_densities         -- wartości zmierzone (laboratorium)
├── grade_density_overrides    -- korekty per gatunek
└── density_temperature_curves -- zmiana gęstości z temperaturą
```

### Tabela: density_reference_values

```sql
CREATE TABLE density_reference_values (
    density_ref_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id       UUID NOT NULL REFERENCES materials(material_id),
    density_kg_m3     NUMERIC(10,4) NOT NULL,
    density_min       NUMERIC(10,4),
    density_max       NUMERIC(10,4),
    temperature_c     NUMERIC(6,1) DEFAULT 20,
    source_type       VARCHAR(50),  -- STANDARD, LITERATURE, MEASURED, MANUFACTURER
    source_reference  VARCHAR(200), -- np. "EN 10027, AISI SAE Handbook"
    confidence        SMALLINT DEFAULT 4 CHECK (confidence BETWEEN 1 AND 5),
    valid_from        DATE NOT NULL DEFAULT CURRENT_DATE,
    notes             TEXT
);
```

### Referencyjne wartości gęstości — tabela danych

| Materiał | Gęstość [kg/m³] | Źródło |
|----------|-----------------|--------|
| S235, S355 (stale węglowe HR/CR) | 7850 | EN 10027 |
| DX51 (stal ocynkowana) | 7850 + powłoka Zn | EN 10346 |
| 304 / 316 (stal nierdzewna) | 7900 / 7980 | EN 10088 |
| Aluminium 1050 | 2710 | EN 573 |
| Aluminium 5052 | 2680 | EN 573 |
| Aluminium 6061/6082 | 2700 | EN 573 |
| Miedź Cu-ETP | 8940 | EN 1977 |
| Mosiądz CuZn37 | 8440 | EN 12164 |
| ABS | 1020–1060 | ISO 1183 |
| PC | 1190–1210 | ISO 1183 |
| PA6 (suchy) | 1130–1150 | ISO 1183 |
| PA66 (suchy) | 1130–1160 | ISO 1183 |
| POM-C | 1410–1420 | ISO 1183 |
| HDPE | 940–965 | ISO 1183 |
| PP homopolimer | 900–910 | ISO 1183 |
| PET | 1370–1400 | ISO 1183 |
| MDF (gęstość nominalna) | 700–800 | EN 622-5 |
| HDF | 800–1050 | EN 622-5 |
| Sklejka (brzozowa) | 680–720 | EN 314 |
| Tektura falista (C-flute) | 90–130 | TAPPI T824 |
| E-glass (włókno szklane) | 2540–2600 | ASTM D578 |
| Carbon fiber T300 | 1750–1800 | ASTM D3800 |
| Pianka PU (30 kg/m³) | 28–32 | ISO 845 |
| EPDM | 1100–1200 | ASTM D297 |

### Logika wyboru gęstości

```python
def get_density(material_id, temperature_c=20):
    """
    Priority:
    1. Measured density for specific batch (if available)
    2. Grade-specific override in density_reference_values
    3. Category default density
    4. Fallback: raise MaterialDataException
    """
    density = (
        get_measured_density(material_id)
        or get_grade_density(material_id, temperature_c)
        or get_category_default_density(material_id)
    )
    if density is None:
        raise MaterialDataException(f"No density for {material_id}")
    return density
```
