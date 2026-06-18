# Supplier Intelligence Engine — Scoring, Ranking & Risk Analysis

## 8. Supplier Scoring — Composite Score Algorithm

### Architektura scorecarda

Composite Score = Σ (komponent_i × waga_i), gdzie Σ wagi = 1.0

| Komponent | Waga | Zakres | Źródło danych |
|-----------|------|--------|---------------|
| Quality Score | 35% | 0–100 | PPM, NCR, certyfikacje |
| Delivery Score | 25% | 0–100 | OTD, OTIF, lead time reliability |
| Price Score | 20% | 0–100 | Competitiveness vs market benchmark |
| Service Score | 10% | 0–100 | Responsiveness, 8D closure, communication |
| Risk Score | 10% | 0–100 | Financial, geopolitical, concentration risk |

### Rating Classes

| Klasa | Zakres | Oznaczenie | Znaczenie operacyjne |
|-------|--------|------------|---------------------|
| A | 85–100 | ★★★★★ | Preferred supplier — fast-track RFQ, auto-approval |
| B | 70–84 | ★★★★☆ | Approved supplier — standard process |
| C | 55–69 | ★★★☆☆ | Conditional — corrective action plan required |
| D | 40–54 | ★★☆☆☆ | Probation — enhanced monitoring |
| E | 25–39 | ★☆☆☆☆ | Suspension candidate — procurement review |
| F | 0–24 | ✗ | Suspended/Blacklisted |

### ScorecardCalculator

