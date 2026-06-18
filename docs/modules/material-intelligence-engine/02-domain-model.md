# Material Intelligence Engine — Domain Model

## 3. Domain Model

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        MATERIAL INTELLIGENCE ENGINE                          │
│                                                                               │
│  ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────────┐   │
│  │  Material        │    │  Material        │    │  Material            │   │
│  │  Taxonomy        │───▶│  Master          │───▶│  Properties          │   │
│  │  (Hierarchy)     │    │  (Core Entity)   │    │  (Physical/Mech)     │   │
│  └──────────────────┘    └────────┬─────────┘    └──────────────────────┘   │
│                                   │                                           │
│           ┌───────────────────────┼───────────────────────┐                  │
│           │                       │                       │                  │
│           ▼                       ▼                       ▼                  │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐      │
│  │  Material       │   │  Material        │   │  Material            │      │
│  │  Standards      │   │  Cost Model      │   │  Substitutions       │      │
│  │  (ISO/DIN/EN)   │   │  (Coefficients)  │   │  (Alternatives)      │      │
│  └─────────────────┘   └──────────────────┘   └──────────────────────┘      │
│                                   │                                           │
│           ┌───────────────────────┼───────────────────────┐                  │
│           │                       │                       │                  │
│           ▼                       ▼                       ▼                  │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐      │
│  │  Market Price   │   │  Process         │   │  Supplier            │      │
│  │  Layer          │   │  Compatibility   │   │  Material Mapping    │      │
│  │  (Indices)      │   │  (Matrix)        │   │  (Availability)      │      │
│  └─────────────────┘   └──────────────────┘   └──────────────────────┘      │
│                                   │                                           │
│                                   ▼                                           │
│                    ┌──────────────────────────┐                               │
│                    │  Material Embeddings     │                               │
│                    │  (Vector Store / AI)     │                               │
│                    └──────────────────────────┘                               │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Agregaty domenowe

| Agregat | Korzeń | Encje wewnętrzne |
|---------|--------|-----------------|
| MaterialAggregate | Material | Properties, Standards, CostCoefficients |
| TaxonomyAggregate | MaterialCategory | CategoryAttribute |
| PriceAggregate | MarketPriceRecord | PriceSource, PriceTrend |
| SubstitutionAggregate | SubstitutionRule | SubstitutionScore |
| CompatibilityAggregate | ProcessCompatibility | CompatibilityConstraint |
| SupplierMaterialAggregate | SupplierMaterial | SupplierPriceRecord, LeadTimeRecord |

### Zdarzenia domenowe

| Zdarzenie | Wyzwalacz | Konsumenci |
|-----------|-----------|-----------|
| MaterialCreated | Nowy materiał | ERP Sync, Search Index |
| MaterialUpdated | Zmiana atrybutów | ERP Sync, Search Index, AI Embeddings |
| MaterialDeactivated | Wycofanie materiału | RFQ Engine, Cost Calc |
| PriceUpdated | Zmiana ceny rynkowej | Cost Calc, Forecasting |
| SubstitutionAdded | Nowy zamiennik | RFQ Engine, Procurement |
| CompatibilityChanged | Zmiana reguły | Cost Calc, Process Engine |
| SupplierMappingChanged | Zmiana powiązania | Supplier Intelligence |

---

## 4. Encje

### 4.1 Material (Materiał — rdzeń systemu)

