# Material Intelligence Engine — Model Kosztowy, Ceny Rynkowe, Dostawcy

## 12. Cost Model — Model kosztowy

### Architektura modelu kosztowego materiału

```
MaterialCostModel
├── BasePriceResolver        -- pobiera aktualną cenę bazową
├── CoefficientApplier       -- nakłada współczynniki kosztowe
├── UnitConverter            -- przelicza jednostki (EUR/kg ↔ EUR/m² ↔ EUR/m³)
├── QuantityDiscountCalc     -- kalkuluje rabaty ilościowe
└── TotalMaterialCostOutput  -- zwraca strukturę kosztową
```

### Formuła kosztu materiału netto

```
Koszt_materiału = Masa_netto × Cena_bazowa × Współczynnik_uzysku
                  + Koszt_odpadu
                  + Koszt_certyfikacji
                  + Koszt_obsługi
                  + Dopłata_mały_wolumen (jeśli dotyczy)
```

Gdzie:

```
Masa_netto          = Masa_teoretyczna × (1 + naddatek_obróbki)
Cena_zakupu         = Cena_rynkowa × (1 + marża_dostawcy) × kurs_waluty
Koszt_odpadu        = Masa_brutto × Wskaźnik_odpadów × Cena_złomu (odzysk)
Współczynnik_uzysku = 1 / (Yield_rate_pct / 100)
Koszt_obsługi       = Wartość_materiału × Handling_cost_pct / 100
```

### Tabela współczynników kosztowych per klasa materiału (domyślne)

| Klasa materiału | Odpady [%] | Uzysk [%] | Naddatek obróbki | Obsługa [%] | Magazyn [%/mies] |
|-----------------|-----------|-----------|-----------------|------------|-----------------|
| Stale HR/CR (blachy) | 8–15 | 85–92 | 1–3 mm | 1.5 | 0.4 |
| Stale nierdzewne | 10–20 | 80–90 | 1–3 mm | 2.0 | 0.5 |
| Aluminium | 12–18 | 82–88 | 1–2 mm | 2.5 | 0.3 |
| Miedź/Mosiądz | 10–15 | 85–90 | 1 mm | 3.0 | 0.5 |
| Tworzywa (granulat) | 2–5 | 95–98 | — | 1.0 | 0.2 |
| Tworzywa (wtrysk) | 3–8 | 92–97 | — | 1.5 | 0.3 |
| MDF/HDF/Sklejka | 10–20 | 80–90 | 0 | 1.0 | 0.5 |
| Tektura falista | 5–10 | 90–95 | — | 0.8 | 0.8 |
| Kompozyty | 15–25 | 75–85 | — | 3.0 | 0.3 |
| Pianki | 5–10 | 90–95 | — | 1.5 | 1.0 |
| Gumy/Elastomery | 5–12 | 88–95 | — | 2.0 | 0.5 |

### Przykładowa kalkulacja — blacha S355 laser

```
Dane:
  - Materiał: S355JR, blacha 5mm, EN 10025
  - Masa nominalna części: 12.5 kg
  - Cena zakupu: 0.85 EUR/kg (DDP)
  - Współczynnik naddatku: 5% (nesting efficiency 76%)
  - Wskaźnik odpadów: 24% (rozliczono nesting)
  - Cena złomu: 0.20 EUR/kg
  - Koszt obsługi: 1.5%
  - Certyfikacja: 0.02 EUR/kg

Obliczenia:
  Masa_brutto         = 12.5 × 1.05 = 13.125 kg
  Masa_kupowana       = 13.125 / (1 - 0.24) = 17.27 kg
  Koszt_materia_brutto = 17.27 × 0.85 = 14.68 EUR
  Odzysk_złomu        = (17.27 - 12.5) × 0.20 = 0.95 EUR
  Koszt_certyfikacji  = 17.27 × 0.02 = 0.35 EUR
  Koszt_obsługi       = 14.68 × 0.015 = 0.22 EUR

  Koszt_netto_materiału = 14.68 - 0.95 + 0.35 + 0.22 = 14.30 EUR
  Koszt_na_kg_części   = 14.30 / 12.5 = 1.144 EUR/kg
```

### Kalkulacja kosztu materiału — SQL Function