```python
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional
from enum import Enum
import statistics

class RatingClass(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"

@dataclass
class ScorecardInput:
    supplier_id: str
    period_start: date
    period_end: date
    # Quality
    ppm: float
    ncr_count: int
    ncr_critical_count: int
    open_8d_overdue_count: int
    active_cert_count: int
    required_cert_count: int
    # Delivery
    otd_pct: float
    otif_pct: float
    avg_delay_days: float
    lead_time_reliability_pct: float
    # Price
    price_vs_benchmark_pct: float       # positive = above market, negative = below market
    price_trend_yoy_pct: float
    price_stability_cv: float           # coefficient of variation
    # Service
    avg_response_hours: float
    avg_8d_closure_days: float
    portal_compliance_pct: float        # documentation completeness
    escalation_count: int
    # Risk
    financial_risk_score: float         # 0-100 (100=safe)
    geopolitical_risk_score: float      # 0-100 (100=safe)
    concentration_risk_score: float     # 0-100 (100=diversified)

@dataclass
class ScorecardResult:
    supplier_id: str
    period_start: date
    period_end: date
    quality_score: float
    delivery_score: float
    price_score: float
    service_score: float
    risk_score: float
    composite_score: float
    rating_class: RatingClass
    trend_vs_prior: Optional[float]
    quality_detail: dict
    delivery_detail: dict
    price_detail: dict
    service_detail: dict
    risk_detail: dict

class ScorecardCalculator:
    WEIGHTS = {
        "quality":  0.35,
        "delivery": 0.25,
        "price":    0.20,
        "service":  0.10,
        "risk":     0.10,
    }

    def calculate(self, inp: ScorecardInput) -> ScorecardResult:
        q = self._quality(inp)
        d = self._delivery(inp)
        p = self._price(inp)
        s = self._service(inp)
        r = self._risk(inp)

        composite = (
            q["score"] * self.WEIGHTS["quality"] +
            d["score"] * self.WEIGHTS["delivery"] +
            p["score"] * self.WEIGHTS["price"] +
            s["score"] * self.WEIGHTS["service"] +
            r["score"] * self.WEIGHTS["risk"]
        )
        composite = round(composite, 2)

        return ScorecardResult(
            supplier_id=inp.supplier_id,
            period_start=inp.period_start,
            period_end=inp.period_end,
            quality_score=q["score"],
            delivery_score=d["score"],
            price_score=p["score"],
            service_score=s["score"],
            risk_score=r["score"],
            composite_score=composite,
            rating_class=self._classify(composite),
            trend_vs_prior=None,
            quality_detail=q,
            delivery_detail=d,
            price_detail=p,
            service_detail=s,
            risk_detail=r,
        )

    # ------------------------------------------------------------------ quality
    def _quality(self, inp: ScorecardInput) -> dict:
        ppm_score    = self._score_ppm(inp.ppm)
        ncr_score    = self._score_ncr(inp.ncr_count, inp.ncr_critical_count, inp.open_8d_overdue_count)
        cert_score   = self._score_certs(inp.active_cert_count, inp.required_cert_count)

        score = round(ppm_score * 0.50 + ncr_score * 0.35 + cert_score * 0.15, 2)
        return {"score": score, "ppm_score": ppm_score, "ncr_score": ncr_score, "cert_score": cert_score}

    @staticmethod
    def _score_ppm(ppm: float) -> float:
        thresholds = [(0, 40), (10, 38), (50, 34), (100, 28), (500, 20), (1000, 12), (5000, 5)]
        for limit, pts in reversed(thresholds):
            if ppm >= limit:
                return float(pts)
        return 0.0

    @staticmethod
    def _score_ncr(count: int, critical: int, overdue_8d: int) -> float:
        base = max(0.0, 40.0 - count * 4.0)
        penalty = critical * 8.0 + overdue_8d * 5.0
        return max(0.0, min(40.0, base - penalty))

    @staticmethod
    def _score_certs(active: int, required: int) -> float:
        if required == 0:
            return 20.0
        ratio = active / required
        if ratio >= 1.0: return 20.0
        if ratio >= 0.8: return 15.0
        if ratio >= 0.6: return 10.0
        return 5.0

    # ----------------------------------------------------------------- delivery
    def _delivery(self, inp: ScorecardInput) -> dict:
        otd_score  = self._score_otd(inp.otd_pct)
        otif_score = self._score_otif(inp.otif_pct)
        rel_score  = self._score_lead_time_reliability(inp.lead_time_reliability_pct)
        delay_pen  = self._score_delay_penalty(inp.avg_delay_days)

        score = round(otd_score * 0.40 + otif_score * 0.35 + rel_score * 0.15 + delay_pen * 0.10, 2)
        return {"score": score, "otd_score": otd_score, "otif_score": otif_score,
                "reliability_score": rel_score, "delay_penalty": delay_pen}

    @staticmethod
    def _score_otd(otd: float) -> float:
        if otd >= 98: return 40.0
        if otd >= 95: return 35.0
        if otd >= 90: return 28.0
        if otd >= 85: return 20.0
        if otd >= 75: return 12.0
        return 5.0

    @staticmethod
    def _score_otif(otif: float) -> float:
        if otif >= 97: return 35.0
        if otif >= 93: return 30.0
        if otif >= 88: return 23.0
        if otif >= 82: return 15.0
        return 5.0

    @staticmethod
    def _score_lead_time_reliability(reliability: float) -> float:
        if reliability >= 95: return 15.0
        if reliability >= 90: return 12.0
        if reliability >= 80: return 8.0
        return 3.0

    @staticmethod
    def _score_delay_penalty(avg_delay_days: float) -> float:
        if avg_delay_days <= 0: return 10.0
        if avg_delay_days <= 1: return 8.0
        if avg_delay_days <= 3: return 5.0
        if avg_delay_days <= 7: return 2.0
        return 0.0

    # -------------------------------------------------------------------- price
    def _price(self, inp: ScorecardInput) -> dict:
        comp_score    = self._score_price_competitiveness(inp.price_vs_benchmark_pct)
        trend_score   = self._score_price_trend(inp.price_trend_yoy_pct)
        stab_score    = self._score_price_stability(inp.price_stability_cv)

        score = round(comp_score * 0.60 + trend_score * 0.25 + stab_score * 0.15, 2)
        return {"score": score, "competitiveness_score": comp_score,
                "trend_score": trend_score, "stability_score": stab_score}

    @staticmethod
    def _score_price_competitiveness(diff_pct: float) -> float:
        if diff_pct <= -10: return 60.0
        if diff_pct <= -5:  return 55.0
        if diff_pct <= 0:   return 50.0
        if diff_pct <= 5:   return 40.0
        if diff_pct <= 10:  return 28.0
        if diff_pct <= 20:  return 15.0
        return 5.0

    @staticmethod
    def _score_price_trend(yoy_pct: float) -> float:
        if yoy_pct <= -5:  return 25.0
        if yoy_pct <= 0:   return 22.0
        if yoy_pct <= 3:   return 18.0
        if yoy_pct <= 7:   return 12.0
        if yoy_pct <= 15:  return 6.0
        return 2.0

    @staticmethod
    def _score_price_stability(cv: float) -> float:
        if cv <= 0.05: return 15.0
        if cv <= 0.10: return 12.0
        if cv <= 0.20: return 8.0
        if cv <= 0.30: return 4.0
        return 1.0

    # ------------------------------------------------------------------ service
    def _service(self, inp: ScorecardInput) -> dict:
        resp_score = self._score_responsiveness(inp.avg_response_hours)
        d8_score   = self._score_8d_closure(inp.avg_8d_closure_days)
        comp_score = self._score_portal_compliance(inp.portal_compliance_pct)
        esc_pen    = max(0.0, 10.0 - inp.escalation_count * 3.0)

        score = round(resp_score * 0.35 + d8_score * 0.30 + comp_score * 0.20 + esc_pen * 0.15, 2)
        return {"score": score, "responsiveness_score": resp_score,
                "8d_score": d8_score, "compliance_score": comp_score}

    @staticmethod
    def _score_responsiveness(hours: float) -> float:
        if hours <= 4:  return 35.0
        if hours <= 8:  return 30.0
        if hours <= 24: return 22.0
        if hours <= 48: return 12.0
        return 4.0

    @staticmethod
    def _score_8d_closure(days: float) -> float:
        if days <= 14: return 30.0
        if days <= 21: return 24.0
        if days <= 30: return 15.0
        if days <= 45: return 7.0
        return 2.0

    @staticmethod
    def _score_portal_compliance(pct: float) -> float:
        if pct >= 98: return 20.0
        if pct >= 90: return 15.0
        if pct >= 80: return 10.0
        return 4.0

    # --------------------------------------------------------------------- risk
    def _risk(self, inp: ScorecardInput) -> dict:
        fin_score  = inp.financial_risk_score * 0.40
        geo_score  = inp.geopolitical_risk_score * 0.35
        conc_score = inp.concentration_risk_score * 0.25
        score = round(fin_score + geo_score + conc_score, 2)
        return {"score": score, "financial_component": fin_score,
                "geopolitical_component": geo_score, "concentration_component": conc_score}

    # ----------------------------------------------------------------- classify
    @staticmethod
    def _classify(score: float) -> RatingClass:
        if score >= 85: return RatingClass.A
        if score >= 70: return RatingClass.B
        if score >= 55: return RatingClass.C
        if score >= 40: return RatingClass.D
        if score >= 25: return RatingClass.E
        return RatingClass.F
```

