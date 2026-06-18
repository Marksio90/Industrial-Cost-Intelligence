# Supplier Intelligence Engine — Master Data i Metryki

## 2. Supplier Master Data

### Encja: Supplier (rdzeń)

| Atrybut | Typ | Opis |
|---------|-----|------|
| supplier_id | UUID PK | |
| supplier_code | VARCHAR(30) UNIQUE | Kod wewnętrzny (np. SUP-DE-001) |
| legal_name | VARCHAR(300) | Pełna nazwa prawna |
| trade_name | VARCHAR(200) | Nazwa handlowa / marka |
| supplier_type | ENUM | MANUFACTURER, WHOLESALER, DISTRIBUTOR, SUBCONTRACTOR, TRADER, AGENT |
| vat_number | VARCHAR(50) | NIP/VAT EU |
| registration_number | VARCHAR(100) | KRS/Handelsregister/siret |
| duns_number | VARCHAR(20) | D-U-N-S Number (Dun & Bradstreet) |
| country_iso | CHAR(2) | Kraj rejestracji (ISO 3166) |
| founded_year | SMALLINT | Rok założenia |
| employee_count | INTEGER | Liczba pracowników |
| annual_revenue_eur | NUMERIC(16,2) | Obrót roczny |
| revenue_year | SMALLINT | Rok danych finansowych |
| website | VARCHAR(300) | |
| primary_language | CHAR(5) | Język komunikacji (de, pl, en) |
| currency_default | CHAR(3) | Domyślna waluta ofert |
| incoterms_default | VARCHAR(10) | Domyślne Incoterms |
| payment_terms_days | SMALLINT | Standardowe terminy płatności |
| status | ENUM | ACTIVE, UNDER_EVALUATION, APPROVED, SUSPENDED, DEACTIVATED, BLACKLISTED |
| approval_date | DATE | Data zatwierdzenia |
| approval_by | UUID FK | |
| strategic_tier | ENUM | TIER1, TIER2, TIER3, SPOT | — poziom strategiczny |
| is_preferred | BOOLEAN | Preferowany dostawca |
| is_single_source | BOOLEAN | Jedyne źródło (risk flag) |
| erp_vendor_id | VARCHAR(50) | ID w ERP (SAP/Oracle) |
| created_at | TIMESTAMPTZ | |
| updated_at | TIMESTAMPTZ | |
| version | INTEGER | Optimistic locking |

### Encja: SupplierAddress

```
SupplierAddress
  address_id       UUID PK
  supplier_id      UUID FK
  address_type     ENUM(REGISTERED, OPERATIONAL, WAREHOUSE, BILLING, SHIPPING)
  street           VARCHAR(200)
  city             VARCHAR(100)
  postal_code      VARCHAR(20)
  state_province   VARCHAR(100)
  country_iso      CHAR(2)
  is_primary       BOOLEAN
  lat              NUMERIC(10,7)   -- geolocation
  lng              NUMERIC(10,7)
  nuts_code        VARCHAR(10)     -- EU NUTS region (for risk scoring)
```

### Encja: SupplierContact

```
SupplierContact
  contact_id       UUID PK
  supplier_id      UUID FK
  contact_type     ENUM(SALES, QUALITY, LOGISTICS, FINANCE, TECHNICAL, EXECUTIVE)
  first_name       VARCHAR(100)
  last_name        VARCHAR(100)
  title            VARCHAR(100)
  email            VARCHAR(200)
  phone            VARCHAR(50)
  mobile           VARCHAR(50)
  is_primary       BOOLEAN
  preferred_lang   CHAR(5)
  notes            TEXT
```

### Encja: SupplierCertification