**Opis:** Centralna encja reprezentująca jeden gatunek/typ materiału. Agreguje wszystkie właściwości techniczne, normy, statusy i powiązania.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| material_id | UUID | Klucz główny |
| material_code | VARCHAR(50) | Unikalny kod wewnętrzny (np. MET-S235-HR-SHEET) |
| material_name | VARCHAR(200) | Pełna nazwa |
| material_name_short | VARCHAR(50) | Skrócona nazwa do raportów |
| category_id | UUID FK | Kategoria w taksonomii |
| subcategory_id | UUID FK | Podkategoria w taksonomii |
| material_class | ENUM | METAL, POLYMER, WOOD, PACKAGING, COMPOSITE, SPECIAL |
| material_subclass | VARCHAR(100) | Podklasa (np. CARBON_STEEL, STAINLESS_STEEL, THERMOPLASTIC) |
| grade | VARCHAR(100) | Gatunek (np. S235, 304, ABS) |
| form_factor | ENUM | SHEET, COIL, BAR, TUBE, PROFILE, GRANULE, LIQUID, ROLL, BLOCK |
| status | ENUM | ACTIVE, PHASED_OUT, REPLACED, DRAFT, DISCONTINUED |
| replaced_by_id | UUID FK | Następnik (jeśli REPLACED) |
| description | TEXT | Opis techniczny |
| internal_notes | TEXT | Notatki wewnętrzne |
| created_at | TIMESTAMPTZ | Data utworzenia |
| updated_at | TIMESTAMPTZ | Data modyfikacji |
| created_by | UUID FK | Użytkownik tworzący |
| version | INTEGER | Wersja rekordu (optimistic locking) |
| is_hazardous | BOOLEAN | Materiał niebezpieczny (REACH, RoHS) |
| regulatory_flags | JSONB | Flagi regulacyjne (REACH, RoHS, SVHC) |
| erp_material_number | VARCHAR(50) | Numer materiału w ERP (SAP/Oracle) |
| customs_tariff_code | VARCHAR(20) | Kod CN/HS do ceł |

**Relacje:**
- `material_id → material_properties (1:1)`
- `material_id → material_mechanical_properties (1:1)`
- `material_id → material_standards (1:N)`
- `material_id → material_cost_coefficients (1:1)`
- `material_id → market_price_records (1:N)`
- `material_id → material_substitutions (source: 1:N)`
- `material_id → process_compatibility (1:N)`
- `material_id → supplier_material_mapping (1:N)`
- `material_id → material_embeddings (1:1)`

---

### 4.2 MaterialCategory (Kategoria materiału)

**Opis:** Węzeł w hierarchii taksonomicznej materiałów. Struktura drzewiasta (adjacency list z materialized path dla wydajności).

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| category_id | UUID | Klucz główny |
| parent_id | UUID FK | Rodzic (NULL = korzeń) |
| category_code | VARCHAR(20) | Kod kategorii (np. MET, MET.CS, MET.CS.HR) |
| category_name | VARCHAR(100) | Nazwa |
| category_name_en | VARCHAR(100) | Nazwa po angielsku |
| hierarchy_level | SMALLINT | Poziom (1=klasa, 2=podklasa, 3=gatunek, 4=odmiana) |
| materialized_path | VARCHAR(500) | Ścieżka (np. /MET/CS/HR/) |
| sort_order | SMALLINT | Kolejność sortowania |
| is_leaf | BOOLEAN | Czy węzeł liścia (bezpośrednio przypisywany materiałom) |
| icon_code | VARCHAR(50) | Ikona UI |
| color_hex | CHAR(7) | Kolor kategorii w UI |

**Relacje:**
- `category_id → category_id (self-referential tree)`
- `category_id → materials (1:N)`

---

### 4.3 MaterialProperties (Właściwości fizyczne)

**Opis:** Właściwości fizyczne i termiczne materiału — wspólne dla wszystkich klas.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| property_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| density_kg_m3 | NUMERIC(10,4) | Gęstość [kg/m³] |
| density_tolerance_pct | NUMERIC(5,2) | Tolerancja gęstości [%] |
| density_source | VARCHAR(100) | Źródło danych (norma, pomiar, literatura) |
| thermal_conductivity_w_mk | NUMERIC(10,4) | Przewodność cieplna [W/m·K] |
| thermal_expansion_1e6_k | NUMERIC(10,4) | Wsp. rozszerzalności cieplnej [10⁻⁶/K] |
| melting_point_c | NUMERIC(8,2) | Temperatura topnienia [°C] |
| max_service_temp_c | NUMERIC(8,2) | Maks. temperatura pracy [°C] |
| min_service_temp_c | NUMERIC(8,2) | Min. temperatura pracy [°C] |
| electrical_resistivity_ohm_m | NUMERIC(15,10) | Rezystywność elektryczna [Ω·m] |
| specific_heat_j_kgk | NUMERIC(10,2) | Ciepło właściwe [J/kg·K] |
| moisture_absorption_pct | NUMERIC(6,3) | Absorpcja wilgoci [%] (tworzywa, drewno) |
| flammability_class | VARCHAR(20) | Klasa palności (UL94, B1/B2/B3) |
| unit_of_measure | ENUM | KG, M, M2, M3, PIECE, LITER |
| standard_thickness_mm | NUMERIC(8,3) | Standardowa grubość [mm] (arkusze) |
| standard_width_mm | NUMERIC(8,2) | Standardowa szerokość [mm] |
| standard_length_mm | NUMERIC(8,2) | Standardowa długość [mm] |
| surface_finish | VARCHAR(50) | Stan powierzchni (RAW, COATED, PAINTED) |
| valid_from | DATE | Data obowiązywania |
| valid_to | DATE | Data wygaśnięcia (NULL = bezterminowo) |