### Score History & Trend Detection

```python
@dataclass
class ScoreTrend:
    supplier_id: str
    current_score: float
    prior_score: float
    delta: float
    direction: str          # IMPROVING / STABLE / DECLINING / CRITICAL_DECLINE
    periods_consecutive_decline: int
    alert_required: bool

class TrendAnalyzer:
    CRITICAL_DECLINE_THRESHOLD = -10.0
    ALERT_CONSECUTIVE_DECLINE  = 3

    def analyze(self, history: list[float]) -> ScoreTrend:
        if len(history) < 2:
            return None
        current = history[-1]
        prior   = history[-2]
        delta   = round(current - prior, 2)

        declines = 0
        for i in range(len(history) - 1, 0, -1):
            if history[i] < history[i - 1]:
                declines += 1
            else:
                break

        if delta <= self.CRITICAL_DECLINE_THRESHOLD:
            direction = "CRITICAL_DECLINE"
        elif delta < -2:
            direction = "DECLINING"
        elif delta > 2:
            direction = "IMPROVING"
        else:
            direction = "STABLE"

        return ScoreTrend(
            supplier_id="",
            current_score=current,
            prior_score=prior,
            delta=delta,
            direction=direction,
            periods_consecutive_decline=declines,
            alert_required=(declines >= self.ALERT_CONSECUTIVE_DECLINE or
                            delta <= self.CRITICAL_DECLINE_THRESHOLD),
        )
```