```
SupplierCertification
  cert_id          UUID PK
  supplier_id      UUID FK
  cert_type        ENUM(ISO9001, ISO14001, ISO45001, IATF16949, AS9100,
                        ISO27001, ISO50001, EN1090, EN15085, NADCAP,
                        FSC, PEFC, REACH, ROHS, CONFLICT_FREE_MINERALS,
                        CUSTOM)
  cert_number      VARCHAR(100)
  issued_by        VARCHAR(200)     -- TÜV, Bureau Veritas, DNV GL, etc.
  valid_from       DATE
  valid_to         DATE
  scope            TEXT             -- certyfikowany zakres
  document_url     VARCHAR(500)
  is_current       BOOLEAN GENERATED
  verified_by      UUID FK
  verified_at      TIMESTAMPTZ
```

### Encja: SupplierCapability (Co dostawca może dostarczyć)

```
SupplierCapability
  capability_id    UUID PK
  supplier_id      UUID FK
  capability_type  ENUM(MATERIAL_SUPPLY, MANUFACTURING_SERVICE,
                        DISTRIBUTION, LOGISTICS, FINISHING)
  category_code    VARCHAR(30)      -- z taksonomii MIE lub MPE
  description      TEXT
  annual_capacity_eur  NUMERIC(16,2)  -- roczna zdolność produkcyjna w EUR
  capacity_unit    VARCHAR(30)
  lead_time_days   SMALLINT
  lead_time_express_days SMALLINT
  min_order_qty    NUMERIC(14,4)
  min_order_unit   VARCHAR(20)
  is_active        BOOLEAN
```

---

## 3. Supplier Rating — Architektura oceny

### Model oceny dostawcy (Scorecard)

```
SupplierScorecard
  ├── QualityScore         (waga: 35%)
  │   ├── PPM (Parts Per Million defect rate)
  │   ├── NCR Rate (Non-Conformance Reports / 100 deliveries)
  │   ├── First Pass Yield
  │   └── Certifications (ISO 9001, IATF, etc.)
  ├── DeliveryScore        (waga: 25%)
  │   ├── On-Time Delivery (OTD) %
  │   ├── On-Time In-Full (OTIF) %
  │   ├── Lead Time Accuracy %
  │   └── Advance Ship Notice Compliance %
  ├── PriceScore           (waga: 20%)
  │   ├── Price Competitiveness vs. market index
  │   ├── Price Stability (YoY variance)
  │   ├── Total Cost of Ownership
  │   └── Payment Terms
  ├── ServiceScore         (waga: 10%)
  │   ├── Response Time (quote turnaround)
  │   ├── Technical Support Quality
  │   └── Flexibility / Emergency Response
  └── RiskScore            (waga: 10%)
      ├── Financial Stability Index
      ├── Geopolitical Risk
      ├── Concentration Risk (single source)
      └── ESG / Compliance Flags
```

### Encja: SupplierScorecard

| Atrybut | Typ | Opis |
|---------|-----|------|
| scorecard_id | UUID PK | |
| supplier_id | UUID FK | |
| period_from | DATE | Okres oceny od |
| period_to | DATE | Okres oceny do |
| evaluation_type | ENUM | MONTHLY, QUARTERLY, ANNUAL, TRIGGERED |
| quality_score | NUMERIC(5,2) | Wynik jakości (0–100) |
| delivery_score | NUMERIC(5,2) | Wynik dostaw (0–100) |
| price_score | NUMERIC(5,2) | Wynik cenowy (0–100) |
| service_score | NUMERIC(5,2) | Wynik obsługi (0–100) |
| risk_score | NUMERIC(5,2) | Wynik ryzyka (0–100) |
| total_score | NUMERIC(5,2) | Wynik łączny ważony (0–100) |
| rating_class | ENUM | A_PREFERRED, B_APPROVED, C_CONDITIONAL, D_PROBATION, F_DISQUALIFIED |
| rating_trend | ENUM | IMPROVING, STABLE, DECLINING, CRITICAL |
| previous_total | NUMERIC(5,2) | Poprzedni wynik (trend) |
| score_delta | NUMERIC(5,2) | Zmiana wyniku |
| transaction_count | INTEGER | Liczba transakcji w okresie |
| total_spend_eur | NUMERIC(16,2) | Wartość zakupów w okresie |
| calculated_at | TIMESTAMPTZ | Czas obliczenia |
| calculated_by | ENUM | AUTOMATIC, MANUAL_OVERRIDE |
| override_reason | TEXT | |
| comments | TEXT | |