```sql
CREATE OR REPLACE FUNCTION calculate_part_material_cost(
    p_material_id     UUID,
    p_net_mass_kg     NUMERIC,
    p_supplier_id     UUID DEFAULT NULL
)
RETURNS JSONB AS $$
DECLARE
    v_price           NUMERIC;
    v_currency        CHAR(3);
    v_coeffs          RECORD;
    v_gross_mass      NUMERIC;
    v_purchase_mass   NUMERIC;
    v_material_cost   NUMERIC;
    v_scrap_recovery  NUMERIC;
    v_handling_cost   NUMERIC;
    v_cert_cost       NUMERIC;
    v_total_cost      NUMERIC;
BEGIN
    -- Get price (supplier-specific or market)
    IF p_supplier_id IS NOT NULL THEN
        SELECT spr.price_value, spr.currency
        INTO v_price, v_currency
        FROM supplier_price_records spr
        WHERE spr.material_id = p_material_id
          AND spr.supplier_id = p_supplier_id
          AND spr.is_active = TRUE
        ORDER BY spr.price_date DESC
        LIMIT 1;
    END IF;

    IF v_price IS NULL THEN
        SELECT price_value, currency
        INTO v_price, v_currency
        FROM market_price_records
        WHERE material_id = p_material_id
          AND price_type = 'PURCHASE'
          AND is_active = TRUE
        ORDER BY price_date DESC
        LIMIT 1;
    END IF;

    -- Get coefficients
    SELECT scrap_rate_pct, yield_rate_pct, handling_cost_pct,
           certification_cost_eur_kg, cutting_waste_pct
    INTO v_coeffs
    FROM material_cost_coefficients
    WHERE material_id = p_material_id
      AND valid_to IS NULL
    LIMIT 1;

    -- Calculations
    v_gross_mass     := p_net_mass_kg * (1 + COALESCE(v_coeffs.cutting_waste_pct, 10) / 100);
    v_purchase_mass  := v_gross_mass / (COALESCE(v_coeffs.yield_rate_pct, 90) / 100);
    v_material_cost  := v_purchase_mass * v_price;
    v_scrap_recovery := (v_purchase_mass - p_net_mass_kg) * v_price * 0.25;
    v_handling_cost  := v_material_cost * COALESCE(v_coeffs.handling_cost_pct, 1.5) / 100;
    v_cert_cost      := v_purchase_mass * COALESCE(v_coeffs.certification_cost_eur_kg, 0);
    v_total_cost     := v_material_cost - v_scrap_recovery + v_handling_cost + v_cert_cost;

    RETURN jsonb_build_object(
        'net_mass_kg',      p_net_mass_kg,
        'gross_mass_kg',    ROUND(v_gross_mass, 4),
        'purchase_mass_kg', ROUND(v_purchase_mass, 4),
        'base_price',       v_price,
        'currency',         v_currency,
        'material_cost',    ROUND(v_material_cost, 4),
        'scrap_recovery',   ROUND(v_scrap_recovery, 4),
        'handling_cost',    ROUND(v_handling_cost, 4),
        'cert_cost',        ROUND(v_cert_cost, 4),
        'total_cost',       ROUND(v_total_cost, 4),
        'cost_per_kg_net',  ROUND(v_total_cost / p_net_mass_kg, 4)
    );
END;
$$ LANGUAGE plpgsql STABLE;
```

---

## 13. Market Price Layer — Architektura cen rynkowych

### Architektura warstwy cen

```
MarketPriceLayer
├── PriceSourceConnectors     -- konektory do zewnętrznych źródeł
│   ├── LMEConnector          -- London Metal Exchange
│   ├── PlattsConnector       -- S&P Global Platts (stal)
│   ├── ICAConnector          -- ICIS (tworzywa)
│   ├── FASISConnector        -- FASIS (tworzywa Europa)
│   ├── EurostatConnector     -- dane statystyczne EU
│   └── ManualPriceConnector  -- ręczne wprowadzanie
├── PriceNormalizer           -- przelicza do EUR/kg (standardowa jednostka)
├── PriceValidator            -- waliduje odchylenia (anomalie)
├── PriceAggregator           -- agreguje z wielu źródeł (średnia ważona)
├── TrendCalculator           -- oblicza trendy (MA7, MA30, MA90)
└── ForecastEngine            -- prognoza cen (Prophet/ARIMA/XGBoost)
```

### Źródła cen per kategoria materiału

| Kategoria | Źródło | Częstotliwość | Jednostka |
|-----------|--------|---------------|-----------|
| Stal HR/CR (EU) | S&P Platts Steel EU | Dziennie | EUR/ton |
| Stal nierdzewna | CRU Stainless | Tygodniowo | EUR/ton |
| Aluminium (LME) | LME Official Price | Dziennie | USD/ton |
| Miedź (LME) | LME Official Price | Dziennie | USD/ton |
| Mosiądz | LME Cu + Zn composite | Dziennie | EUR/kg |
| ABS, PC, PA | ICIS Polymers EU | Tygodniowo | EUR/ton |
| PP, PE, PET | ICIS Polymers EU | Tygodniowo | EUR/ton |
| MDF/HDF | EURO PANEL PRODUCTS | Miesięcznie | EUR/m³ |
| Tektura falista | RISI (FOEX) / CEPI | Miesięcznie | EUR/ton |
| Włókno szklane | Reinforced Plastics | Miesięcznie | EUR/kg |
| Włókno węglowe | JEC Composites | Miesięcznie | EUR/kg |