---

## 9. Supplier Ranking

### Multi-Dimensional Ranking

Ranking jest obliczany per kategoria zakupowa (`category_id`), a nie globalnie — dostawca może być #1 w stalach, a #15 w elektronice.

```sql
CREATE VIEW v_supplier_ranking AS
WITH latest_scorecards AS (
    SELECT DISTINCT ON (supplier_id, category_id)
        supplier_id,
        category_id,
        composite_score,
        quality_score,
        delivery_score,
        price_score,
        service_score,
        risk_score,
        rating_class,
        period_end
    FROM supplier_scorecards
    WHERE is_active = TRUE
    ORDER BY supplier_id, category_id, period_end DESC
),
ranked AS (
    SELECT
        ls.*,
        RANK() OVER (PARTITION BY category_id ORDER BY composite_score DESC) AS rank_in_category,
        PERCENT_RANK() OVER (PARTITION BY category_id ORDER BY composite_score) AS percentile_in_category,
        COUNT(*) OVER (PARTITION BY category_id) AS total_in_category
    FROM latest_scorecards ls
)
SELECT
    r.*,
    s.legal_name,
    s.country_code,
    s.strategic_tier,
    CASE
        WHEN rank_in_category = 1 THEN 'LEADER'
        WHEN percentile_in_category >= 0.75 THEN 'TOP_QUARTILE'
        WHEN percentile_in_category >= 0.50 THEN 'ABOVE_AVERAGE'
        WHEN percentile_in_category >= 0.25 THEN 'BELOW_AVERAGE'
        ELSE 'BOTTOM_QUARTILE'
    END AS quartile_label
FROM ranked r
JOIN suppliers s ON r.supplier_id = s.supplier_id;
```

### Tier Assignment Algorithm

```python
from enum import Enum

class StrategicTier(str, Enum):
    TIER1 = "TIER1"   # Strategic — partnership model, JIT, VMI eligible
    TIER2 = "TIER2"   # Preferred — standard bidding
    TIER3 = "TIER3"   # Approved — limited categories
    SPOT  = "SPOT"    # Spot/opportunistic — no framework agreement

@dataclass
class TierAssignmentCriteria:
    composite_score: float
    spend_share_pct: float          # % of category spend
    is_single_source: bool
    years_active: int
    strategic_material_coverage: bool

class TierAssigner:
    def assign(self, c: TierAssignmentCriteria) -> StrategicTier:
        if (c.composite_score >= 80 and
                (c.spend_share_pct >= 20 or c.strategic_material_coverage) and
                c.years_active >= 2):
            return StrategicTier.TIER1

        if c.composite_score >= 65 and c.years_active >= 1:
            return StrategicTier.TIER2

        if c.composite_score >= 50:
            return StrategicTier.TIER3

        return StrategicTier.SPOT
```

### Ranking Change Notifications

| Zmiana | Próg | Akcja |
|--------|------|-------|
| Awans do TIER1 | score ↑ przez ≥2 okresy do ≥80 | Notify category buyer, update RFQ preferred list |
| Degradacja TIER1→TIER2 | score < 75 przez 2 okresy | Notify procurement manager, review contract |
| Wejście do TOP 3 kategorii | rank ≤ 3 | Flag in RFQ engine |
| Wypadnięcie z TOP 10 | rank > 10 AND prior rank ≤ 10 | Alert category manager |
| Klasa F (suspension) | score < 25 | Block in ERP, alert procurement director |

---

## 10. Risk Analysis

### Risk Taxonomy