### Klasy ratingowe

| Klasa | Wynik | Znaczenie | Działania |
|-------|-------|-----------|-----------|
| A — PREFERRED | 85–100 | Strategiczny partner | Rozszerzanie współpracy, preferowane w RFQ |
| B — APPROVED | 70–84 | Zatwierdzony, dobry | Standard, normalne warunki |
| C — CONDITIONAL | 55–69 | Warunkowo zatwierdzone | Plan poprawy, zwiększony monitoring |
| D — PROBATION | 40–54 | Okres próbny | Ograniczenie zamówień, audyt wymagany |
| F — DISQUALIFIED | 0–39 | Dyskwalifikacja | Blokada zamówień, decyzja zarządu |

---

## 4. Quality Metrics

### Encja: QualityRecord (per dostawa / per okres)

| Atrybut | Typ | Opis |
|---------|-----|------|
| quality_record_id | UUID PK | |
| supplier_id | UUID FK | |
| delivery_id | UUID FK | Powiązana dostawa |
| period_from | DATE | |
| period_to | DATE | |
| total_parts_received | INTEGER | |
| defective_parts | INTEGER | |
| ppm | NUMERIC(10,2) | Defekty na milion (defective/received × 1M) |
| ncr_count | INTEGER | Liczba reklamacji (NCR) |
| ncr_critical | SMALLINT | NCR krytyczne (safety, function) |
| ncr_major | SMALLINT | NCR poważne |
| ncr_minor | SMALLINT | NCR drobne |
| first_pass_yield_pct | NUMERIC(6,3) | % części przyjętych bez reklamacji |
| return_rate_pct | NUMERIC(6,3) | % zwróconych |
| warranty_claims | INTEGER | Reklamacje gwarancyjne |
| line_stoppages | INTEGER | Zatrzymania linii z winy dostawcy |
| corrective_action_pending | BOOLEAN | Oczekuje na CAPA |
| inspection_level | ENUM | SKIP_LOT, REDUCED, NORMAL, TIGHTENED, 100PCT |
| source | ENUM | ERP_AUTO, QUALITY_SYSTEM, MANUAL |

### Encja: NCRRecord (Non-Conformance Report)

```
NCRRecord
  ncr_id              UUID PK
  supplier_id         UUID FK
  delivery_id         UUID FK
  ncr_number          VARCHAR(50)       -- numer reklamacji
  raised_date         DATE
  closed_date         DATE
  severity            ENUM(CRITICAL, MAJOR, MINOR)
  defect_category     ENUM(DIMENSIONAL, SURFACE, MATERIAL_CERT,
                           LABELING, QUANTITY, DOCUMENTATION,
                           CONTAMINATION, FUNCTIONAL, SAFETY)
  defect_description  TEXT
  quantity_affected   INTEGER
  quantity_scrapped   INTEGER
  quantity_reworked   INTEGER
  cost_impact_eur     NUMERIC(10,4)     -- koszt reklamacji
  root_cause          TEXT
  corrective_action   TEXT
  capa_due_date       DATE
  capa_closed_date    DATE
  supplier_response_days SMALLINT       -- czas odpowiedzi dostawcy
  8d_report_received  BOOLEAN
  is_recurring        BOOLEAN           -- podobna NCR w ostatnich 12 mies.
  previous_ncr_id     UUID FK
  status              ENUM(OPEN, SUPPLIER_RESPONSE_PENDING, CAPA_PENDING,
                           CAPA_VERIFICATION, CLOSED_EFFECTIVE, CLOSED_INEFFECTIVE)
```

### Kalkulacja PPM i Quality Score