### Struktura konektora cen (interfejs)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

@dataclass
class RawPriceData:
    source_code: str
    material_grade: str
    price_date: date
    price_value: Decimal
    currency: str
    unit: str
    region: str
    quality_indicator: int  # 1-5

class PriceSourceConnector(ABC):
    @abstractmethod
    def fetch_latest(self, material_codes: list[str]) -> list[RawPriceData]:
        """Fetch latest prices for given material codes."""
        pass

    @abstractmethod
    def fetch_history(self, material_code: str,
                      from_date: date, to_date: date) -> list[RawPriceData]:
        """Fetch historical price series."""
        pass

    @abstractmethod
    def get_supported_materials(self) -> list[str]:
        """Return list of supported material codes."""
        pass


class LMEConnector(PriceSourceConnector):
    """
    LME (London Metal Exchange) connector.
    Fetches official settlement prices for Cu, Al, Zn, Pb, Ni, Sn.
    API: LME Data Services (licensed access required).
    """
    MATERIAL_CODES = {
        'MET.CU.PU': 'LME_CU_3M',
        'MET.AL':    'LME_AH_3M',
        'MET.CU.BR': 'LME_ZN_3M',  # Mosiądz: Cu + Zn composite
    }

    def fetch_latest(self, material_codes):
        # Implementation uses LME Data Services REST API
        ...
```

### Normalizacja cen — reguły

```python
class PriceNormalizer:
    """
    Converts all prices to EUR/kg (canonical unit).
    Uses ECB exchange rates (fetched daily).
    """

    UNIT_FACTORS = {
        'PER_TON':   0.001,    # 1 ton = 1000 kg
        'PER_KG':    1.0,
        'PER_M2':    None,     # Requires density
        'PER_M3':    None,     # Requires density
        'PER_PIECE': None,     # Requires unit weight
    }

    def normalize(self, raw: RawPriceData, material: Material) -> NormalizedPrice:
        eur_rate = self.exchange_service.get_rate(raw.currency, 'EUR', raw.price_date)
        unit_factor = self.UNIT_FACTORS.get(raw.unit)

        if unit_factor is None:
            density = material.properties.density_kg_m3
            if raw.unit == 'PER_M3':
                unit_factor = 1.0 / density
            elif raw.unit == 'PER_M2':
                thickness = material.properties.standard_thickness_mm / 1000
                unit_factor = 1.0 / (density * thickness)

        price_eur_kg = raw.price_value * eur_rate * unit_factor
        return NormalizedPrice(price_eur_kg=price_eur_kg, date=raw.price_date)
```

### Anomaly Detection — walidacja cen

```python
class PriceValidator:
    """
    Detects price anomalies before persistence.
    Rules:
    - Max daily change: configurable per material class
    - Min/Max absolute price bounds
    - Comparison with other sources
    """

    MAX_DAILY_CHANGE_PCT = {
        'METAL':     8.0,   # LME can move 5-7% daily
        'POLYMER':   5.0,
        'WOOD':      3.0,
        'PACKAGING': 3.0,
    }

    def validate(self, new_price: NormalizedPrice,
                 last_price: NormalizedPrice,
                 material: Material) -> ValidationResult:
        change_pct = abs(new_price.price_eur_kg - last_price.price_eur_kg) \
                     / last_price.price_eur_kg * 100

        max_change = self.MAX_DAILY_CHANGE_PCT.get(material.material_class, 5.0)

        if change_pct > max_change:
            return ValidationResult(
                status='WARNING',
                message=f"Price change {change_pct:.1f}% exceeds threshold {max_change}%",
                requires_manual_review=True
            )
        return ValidationResult(status='OK')
```

### Price Forecast Engine

```python
class PriceForecastEngine:
    """
    Generates price forecasts using time series models.
    Supports: Prophet, ARIMA, XGBoost regression.
    Forecast horizons: 7, 30, 90, 180 days.
    """

    def forecast(self, material_id: str,
                 horizon_days: int = 30) -> ForecastResult:
        history = self.get_price_history(material_id, days=365)
        features = self.build_features(history)

        # Model selection by data volume
        if len(history) >= 730:
            model = ProphetModel()
        elif len(history) >= 90:
            model = ARIMAModel()
        else:
            model = LinearTrendModel()

        forecast = model.fit(history).predict(horizon_days)
        return ForecastResult(
            material_id=material_id,
            horizon_days=horizon_days,
            values=forecast.values,
            lower_95=forecast.lower_bound,
            upper_95=forecast.upper_bound,
            mape=model.last_mape,
            model_name=model.name
        )