---

### 4.4 MaterialMechanicalProperties (Właściwości mechaniczne)

**Opis:** Parametry mechaniczne — głównie dla metali i kompozytów.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| mech_prop_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| tensile_strength_mpa | NUMERIC(8,2) | Wytrzymałość na rozciąganie Rm [MPa] |
| yield_strength_mpa | NUMERIC(8,2) | Granica plastyczności Re [MPa] |
| elongation_pct | NUMERIC(6,2) | Wydłużenie A5 [%] |
| hardness_hb | NUMERIC(6,1) | Twardość [HB] |
| hardness_hrc | NUMERIC(6,2) | Twardość [HRC] |
| hardness_hv | NUMERIC(6,1) | Twardość [HV] |
| impact_energy_j | NUMERIC(8,2) | Energia uderzenia KV [J] |
| impact_temp_c | NUMERIC(6,1) | Temperatura próby udarności [°C] |
| youngs_modulus_gpa | NUMERIC(8,2) | Moduł Younga E [GPa] |
| shear_modulus_gpa | NUMERIC(8,2) | Moduł Kirchhoffa G [GPa] |
| poissons_ratio | NUMERIC(6,4) | Współczynnik Poissona ν |
| fatigue_limit_mpa | NUMERIC(8,2) | Granica zmęczenia [MPa] |
| fracture_toughness_mpa_m | NUMERIC(8,3) | Odporność na pękanie KIc [MPa·√m] |
| condition | VARCHAR(50) | Stan (annealed, normalized, quenched, T6, etc.) |
| test_standard | VARCHAR(50) | Norma badania (ISO 6892, ASTM E8) |

---

### 4.5 MaterialStandard (Norma materiałowa)

**Opis:** Powiązanie materiału z normami technicznymi i ich odpowiednikami między systemami norm.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| standard_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| standard_system | ENUM | ISO, DIN, EN, ASTM, BS, JIS, GB, PN, GOST |
| standard_number | VARCHAR(50) | Numer normy (np. EN 10025, ASTM A36) |
| standard_grade | VARCHAR(50) | Oznaczenie gatunku wg normy |
| standard_year | SMALLINT | Rok wydania normy |
| is_primary | BOOLEAN | Czy norma pierwotna dla materiału |
| equivalence_type | ENUM | IDENTICAL, EQUIVALENT, SIMILAR, REPLACED_BY |
| equivalence_notes | TEXT | Różnice/uwagi do równoważności |
| certificate_required | BOOLEAN | Czy wymagany certyfikat 3.1/3.2 |

---

### 4.6 MaterialCostCoefficients (Współczynniki kosztowe)