```python
class QualityMetricsCalculator:

    def calculate_ppm(self, total_received: int, defective: int) -> float:
        """Parts Per Million defect rate."""
        if total_received == 0:
            return 0.0
        return (defective / total_received) * 1_000_000

    def calculate_quality_score(self,
                                 ppm: float,
                                 ncr_rate: float,          # NCRs per 100 deliveries
                                 first_pass_yield: float,  # 0-100%
                                 certifications: list,
                                 line_stoppages: int) -> QualityScore:
        """
        Quality Score = PPM component + NCR component + FPY component
                      + Certification component - Line stoppage penalty
        """
        # PPM scoring (lower = better)
        ppm_score = self._score_ppm(ppm)         # 0–40 points
        ncr_score = self._score_ncr(ncr_rate)    # 0–25 points
        fpy_score = first_pass_yield * 0.20      # 0–20 points
        cert_score = self._score_certs(certifications)  # 0–10 points
        stoppage_penalty = min(line_stoppages * 5, 25)  # up to -25

        total = max(0, ppm_score + ncr_score + fpy_score + cert_score - stoppage_penalty)
        return QualityScore(
            total=round(min(total, 100), 2),
            ppm_component=ppm_score,
            ncr_component=ncr_score,
            fpy_component=fpy_score,
            cert_component=cert_score,
            stoppage_penalty=stoppage_penalty,
        )

    def _score_ppm(self, ppm: float) -> float:
        """PPM thresholds for scoring (automotive-inspired)."""
        if ppm == 0:       return 40.0
        elif ppm <= 10:    return 38.0
        elif ppm <= 50:    return 34.0
        elif ppm <= 100:   return 28.0
        elif ppm <= 500:   return 20.0
        elif ppm <= 1000:  return 12.0
        elif ppm <= 5000:  return 5.0
        else:              return 0.0

    def _score_ncr(self, ncr_rate: float) -> float:
        """NCR rate per 100 deliveries."""
        if ncr_rate == 0:      return 25.0
        elif ncr_rate <= 0.5:  return 22.0
        elif ncr_rate <= 1.0:  return 18.0
        elif ncr_rate <= 2.0:  return 12.0
        elif ncr_rate <= 5.0:  return 6.0
        else:                  return 0.0

    def _score_certs(self, certifications: list) -> float:
        """Certification portfolio scoring (max 10)."""
        key_certs = {'ISO9001': 3, 'IATF16949': 5, 'AS9100': 5,
                     'ISO14001': 1, 'ISO45001': 1}
        score = sum(key_certs.get(c, 0.5) for c in certifications)
        return min(score, 10)
```

---

## 5. Lead Time Metrics

### Encja: LeadTimeRecord

| Atrybut | Typ | Opis |
|---------|-----|------|
| lt_record_id | UUID PK | |
| supplier_id | UUID FK | |
| material_id | UUID FK | |
| process_type_code | VARCHAR(30) | Jeśli usługa |
| order_date | DATE | Data zamówienia |
| promised_date | DATE | Obiecana data dostawy |
| actual_delivery_date | DATE | Rzeczywista data |
| promised_lead_time_days | SMALLINT | Obiecany LT w dniach rob. |
| actual_lead_time_days | SMALLINT | Rzeczywisty LT w dniach rob. |
| variance_days | SMALLINT | actual - promised |
| on_time | BOOLEAN | actual ≤ promised |
| quantity_ordered | NUMERIC(14,4) | |
| quantity_delivered | NUMERIC(14,4) | |
| otif_flag | BOOLEAN | On Time In Full |
| delay_reason | VARCHAR(200) | Powód opóźnienia |
| delay_category | ENUM | SUPPLIER_FAULT, LOGISTICS, FORCE_MAJEURE, CUSTOMS, CLIENT_CHANGE |
| incoterms | VARCHAR(10) | |

### Kalkulacja metryk Lead Time