```

---

## 14. Supplier Mapping — Powiązania z dostawcami

### Architektura mapowania dostawców

```
SupplierMaterialLayer
├── SupplierMaterialCatalog   -- katalog materiałów u dostawców
├── SupplierPriceFeed         -- ceny ofertowe od dostawców
├── SupplyRiskAssessor        -- ocena ryzyka dostaw
├── LeadTimeTracker           -- śledzenie lead time
└── PreferredSupplierEngine   -- logika wyboru preferowanego dostawcy
```

### Supplier price records (oferty cenowe dostawców)

```sql
CREATE TABLE supplier_price_records (
    spr_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    material_id        UUID NOT NULL REFERENCES materials(material_id),
    supplier_id        UUID NOT NULL,
    price_value        NUMERIC(14,4) NOT NULL,
    currency           CHAR(3) NOT NULL DEFAULT 'EUR',
    unit               price_unit_enum NOT NULL DEFAULT 'PER_KG',
    min_quantity       NUMERIC(14,4),
    max_quantity       NUMERIC(14,4),
    valid_from         DATE NOT NULL,
    valid_to           DATE,
    incoterms          VARCHAR(10),
    delivery_days      SMALLINT,
    notes              TEXT,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    quote_reference    VARCHAR(100),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT chk_quantity_range CHECK (
        max_quantity IS NULL OR max_quantity >= min_quantity
    )
);

CREATE INDEX idx_spr_material_supplier ON supplier_price_records(material_id, supplier_id)
    WHERE is_active = TRUE;
CREATE INDEX idx_spr_valid ON supplier_price_records(valid_from, valid_to)
    WHERE is_active = TRUE;
```

### Supply Risk Assessment

```python
@dataclass
class SupplyRiskAssessment:
    material_id: str
    overall_risk: str          # LOW, MEDIUM, HIGH, CRITICAL
    single_source: bool
    approved_supplier_count: int
    min_lead_time_days: int
    geographic_concentration: str   # EU, ASIA, GLOBAL
    price_volatility_90d_pct: float
    last_shortage_event: date | None
    risk_factors: list[str]
    recommendations: list[str]

class SupplyRiskAssessor:
    def assess(self, material_id: str) -> SupplyRiskAssessment:
        mappings = self.get_active_supplier_mappings(material_id)
        price_history = self.get_price_history(material_id, days=90)

        single_source = len(mappings) == 1
        approved_count = sum(1 for m in mappings if m.is_approved)
        volatility = self._calc_price_volatility(price_history)

        risk_factors = []
        if single_source:
            risk_factors.append("SINGLE_SOURCE_RISK")
        if approved_count == 0:
            risk_factors.append("NO_APPROVED_SUPPLIER")
        if volatility > 15:
            risk_factors.append(f"HIGH_PRICE_VOLATILITY_{volatility:.0f}PCT")
        if all(m.origin_country in ['CN', 'TW', 'KR'] for m in mappings):
            risk_factors.append("GEOGRAPHIC_CONCENTRATION_ASIA")

        overall = self._determine_overall_risk(risk_factors, single_source, volatility)

        return SupplyRiskAssessment(
            material_id=material_id,
            overall_risk=overall,
            single_source=single_source,
            approved_supplier_count=approved_count,
            risk_factors=risk_factors,
            recommendations=self._build_recommendations(risk_factors)
        )
```

### Lead Time Matrix

```sql
-- View: materiały z aktualnym lead time od najlepszego dostawcy
CREATE VIEW v_material_lead_times AS
SELECT
    m.material_id,
    m.material_code,
    m.material_name,
    MIN(smm.lead_time_days)         AS min_lead_time_days,
    MAX(smm.lead_time_days)         AS max_lead_time_days,
    AVG(smm.lead_time_days)         AS avg_lead_time_days,
    MIN(smm.lead_time_days_express) AS express_lead_time_days,
    COUNT(smm.supplier_id)          AS supplier_count,
    BOOL_OR(smm.is_single_source)   AS has_single_source_risk
FROM materials m
JOIN supplier_material_mapping smm ON smm.material_id = m.material_id
WHERE smm.active = TRUE
  AND smm.is_approved = TRUE
  AND m.status = 'ACTIVE'
GROUP BY m.material_id, m.material_code, m.material_name;
```