**Opis:** Parametry kosztowe specyficzne dla materiału, niezależne od aktualnej ceny rynkowej.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| coeff_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| scrap_rate_pct | NUMERIC(6,3) | Wskaźnik odpadów technologicznych [%] |
| yield_rate_pct | NUMERIC(6,3) | Wskaźnik uzysku materiału [%] |
| machining_allowance_mm | NUMERIC(6,2) | Naddatek na obróbkę [mm] |
| forming_allowance_pct | NUMERIC(6,3) | Naddatek formowania [%] |
| handling_cost_pct | NUMERIC(6,3) | Koszt obsługi (% wartości materiału) |
| storage_cost_pct_month | NUMERIC(6,4) | Koszt magazynowania [%/miesiąc] |
| certification_cost_eur_kg | NUMERIC(10,4) | Koszt certyfikacji [EUR/kg] |
| minimum_order_surcharge_pct | NUMERIC(6,3) | Dopłata za małe zamówienie [%] |
| cutting_waste_pct | NUMERIC(6,3) | Odpad z cięcia [%] |
| nesting_efficiency_pct | NUMERIC(6,3) | Efektywność nestingu [%] (blachy) |
| density_tolerance_impact | NUMERIC(6,4) | Wpływ tolerancji gęstości na masę |
| currency | CHAR(3) | Waluta bazowa (EUR, PLN, USD) |
| valid_from | DATE | Obowiązywanie od |
| valid_to | DATE | Obowiązywanie do |

---

### 4.7 MarketPriceRecord (Rekord ceny rynkowej)

**Opis:** Historyczny i bieżący rekord ceny rynkowej/zakupowej materiału.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| price_record_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| price_source_id | UUID FK | Źródło ceny |
| price_type | ENUM | MARKET_INDEX, PURCHASE, QUOTED, FORECAST |
| price_date | DATE | Data ceny |
| price_value | NUMERIC(14,4) | Wartość ceny |
| currency | CHAR(3) | Waluta |
| unit | ENUM | PER_KG, PER_TON, PER_M2, PER_M3, PER_PIECE, PER_M |
| price_region | VARCHAR(50) | Region cenowy (EU, DE, PL, GLOBAL) |
| confidence_level | SMALLINT | Poziom zaufania (1-5) |
| notes | TEXT | Notatki |
| is_active | BOOLEAN | Czy rekord aktywny (najnowszy) |
| created_at | TIMESTAMPTZ | Data wpisu |

---

### 4.8 PriceSource (Źródło cen)

**Opis:** Definicja źródła danych cenowych (giełda, indeks, dostawca, wewnętrzne).

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| source_id | UUID | Klucz główny |
| source_code | VARCHAR(50) | Kod (LME, PLATTS, SUPPLIER_X) |
| source_name | VARCHAR(200) | Pełna nazwa |
| source_type | ENUM | EXCHANGE, INDEX, SUPPLIER, INTERNAL, MANUAL |
| url | VARCHAR(500) | URL (jeśli automatyczne pobieranie) |
| update_frequency | ENUM | REALTIME, DAILY, WEEKLY, MONTHLY, MANUAL |
| api_connector_class | VARCHAR(200) | Klasa konektora (FQCN) |
| is_active | BOOLEAN | Aktywne źródło |
| reliability_score | SMALLINT | Wiarygodność (1-10) |
| currency | CHAR(3) | Domyślna waluta źródła |
| last_sync_at | TIMESTAMPTZ | Ostatnia synchronizacja |

---

### 4.9 MaterialSubstitution (Zamiennik materiałowy)

**Opis:** Reguła substytucji — definiuje, czym można zastąpić dany materiał i przy jakich warunkach.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| substitution_id | UUID | Klucz główny |
| source_material_id | UUID FK | Materiał oryginalny |
| substitute_material_id | UUID FK | Materiał zamienny |
| substitution_type | ENUM | DIRECT, CONDITIONAL, PARTIAL, PROCESS_CHANGE_NEEDED |
| compatibility_score | NUMERIC(5,2) | Zgodność techniczna (0.0–100.0) |
| cost_impact_pct | NUMERIC(8,3) | Zmiana kosztu [%] (ujemna = tańszy) |
| weight_impact_pct | NUMERIC(8,3) | Zmiana masy [%] |
| conditions | JSONB | Warunki zastosowania zamiennika |
| process_changes | TEXT | Wymagane zmiany procesowe |
| engineering_approval_required | BOOLEAN | Wymagana zgoda inżyniera |
| valid_applications | TEXT[] | Dopuszczalne zastosowania |
| excluded_applications | TEXT[] | Wykluczone zastosowania |
| created_by | UUID FK | Kto zatwierdził |
| approved_at | TIMESTAMPTZ | Data zatwierdzenia |
| notes | TEXT | Notatki techniczne |

---