```python
class LeadTimeMetricsCalculator:

    def calculate_metrics(self, records: list[LeadTimeRecord],
                          period_days: int = 90) -> LeadTimeMetrics:
        if not records:
            return LeadTimeMetrics.empty()

        # OTD — On-Time Delivery
        on_time_count = sum(1 for r in records if r.on_time)
        otd_pct = on_time_count / len(records) * 100

        # OTIF — On-Time In-Full
        otif_count = sum(1 for r in records if r.otif_flag)
        otif_pct = otif_count / len(records) * 100

        # Lead time statistics
        actual_lts = [r.actual_lead_time_days for r in records]
        avg_lt = statistics.mean(actual_lts)
        std_lt = statistics.stdev(actual_lts) if len(actual_lts) > 1 else 0
        p90_lt = sorted(actual_lts)[int(len(actual_lts) * 0.9)]

        # Variance analysis
        variances = [r.variance_days for r in records]
        avg_variance = statistics.mean(variances)

        # Lead time reliability: % of deliveries within promised ± 1 day
        reliable = sum(1 for v in variances if abs(v) <= 1)
        reliability_pct = reliable / len(variances) * 100

        return LeadTimeMetrics(
            otd_pct=round(otd_pct, 2),
            otif_pct=round(otif_pct, 2),
            avg_lead_time_days=round(avg_lt, 1),
            std_lead_time_days=round(std_lt, 1),
            p90_lead_time_days=p90_lt,
            avg_variance_days=round(avg_variance, 1),
            reliability_pct=round(reliability_pct, 2),
            sample_size=len(records),
        )

    def calculate_delivery_score(self, metrics: LeadTimeMetrics) -> float:
        """Delivery score (0–100) from OTD, OTIF, reliability."""
        otd_component  = self._score_otd(metrics.otd_pct)   # 0–40
        otif_component = self._score_otif(metrics.otif_pct) # 0–35
        rel_component  = metrics.reliability_pct * 0.25     # 0–25
        return round(min(100, otd_component + otif_component + rel_component), 2)

    def _score_otd(self, otd: float) -> float:
        if otd >= 98:  return 40.0
        elif otd >= 95: return 35.0
        elif otd >= 90: return 28.0
        elif otd >= 85: return 20.0
        elif otd >= 75: return 12.0
        else:           return 0.0

    def _score_otif(self, otif: float) -> float:
        if otif >= 97:  return 35.0
        elif otif >= 93: return 30.0
        elif otif >= 88: return 22.0
        elif otif >= 80: return 14.0
        else:            return 0.0
```

---

## 6. MOQ Metrics

### Encja: MOQRecord

```
MOQRecord
  moq_id              UUID PK
  supplier_id         UUID FK
  material_id         UUID FK
  capability_id       UUID FK
  moq_value           NUMERIC(14,4)       -- minimalna wielkość zamówienia
  moq_unit            ENUM(KG, TON, M, M2, M3, PIECE, ROLL, PALLET)
  moq_value_eur       NUMERIC(14,2)       -- wartość MOQ w EUR (jeśli brak jednostki)
  lot_increment       NUMERIC(14,4)       -- wielokrotność zamówienia (1 → każda ilość)
  lot_increment_unit  ENUM                -- identyczna z moq_unit
  small_order_surcharge_pct NUMERIC(6,3)  -- dopłata za zamówienie poniżej MOQ
  max_order_qty       NUMERIC(14,4)       -- maks. jednorazowe zamówienie
  stock_available_qty NUMERIC(14,4)       -- dostępne na magazynie (jeśli hurtownia)
  stock_updated_at    TIMESTAMPTZ
  valid_from          DATE
  valid_to            DATE
  notes               TEXT
```

### Analiza MOQ vs. Popyt