```
SupplierRisk
├── FinancialRisk
│   ├── CreditRisk (D&B score, credit limit)
│   ├── LiquidityRisk (current ratio, cash flow)
│   └── SolvencyRisk (Altman Z-score, D/E ratio)
├── OperationalRisk
│   ├── SingleSiteRisk (concentration of production)
│   ├── KeyPersonRisk (founder/owner dependency)
│   └── QualitySystemRisk (cert expiry, audit findings)
├── GeopoliticalRisk
│   ├── CountryRisk (Coface rating, sanctions lists)
│   ├── TariffRisk (trade war exposure, HS codes)
│   └── DisruptionRisk (natural disasters, conflict proximity)
├── SupplyChainRisk
│   ├── ConcentrationRisk (single source, >30% spend)
│   ├── SubSupplierRisk (Tier-2/3 visibility)
│   └── LogisticsRisk (port congestion, carrier dependency)
└── ComplianceRisk
    ├── SanctionsRisk (OFAC, EU, UK lists)
    ├── RegulatoryRisk (REACH, RoHS, conflict minerals)
    └── ESGRisk (labor practices, carbon footprint)
```

### RiskProfile Entity

```python
@dataclass
class RiskFactor:
    factor_id: str
    category: str               # FINANCIAL / OPERATIONAL / GEOPOLITICAL / SUPPLY_CHAIN / COMPLIANCE
    sub_category: str
    raw_score: float            # 0-100 (0=high risk, 100=no risk)
    weight: float
    evidence: str
    source: str
    assessed_at: date
    valid_until: Optional[date]

@dataclass
class RiskAlert:
    alert_id: str
    supplier_id: str
    alert_type: str
    severity: str               # LOW / MEDIUM / HIGH / CRITICAL
    title: str
    description: str
    recommended_action: str
    triggered_at: datetime
    resolved_at: Optional[datetime]
    owner: str

@dataclass
class RiskProfile:
    supplier_id: str
    overall_risk_score: float   # 0-100 (100=lowest risk)
    risk_class: str             # LOW / MEDIUM / HIGH / CRITICAL
    factors: list[RiskFactor]
    active_alerts: list[RiskAlert]
    last_assessed: date
    next_review_date: date
```

### Risk Scoring Engine

```python
class RiskScoringEngine:
    FACTOR_WEIGHTS = {
        "FINANCIAL":     0.30,
        "GEOPOLITICAL":  0.25,
        "SUPPLY_CHAIN":  0.25,
        "OPERATIONAL":   0.15,
        "COMPLIANCE":    0.05,
    }

    def calculate_overall_risk(self, factors: list[RiskFactor]) -> float:
        category_scores: dict[str, list[float]] = {}
        for f in factors:
            category_scores.setdefault(f.category, []).append(f.raw_score * f.weight)

        weighted_sum = 0.0
        weight_total = 0.0
        for category, scores in category_scores.items():
            cat_score = sum(scores) / sum(f.weight for f in factors if f.category == category)
            cat_weight = self.FACTOR_WEIGHTS.get(category, 0.0)
            weighted_sum  += cat_score * cat_weight
            weight_total  += cat_weight

        return round(weighted_sum / weight_total if weight_total > 0 else 0.0, 2)

    def classify(self, score: float) -> str:
        if score >= 75: return "LOW"
        if score >= 50: return "MEDIUM"
        if score >= 25: return "HIGH"
        return "CRITICAL"

    def generate_alerts(self, supplier_id: str, factors: list[RiskFactor]) -> list[RiskAlert]:
        alerts = []
        for f in factors:
            if f.raw_score < 25:
                severity = "CRITICAL" if f.raw_score < 10 else "HIGH"
                alerts.append(RiskAlert(
                    alert_id=str(uuid4()),
                    supplier_id=supplier_id,
                    alert_type=f.sub_category,
                    severity=severity,
                    title=f"Risk alert: {f.sub_category}",
                    description=f.evidence,
                    recommended_action=self._recommend_action(f),
                    triggered_at=datetime.utcnow(),
                    resolved_at=None,
                    owner="PROCUREMENT_DIRECTOR",
                ))
        return alerts

    @staticmethod
    def _recommend_action(f: RiskFactor) -> str:
        actions = {
            "CreditRisk":        "Request latest financial statements. Consider payment terms revision.",
            "SingleSiteRisk":    "Qualify backup supplier. Increase safety stock.",
            "CountryRisk":       "Evaluate dual-sourcing from alternative country. Review logistics contingency.",
            "ConcentrationRisk": "Initiate alternative supplier qualification. Target <30% single-source.",
            "SanctionsRisk":     "Escalate to compliance team immediately. Freeze new orders.",
        }
        return actions.get(f.sub_category, "Initiate supplier corrective action request (SCAR).")
```