### 4.10 ProcessCompatibility (Kompatybilność materiał–proces)

**Opis:** Macierz łącząca materiały z procesami produkcyjnymi, definiująca parametry i ograniczenia.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| compat_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| process_code | VARCHAR(50) | Kod procesu (LASER_CUT, WELD_MIG, BEND_90, INJECTION_MOLD) |
| process_category | ENUM | CUTTING, FORMING, WELDING, MACHINING, COATING, MOLDING, ASSEMBLY |
| compatibility_level | ENUM | OPTIMAL, ACCEPTABLE, POSSIBLE_WITH_CAUTION, NOT_RECOMMENDED, FORBIDDEN |
| min_thickness_mm | NUMERIC(6,2) | Min. grubość [mm] |
| max_thickness_mm | NUMERIC(6,2) | Maks. grubość [mm] |
| speed_factor | NUMERIC(6,3) | Mnożnik prędkości (1.0 = standard) |
| quality_notes | TEXT | Uwagi jakościowe |
| tooling_requirements | TEXT | Wymagania narzędziowe |
| parameter_overrides | JSONB | Nadpisanie parametrów technologicznych |
| waste_factor_override | NUMERIC(6,3) | Nadpisanie wskaźnika odpadów dla tej kombinacji |

---

### 4.11 SupplierMaterialMapping (Mapowanie dostawca–materiał)

**Opis:** Powiązanie materiału z konkretnym dostawcą — zawiera dane handlowe i logistyczne.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| mapping_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| supplier_id | UUID FK | Dostawca (referencja do Supplier Intelligence) |
| supplier_material_code | VARCHAR(100) | Kod materiału u dostawcy |
| supplier_material_name | VARCHAR(200) | Nazwa u dostawcy |
| is_preferred | BOOLEAN | Preferowany dostawca |
| is_approved | BOOLEAN | Zatwierdzony dostawca |
| approval_date | DATE | Data zatwierdzenia |
| min_order_quantity | NUMERIC(14,4) | MOQ |
| min_order_unit | ENUM | KG, TON, M2, PIECE, M |
| lead_time_days | SMALLINT | Lead time [dni robocze] |
| lead_time_days_express | SMALLINT | Lead time ekspresowy [dni] |
| price_validity_days | SMALLINT | Ważność ceny [dni] |
| incoterms | VARCHAR(10) | Incoterms (DDP, FCA, EXW) |
| origin_country | CHAR(2) | Kraj pochodzenia (ISO 3166) |
| supply_risk_level | ENUM | LOW, MEDIUM, HIGH, CRITICAL |
| is_single_source | BOOLEAN | Jedyne źródło dostaw |
| active | BOOLEAN | Aktywne powiązanie |

---

### 4.12 MaterialEmbedding (Embedding wektorowy)

**Opis:** Wektorowa reprezentacja materiału dla wyszukiwania semantycznego i AI.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| embedding_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| embedding_model | VARCHAR(100) | Model embeddingów (np. text-embedding-3-small) |
| embedding_version | VARCHAR(20) | Wersja modelu |
| vector | VECTOR(1536) | Wektor (pgvector) |
| text_input | TEXT | Tekst wejściowy do embeddingu |
| created_at | TIMESTAMPTZ | Data generowania |
| is_current | BOOLEAN | Czy aktualny embedding |

---

### 4.13 MaterialAuditLog (Log zmian)

**Opis:** Pełna historia zmian w danych materiałowych.

**Atrybuty:**

| Atrybut | Typ | Opis |
|---------|-----|------|
| audit_id | UUID | Klucz główny |
| material_id | UUID FK | Materiał |
| entity_type | VARCHAR(50) | Typ encji (material, properties, price) |
| entity_id | UUID | ID zmienianej encji |
| operation | ENUM | CREATE, UPDATE, DELETE, RESTORE |
| changed_fields | JSONB | Zmienione pola (old/new) |
| changed_by | UUID FK | Użytkownik |
| changed_at | TIMESTAMPTZ | Czas zmiany |
| reason | TEXT | Powód zmiany |
| ip_address | INET | IP zmienialacza |
| session_id | VARCHAR(100) | ID sesji |