```python
class MOQAnalyzer:
    def analyze_moq_impact(self, moq: MOQRecord,
                            avg_monthly_demand: float) -> MOQAnalysis:
        """
        Determines if MOQ creates over-stocking or cost inefficiency.
        """
        months_of_stock = moq.moq_value / avg_monthly_demand if avg_monthly_demand > 0 else 999

        if months_of_stock <= 1:
            impact = 'NONE'
        elif months_of_stock <= 2:
            impact = 'LOW'
        elif months_of_stock <= 4:
            impact = 'MEDIUM'
        elif months_of_stock <= 6:
            impact = 'HIGH'
        else:
            impact = 'CRITICAL'

        storage_cost_eur = (moq.moq_value_eur * 0.25 / 12) * months_of_stock

        return MOQAnalysis(
            months_of_stock=round(months_of_stock, 1),
            inventory_impact=impact,
            estimated_storage_cost_eur=round(storage_cost_eur, 2),
            negotiation_recommended=(months_of_stock > 3),
        )
```

---

## 7. Historical Pricing

### Encja: PriceOffer (Oferta cenowa dostawcy)

| Atrybut | Typ | Opis |
|---------|-----|------|
| offer_id | UUID PK | |
| supplier_id | UUID FK | |
| material_id | UUID FK | |
| process_type_code | VARCHAR(30) | Jeśli usługa |
| quote_reference | VARCHAR(100) | Numer oferty |
| price_value | NUMERIC(14,4) | Cena jednostkowa |
| currency | CHAR(3) | |
| unit | ENUM | PER_KG, PER_TON, PER_M2, PER_M3, PER_PIECE, PER_H |
| incoterms | VARCHAR(10) | |
| quantity_from | NUMERIC(14,4) | Próg ilościowy od |
| quantity_to | NUMERIC(14,4) | Próg ilościowy do |
| valid_from | DATE | |
| valid_to | DATE | |
| price_basis | ENUM | FIXED, INDEX_LINKED, SPOT, CONTRACT |
| index_reference | VARCHAR(100) | Indeks (LME Cu, Platts HRC) |
| index_adjustment_pct | NUMERIC(8,4) | Korekta od indeksu |
| lead_time_days | SMALLINT | LT dla tej ceny |
| tooling_cost_eur | NUMERIC(12,2) | Jednorazowy koszt oprzyrządowania |
| tooling_amortization_qty | INTEGER | Amortyzacja narzędzi na qty |
| payment_terms_days | SMALLINT | |
| discount_pct | NUMERIC(6,3) | Rabat |
| surcharge_pct | NUMERIC(6,3) | Dopłata (mały wolumen) |
| is_active | BOOLEAN | |
| source | ENUM | RFQ_RESPONSE, CATALOG, NEGOTIATED, ERP_IMPORT |

### Analiza trendów cenowych dostawcy

```python
class SupplierPriceTrendAnalyzer:
    """
    Analyzes supplier price history vs. market benchmarks.
    """

    def analyze(self, supplier_id: str,
                material_id: str,
                periods: int = 12) -> PriceTrendAnalysis:
        offers = self.get_price_history(supplier_id, material_id, periods)
        market_index = self.get_market_index(material_id, periods)

        supplier_prices = [o.normalized_price_eur_kg for o in offers]
        market_prices = [m.price_eur_kg for m in market_index]

        # Price competitiveness: supplier vs. market average
        current_diff_pct = ((offers[-1].normalized_price_eur_kg - market_prices[-1])
                            / market_prices[-1] * 100)

        # Price stability: coefficient of variation
        cv = statistics.stdev(supplier_prices) / statistics.mean(supplier_prices) * 100

        # YoY change
        yoy_change = None
        if len(supplier_prices) >= 13:
            yoy_change = (supplier_prices[-1] - supplier_prices[-13]) / supplier_prices[-13] * 100

        return PriceTrendAnalysis(
            supplier_id=supplier_id,
            material_id=material_id,
            current_price=offers[-1].normalized_price_eur_kg,
            market_price=market_prices[-1],
            competitiveness_pct=round(current_diff_pct, 2),
            price_cv_pct=round(cv, 2),
            yoy_change_pct=round(yoy_change, 2) if yoy_change else None,
            trend='COMPETITIVE' if current_diff_pct < 0 else
                  'AT_MARKET' if current_diff_pct < 5 else 'ABOVE_MARKET',
            months_analyzed=len(offers),
        )

    def calculate_price_score(self, analysis: PriceTrendAnalysis) -> float:
        """Price score (0–100). Competitive pricing = high score."""
        # Competitiveness component (0–60)
        diff = analysis.competitiveness_pct
        if diff <= -10:     comp = 60.0    # >10% cheaper than market
        elif diff <= -5:    comp = 55.0
        elif diff <= 0:     comp = 50.0
        elif diff <= 5:     comp = 40.0
        elif diff <= 10:    comp = 28.0
        elif diff <= 20:    comp = 15.0
        else:               comp = 0.0

        # Stability component (0–25): lower CV = higher score
        cv = analysis.price_cv_pct
        stab = max(0, 25 - cv * 1.5)

        # Payment terms component (0–15)
        terms_score = min(15, analysis.payment_terms_days / 60 * 15)

        return round(min(100, comp + stab + terms_score), 2)
```