### Concentration Risk Analysis

```python
@dataclass
class ConcentrationRiskReport:
    category_id: str
    single_source_items: list[str]      # material IDs with only 1 approved supplier
    top_supplier_spend_pct: float       # % of category spend with #1 supplier
    herfindahl_index: float             # HHI = Σ(share²), 0=perfect distribution, 1=monopoly
    risk_level: str

class ConcentrationAnalyzer:
    SINGLE_SOURCE_THRESHOLD  = 1        # only 1 approved supplier
    TOP_SUPPLIER_RISK_PCT    = 0.40     # >40% with one supplier = HIGH risk
    HHI_HIGH_THRESHOLD       = 0.25    # HHI > 0.25 = high concentration

    def analyze(self, category_id: str, supplier_spend: dict[str, float]) -> ConcentrationRiskReport:
        total = sum(supplier_spend.values())
        shares = {s: v / total for s, v in supplier_spend.items()} if total > 0 else {}
        hhi = sum(s ** 2 for s in shares.values())
        top_pct = max(shares.values()) if shares else 0.0

        risk_level = "LOW"
        if hhi > self.HHI_HIGH_THRESHOLD or top_pct > self.TOP_SUPPLIER_RISK_PCT:
            risk_level = "HIGH"
        elif hhi > 0.15 or top_pct > 0.30:
            risk_level = "MEDIUM"

        return ConcentrationRiskReport(
            category_id=category_id,
            single_source_items=[],         # populated from DB query
            top_supplier_spend_pct=top_pct,
            herfindahl_index=round(hhi, 4),
            risk_level=risk_level,
        )
```

### Geopolitical Risk Integration

| Źródło | API | Częstotliwość | Dane |
|--------|-----|---------------|------|
| Coface Country Risk | REST | Monthly | 7-tier rating (A1–E) per country |
| Control Risks | REST | Weekly | Political stability, conflict index |
| OFAC SDN List | REST | Daily | Sanctions screening |
| EU Consolidated Sanctions | REST | Daily | EU sanctions list |
| UK FCDO | REST | Weekly | UK sanctions |
| Atradius | REST | Monthly | Trade credit risk per country |

```python
class GeopoliticalRiskMapper:
    COFACE_TO_SCORE = {
        "A1": 95, "A2": 88, "A3": 80, "A4": 70,
        "B":  55, "C":  35, "D":  15, "E": 5,
    }

    def map_country_risk(self, country_code: str, coface_rating: str) -> float:
        base_score = self.COFACE_TO_SCORE.get(coface_rating, 50)
        # Apply regional proximity penalty for conflict zones
        return base_score

    def is_sanctioned(self, entity_name: str, country_code: str,
                      duns_number: Optional[str]) -> bool:
        # Check OFAC + EU + UK lists
        raise NotImplementedError
```

---

## 11. Financial Stability Signals

### Altman Z-Score (Manufacturing)

```
Z' = 0.717×X1 + 0.847×X2 + 3.107×X3 + 0.420×X4 + 0.998×X5

X1 = Working Capital / Total Assets
X2 = Retained Earnings / Total Assets
X3 = EBIT / Total Assets
X4 = Book Value of Equity / Total Liabilities
X5 = Revenue / Total Assets

Interpretacja:
Z' > 2.9   → SAFE ZONE      (risk_score ≥ 75)
1.23–2.9   → GREY ZONE      (risk_score 40–74)
Z' < 1.23  → DISTRESS ZONE  (risk_score < 40)
```