---

## 12. Delivery Performance

### Encja: DeliveryRecord (per dostawa)

| Atrybut | Typ | Opis |
|---------|-----|------|
| delivery_id | UUID PK | |
| supplier_id | UUID FK | |
| purchase_order_id | VARCHAR(50) | Numer PO (z ERP) |
| po_line_id | VARCHAR(50) | Linia PO |
| material_id | UUID FK | |
| order_date | DATE | Data PO |
| confirmed_delivery_date | DATE | Potwierdzona data |
| requested_delivery_date | DATE | Żądana data |
| actual_delivery_date | DATE | Rzeczywista data GR |
| quantity_ordered | NUMERIC(14,4) | |
| quantity_delivered | NUMERIC(14,4) | |
| quantity_accepted | NUMERIC(14,4) | Po kontroli |
| quantity_rejected | NUMERIC(14,4) | |
| otd_flag | BOOLEAN | On-Time Delivery |
| otif_flag | BOOLEAN | On-Time In-Full |
| early_delivery_days | SMALLINT | Ile dni za wcześnie (>0 = problem) |
| late_delivery_days | SMALLINT | Ile dni za późno |
| shipping_docs_ok | BOOLEAN | Kompletna dokumentacja |
| customs_cleared | BOOLEAN | Odprawa celna (import) |
| incoterms_used | VARCHAR(10) | |
| carrier | VARCHAR(100) | Przewoźnik |
| tracking_number | VARCHAR(100) | |
| invoice_id | VARCHAR(50) | |
| invoice_amount_eur | NUMERIC(14,2) | |
| payment_on_time | BOOLEAN | Czy MY zapłaciliśmy na czas |
| source | ENUM | ERP_AUTO, MANUAL |

### KPI Delivery Performance

```python
@dataclass
class DeliveryPerformanceKPIs:
    # Core delivery KPIs
    otd_pct:           float   # On-Time Delivery %
    otif_pct:          float   # On-Time In-Full %
    fill_rate_pct:     float   # Quantity delivered / ordered %
    acceptance_rate_pct: float # Accepted / delivered %

    # Lead time KPIs
    avg_lead_time_days:  float
    lead_time_p90_days:  float
    lead_time_cv_pct:    float  # Coefficient of variation (consistency)

    # Documentation KPIs
    doc_compliance_pct:  float  # % deliveries with complete docs

    # Trend
    otd_trend_3m:        float  # Change in OTD % over last 3 months
    deliveries_count:    int
    total_spend_eur:     float

    # Derived
    @property
    def delivery_score(self) -> float:
        """Composite delivery score (0–100)."""
        return (
            self._score_otd(self.otd_pct)   * 0.40 +
            self._score_otif(self.otif_pct) * 0.35 +
            self.fill_rate_pct / 100        * 0.15 +
            self.doc_compliance_pct / 100   * 0.10
        ) * 100
```