```python
@dataclass
class FinancialStatement:
    supplier_id: str
    fiscal_year: int
    working_capital: float
    total_assets: float
    retained_earnings: float
    ebit: float
    book_value_equity: float
    total_liabilities: float
    revenue: float
    net_income: float
    current_ratio: float
    quick_ratio: float
    debt_to_equity: float
    interest_coverage: float

@dataclass
class AltmanZScore:
    z_score: float
    zone: str           # SAFE / GREY / DISTRESS
    x1: float; x2: float; x3: float; x4: float; x5: float
    risk_score: float   # mapped to 0-100 scale

class AltmanZScoreCalculator:
    def calculate(self, fs: FinancialStatement) -> AltmanZScore:
        if fs.total_assets == 0:
            raise ValueError("Total assets cannot be zero")
        x1 = fs.working_capital      / fs.total_assets
        x2 = fs.retained_earnings    / fs.total_assets
        x3 = fs.ebit                 / fs.total_assets
        x4 = fs.book_value_equity    / fs.total_liabilities if fs.total_liabilities else 0
        x5 = fs.revenue              / fs.total_assets

        z = 0.717*x1 + 0.847*x2 + 3.107*x3 + 0.420*x4 + 0.998*x5

        if z > 2.9:
            zone = "SAFE"
            risk_score = min(100.0, 75.0 + (z - 2.9) * 5.0)
        elif z >= 1.23:
            zone = "GREY"
            risk_score = 40.0 + ((z - 1.23) / (2.9 - 1.23)) * 35.0
        else:
            zone = "DISTRESS"
            risk_score = max(0.0, z / 1.23 * 40.0)

        return AltmanZScore(
            z_score=round(z, 3), zone=zone,
            x1=x1, x2=x2, x3=x3, x4=x4, x5=x5,
            risk_score=round(risk_score, 2),
        )
```

### Dun & Bradstreet Integration

```python
@dataclass
class DnBSignal:
    duns_number: str
    paydex_score: int           # 0-100; ≥80 = pays within terms
    delinquency_score: int      # 1-5; 1=lowest risk
    failure_score: int          # 1-5; 1=lowest risk
    credit_limit_usd: float
    days_beyond_terms: float    # average DBT
    bankruptcy_indicator: bool
    retrieved_at: datetime

class DnBRiskMapper:
    def to_financial_risk_score(self, signal: DnBSignal) -> float:
        if signal.bankruptcy_indicator:
            return 0.0

        paydex_pts = signal.paydex_score * 0.40         # 0-40
        del_pts    = (5 - signal.delinquency_score) / 4 * 30  # 0-30
        fail_pts   = (5 - signal.failure_score) / 4 * 20      # 0-20
        dbt_pts    = max(0, 10 - signal.days_beyond_terms)     # 0-10

        return round(paydex_pts + del_pts + fail_pts + dbt_pts, 2)
```

### Credit Rating Mapping

| Agencja | Rating | Z-score equiv. | Risk Score |
|---------|--------|----------------|------------|
| Moody's | Aaa–Aa3 | >3.5 | 90–100 |
| Moody's | A1–A3 | 3.0–3.5 | 80–89 |
| Moody's | Baa1–Baa3 | 2.5–3.0 | 65–79 |
| Moody's | Ba1–Ba3 | 1.5–2.5 | 45–64 |
| Moody's | B1–B3 | 0.5–1.5 | 20–44 |
| Moody's | Caa–C | <0.5 | 0–19 |
| S&P | AAA–AA- | >3.5 | 90–100 |
| S&P | A+–A- | 3.0–3.5 | 80–89 |
| S&P | BBB+–BBB- | 2.5–3.0 | 65–79 |
| S&P | BB+–B- | 0.5–2.5 | 20–64 |
| S&P | CCC–D | <0.5 | 0–19 |

### Financial Signal Refresh Policy

| Sygnał | Częstotliwość | Źródło | Trigger alertu |
|--------|---------------|--------|----------------|
| D&B PAYDEX | Monthly | D&B API | PAYDEX < 70 |
| Altman Z | Quarterly (from annual) | ERP / D&B | Zone change to GREY/DISTRESS |
| Credit rating | On change | Moody's/S&P webhook | Downgrade ≥ 2 notches |
| Bankruptcy indicator | Daily | D&B / Court DB | Any change to TRUE |
| Days Beyond Terms | Monthly | D&B API | DBT > 30 days |
| Trade references | Semi-annual | Supplier portal | Rejection / no response |
