# Cost Risk Analysis Engine — Sections 1–3

## 1. Risk Model

### 1.1 Taksonomia ryzyk kosztowych

System klasyfikuje ryzyka w czterech domenach, każda z hierarchią podkategorii.

```
Cost Risk Universe
│
├── SUPPLIER_RISK
│   ├── SR01  supplier_concentration     — udział > 30% zakupów u jednego dostawcy
│   ├── SR02  single_source_dependency   — brak alternatywnego dostawcy dla komponentu
│   ├── SR03  supplier_financial_health  — wskaźnik Altman Z-Score < 1.8
│   ├── SR04  supplier_lead_time_drift   — lead time rośnie > 20% MoM
│   ├── SR05  supplier_quality_risk      — PPM > 500 lub reklamacje > 2% lot
│   └── SR06  geopolitical_exposure      — dostawca w kraju risk_tier ≥ 3
│
├── MARKET_RISK
│   ├── MR01  material_price_volatility  — σ 30-dniowa > 8% (HRC, Al, Cu)
│   ├── MR02  price_forecast_upside      — PFE prognoza +N% w horyzoncie 90 dni
│   ├── MR03  fx_exposure                — zakupy w USD/CNY > 20% total spend
│   ├── MR04  energy_price_spike         — electricity DE > 120 EUR/MWh (alert)
│   ├── MR05  commodity_scarcity         — LME/CME inventory < 6-week demand
│   └── MR06  inflation_passthrough      — PPI metals > CPI + 3pp (marża w dół)
│
├── PRODUCTION_RISK
│   ├── PR01  oee_degradation            — OEE < 75% przez > 3 dni
│   ├── PR02  bottleneck_machine         — jedna maszyna = > 40% throughput
│   ├── PR03  tooling_shortage           — tool_life < 20% przy braku zapasu
│   ├── PR04  scrap_rate_spike           — % scrap > 2× historyczna średnia
│   ├── PR05  labor_shortage             — availability < 85% direct operators
│   └── PR06  process_yield_drop        — yield poniżej specyfikacji o > 5pp
│
└── MATERIAL_RISK
    ├── MT01  bom_coverage_gap           — BOM line bez aktywnego dostawcy
    ├── MT02  stock_depletion            — stock_days < reorder_point × 1.5
    ├── MT03  substitution_unavailable   — brak zamiennika dla krytycznego mat.
    ├── MT04  material_certification_exp — certyfikat wygasa < 30 dni
    ├── MT05  long_lead_time_material    — lead time > 90 dni
    └── MT06  import_restriction         — materiał objęty cłem / sankcją
```

### 1.2 RiskFactor — model danych

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

class RiskDomain(str, Enum):
    SUPPLIER   = "SUPPLIER"
    MARKET     = "MARKET"
    PRODUCTION = "PRODUCTION"
    MATERIAL   = "MATERIAL"

class RiskCategory(str, Enum):
    # SUPPLIER
    SUPPLIER_CONCENTRATION      = "SR01"
    SINGLE_SOURCE               = "SR02"
    SUPPLIER_FINANCIAL          = "SR03"
    LEAD_TIME_DRIFT             = "SR04"
    SUPPLIER_QUALITY            = "SR05"
    GEOPOLITICAL                = "SR06"
    # MARKET
    PRICE_VOLATILITY            = "MR01"
    PRICE_FORECAST_UPSIDE       = "MR02"
    FX_EXPOSURE                 = "MR03"
    ENERGY_PRICE_SPIKE          = "MR04"
    COMMODITY_SCARCITY          = "MR05"
    INFLATION_PASSTHROUGH       = "MR06"
    # PRODUCTION
    OEE_DEGRADATION             = "PR01"
    BOTTLENECK_MACHINE          = "PR02"
    TOOLING_SHORTAGE            = "PR03"
    SCRAP_RATE_SPIKE            = "PR04"
    LABOR_SHORTAGE              = "PR05"
    PROCESS_YIELD_DROP          = "PR06"
    # MATERIAL
    BOM_COVERAGE_GAP            = "MT01"
    STOCK_DEPLETION             = "MT02"
    SUBSTITUTION_UNAVAILABLE    = "MT03"
    MATERIAL_CERTIFICATION_EXP  = "MT04"
    LONG_LEAD_TIME              = "MT05"
    IMPORT_RESTRICTION          = "MT06"

class RiskLevel(str, Enum):
    LOW      = "LOW"       # score 0–25
    MEDIUM   = "MEDIUM"    # score 26–50
    HIGH     = "HIGH"      # score 51–75
    CRITICAL = "CRITICAL"  # score 76–100

class RiskStatus(str, Enum):
    OPEN       = "OPEN"
    MITIGATED  = "MITIGATED"
    ACCEPTED   = "ACCEPTED"
    CLOSED     = "CLOSED"
    ESCALATED  = "ESCALATED"

@dataclass
class RiskFactor:
    risk_id:         UUID
    domain:          RiskDomain
    category:        RiskCategory
    entity_type:     str           # "SUPPLIER" | "MATERIAL" | "MACHINE" | "COMMODITY"
    entity_id:       UUID
    entity_name:     str
    score:           float         # 0–100
    probability:     float         # 0.0–1.0
    impact_eur:      Decimal       # szacowany wpływ finansowy
    level:           RiskLevel
    status:          RiskStatus    = RiskStatus.OPEN
    detected_at:     datetime      = field(default_factory=datetime.utcnow)
    due_date:        date | None   = None
    assigned_to:     str | None    = None
    evidence:        dict          = field(default_factory=dict)
    mitigation_plan: str | None    = None
    residual_score:  float | None  = None

@dataclass
class RiskPortfolio:
    """Zagregowany profil ryzyka dla całej organizacji lub projektu."""
    portfolio_id:   UUID
    calculated_at:  datetime
    total_risks:    int
    critical_count: int
    high_count:     int
    medium_count:   int
    low_count:      int
    total_impact_eur: Decimal
    composite_score:  float        # 0–100 (ważona suma)
    domain_scores:    dict[RiskDomain, float]
    top_risks:        list[RiskFactor]
    var_95_eur:       Decimal      # Value-at-Risk 95% (Monte Carlo)
    expected_loss_eur: Decimal     # P × Impact średnioważona
```

### 1.3 Risk Impact Model — finansowy

```python
from decimal import Decimal

class ImpactCalculator:
    """
    Oblicza finansowy wpływ ryzyka (EUR).
    Używa danych z CBE, BOM Engine i PFE.
    """

    async def calc_supplier_impact(
        self,
        supplier_id:  UUID,
        risk_category: RiskCategory,
        annual_spend_eur: Decimal,
        lead_time_days: int,
        affected_bom_lines: int,
    ) -> Decimal:
        """Wpływ = lost production cost + expediting cost + premium sourcing."""
        match risk_category:
            case RiskCategory.SINGLE_SOURCE:
                # Ryzyko zatrzymania produkcji: dzienna marża × LT
                daily_margin = annual_spend_eur / Decimal("250") * Decimal("0.12")
                return daily_margin * lead_time_days
            case RiskCategory.SUPPLIER_CONCENTRATION:
                # 30% zakupów × premium alternatywnego źródła (+15%)
                return annual_spend_eur * Decimal("0.30") * Decimal("0.15")
            case RiskCategory.SUPPLIER_FINANCIAL:
                # Ryzyko bankructwa: koszt rebuildingu bazy × prawdopodobieństwo
                return annual_spend_eur * Decimal("0.45")  # 45% rocznego spend
            case _:
                return annual_spend_eur * Decimal("0.05")

    async def calc_market_impact(
        self,
        commodity:      str,
        annual_spend_eur: Decimal,
        price_change_pct: Decimal,
        hedge_ratio:    float = 0.0,
    ) -> Decimal:
        """Wpływ ceny surowca na koszty materiałów."""
        unhedged = Decimal(str(1 - hedge_ratio))
        return annual_spend_eur * price_change_pct / Decimal("100") * unhedged

    async def calc_production_impact(
        self,
        machine_id:    UUID,
        downtime_days: Decimal,
        daily_throughput_eur: Decimal,
        oee_current: float,
        oee_target:  float = 0.85,
    ) -> Decimal:
        """Wpływ utraty wydajności na przychód."""
        oee_gap    = max(0, oee_target - oee_current)
        lost_daily = daily_throughput_eur * Decimal(str(oee_gap))
        return lost_daily * downtime_days

    async def monte_carlo_var(
        self,
        risk_factors: list[RiskFactor],
        n_simulations: int = 10_000,
        confidence: float  = 0.95,
    ) -> dict[str, Decimal]:
        """Monte Carlo VaR — symuluje łączny wpływ ryzyk."""
        import numpy as np
        rng = np.random.default_rng(42)
        total_losses = np.zeros(n_simulations)

        for rf in risk_factors:
            # Każde ryzyko losuje: czy zajdzie (Bernoulli p) × jaki impact (LogNormal)
            occurs   = rng.binomial(1, rf.probability, n_simulations)
            mu_log   = float(np.log(float(rf.impact_eur) + 1))
            sigma_log = 0.30   # 30% zmienność impactu
            impacts  = rng.lognormal(mu_log, sigma_log, n_simulations)
            total_losses += occurs * impacts

        var_95   = Decimal(str(np.percentile(total_losses, confidence * 100)))
        cvar_95  = Decimal(str(
            np.mean(total_losses[total_losses >= float(var_95)])
        ))
        exp_loss = Decimal(str(np.mean(total_losses)))

        return {
            "var_95_eur":       var_95,
            "cvar_95_eur":      cvar_95,   # Conditional VaR (Expected Shortfall)
            "expected_loss_eur": exp_loss,
        }
```

### 1.4 Risk Heat Map — macierz Prawdopodobieństwo × Wpływ

```
Impact (EUR)
         │  LOW         MEDIUM       HIGH        CRITICAL
         │  < 10k       10–100k      100k–1M     > 1M
─────────┼──────────────────────────────────────────────────
HIGH     │  MEDIUM      HIGH         CRITICAL    CRITICAL
0.6–1.0  │  (26–50)     (51–75)      (76–90)     (91–100)
─────────┼──────────────────────────────────────────────────
MEDIUM   │  LOW         MEDIUM       HIGH        CRITICAL
0.3–0.6  │  (10–25)     (26–50)      (51–75)     (76–90)
─────────┼──────────────────────────────────────────────────
LOW      │  LOW         LOW          MEDIUM      HIGH
< 0.3    │  (1–10)      (10–25)      (26–50)     (51–75)
─────────┴──────────────────────────────────────────────────
```

---

## 2. Risk Scoring

### 2.1 RiskScorer — silnik oceny

```python
from dataclasses import dataclass
from decimal import Decimal

@dataclass
class ScoringWeights:
    """Wagi dla każdego wymiaru oceny ryzyka."""
    probability_weight: float = 0.35
    impact_weight:      float = 0.35
    velocity_weight:    float = 0.15   # jak szybko ryzyko się materializuje
    detectability_weight: float = 0.15  # jak łatwo wykryć wcześnie

class RiskScorer:
    """
    Oblicza score 0–100 dla każdego RiskFactor.
    Metodologia: FMEA-based (Failure Mode and Effects Analysis) + finansowy VaR.
    """

    WEIGHTS = ScoringWeights()

    # Progi poziomów
    LEVEL_THRESHOLDS = {
        RiskLevel.LOW:      (0,  25),
        RiskLevel.MEDIUM:   (26, 50),
        RiskLevel.HIGH:     (51, 75),
        RiskLevel.CRITICAL: (76, 100),
    }

    def score(
        self,
        probability:    float,   # 0.0–1.0
        impact_eur:     Decimal,
        velocity_days:  int,     # dni do materializacji ryzyka
        detectability:  float,   # 0.0–1.0 (1 = łatwo wykryć wcześnie)
        reference_spend: Decimal = Decimal("1_000_000"),
    ) -> tuple[float, RiskLevel]:
        """Zwraca (score: 0–100, RiskLevel)."""

        # Normalizacja impact do 0–1 (log-skala)
        import math
        impact_norm = min(1.0, math.log10(max(1, float(impact_eur))) / 6)
        # log10(1M) = 6 → impact_norm=1.0 dla 1M EUR

        # Velocity: im szybciej, tym wyższy score
        velocity_norm = max(0.0, 1.0 - velocity_days / 180)
        # 0 dni → 1.0; 180 dni → 0.0

        # Detectability: niska wykrywalność = wyższy score
        detect_score = 1.0 - detectability

        raw = (
            probability  * self.WEIGHTS.probability_weight +
            impact_norm  * self.WEIGHTS.impact_weight +
            velocity_norm * self.WEIGHTS.velocity_weight +
            detect_score  * self.WEIGHTS.detectability_weight
        )
        score = round(raw * 100, 2)

        level = self._level(score)
        return score, level

    def _level(self, score: float) -> RiskLevel:
        for level, (lo, hi) in self.LEVEL_THRESHOLDS.items():
            if lo <= score <= hi:
                return level
        return RiskLevel.CRITICAL

    def composite_portfolio_score(
        self, risk_factors: list["RiskFactor"]
    ) -> float:
        """
        Composite score portfela = ważona średnia (waga = impact_eur).
        Uwzględnia efekt dywersyfikacji: korelacja ryzyk < 1.
        """
        if not risk_factors:
            return 0.0
        total_impact = sum(float(r.impact_eur) for r in risk_factors) or 1.0
        weighted = sum(
            r.score * float(r.impact_eur) for r in risk_factors
        )
        raw_composite = weighted / total_impact

        # Efekt dywersyfikacji: redukcja 15% jeśli > 5 niezależnych ryzyk
        if len(risk_factors) > 5:
            domain_set = {r.domain for r in risk_factors}
            if len(domain_set) >= 3:
                raw_composite *= 0.85

        return round(min(100.0, raw_composite), 2)
```

### 2.2 Scoring per domena

```python
class SupplierRiskScorer:
    """Szczegółowe scoring dla SUPPLIER ryzyk."""

    async def score_concentration(
        self,
        supplier_id:      UUID,
        spend_eur:        Decimal,
        total_spend_eur:  Decimal,
    ) -> tuple[float, Decimal]:
        share = float(spend_eur / total_spend_eur)
        # share > 50% → probability 0.9; 30–50% → 0.6; < 30% → 0.2
        prob = 0.9 if share > 0.50 else (0.6 if share > 0.30 else 0.2)
        impact = spend_eur * Decimal(str(share)) * Decimal("0.15")
        score, _ = RiskScorer().score(prob, impact, velocity_days=60,
                                      detectability=0.8)
        return score, impact

    async def score_financial_health(
        self,
        altman_z: float | None,
        days_overdue: int,
        credit_limit_breached: bool,
    ) -> tuple[float, Decimal]:
        if altman_z is None:
            prob = 0.5   # brak danych → umiarkowane
        elif altman_z < 1.23:
            prob = 0.85  # strefa bankructwa
        elif altman_z < 2.90:
            prob = 0.45  # szara strefa
        else:
            prob = 0.10  # bezpieczna
        if days_overdue > 60:
            prob = min(1.0, prob + 0.15)
        if credit_limit_breached:
            prob = min(1.0, prob + 0.10)
        impact = Decimal("200_000")   # koszt rebuildu bazy
        score, _ = RiskScorer().score(prob, impact, velocity_days=90,
                                      detectability=0.55)
        return score, impact

class MarketRiskScorer:
    """Scoring dla MARKET ryzyk."""

    async def score_price_volatility(
        self,
        commodity:    str,
        vol_30d:      float,   # σ 30-dniowa (np. 0.08 = 8%)
        annual_spend: Decimal,
    ) -> tuple[float, Decimal]:
        # vol > 15% → CRITICAL; 8–15% → HIGH; < 8% → MEDIUM
        prob = min(1.0, vol_30d * 5)   # 20% vol → prob 1.0
        price_change = Decimal(str(vol_30d * 1.65))   # 1-sigma × 1.65 = 95th %
        impact = annual_spend * price_change
        score, _ = RiskScorer().score(prob, impact, velocity_days=14,
                                      detectability=0.90)   # ceny rynkowe ← PFE
        return score, impact

    async def score_fx_exposure(
        self,
        usd_spend_eur:  Decimal,
        cny_spend_eur:  Decimal,
        total_spend:    Decimal,
        eurusd_vol_30d: float,
    ) -> tuple[float, Decimal]:
        foreign_share = float((usd_spend_eur + cny_spend_eur) / total_spend)
        prob = min(0.95, foreign_share * 1.5 * eurusd_vol_30d * 10)
        impact = (usd_spend_eur + cny_spend_eur) * Decimal(str(eurusd_vol_30d * 1.65))
        score, _ = RiskScorer().score(prob, impact, velocity_days=30,
                                      detectability=0.85)
        return score, impact
```

### 2.3 Scoring kalibracja — mapa ryzyk

| Kategoria | Probability | Impact EUR | Velocity | Detectability | Typowy score |
|-----------|:-----------:|:----------:|:--------:|:-------------:|:------------:|
| SR01 Koncentracja | 0.60 | 150k | 60 dni | 0.80 | 52 (HIGH) |
| SR02 Single source | 0.70 | 400k | 30 dni | 0.50 | 74 (HIGH) |
| SR03 Financial health | 0.45 | 200k | 90 dni | 0.55 | 48 (MEDIUM) |
| MR01 Price volatility | 0.75 | 500k | 14 dni | 0.90 | 65 (HIGH) |
| MR03 FX exposure | 0.55 | 250k | 30 dni | 0.85 | 48 (MEDIUM) |
| MR04 Energy spike | 0.40 | 180k | 7 dni | 0.90 | 42 (MEDIUM) |
| PR01 OEE degradation | 0.60 | 120k | 3 dni | 0.70 | 56 (HIGH) |
| PR02 Bottleneck | 0.35 | 600k | 1 dzień | 0.60 | 55 (HIGH) |
| MT01 BOM coverage gap | 0.80 | 300k | 14 dni | 0.40 | 78 (CRITICAL) |
| MT02 Stock depletion | 0.70 | 200k | 7 dni | 0.65 | 67 (HIGH) |

---

## 3. Supplier Risk

### 3.1 SupplierRiskAnalyzer

```python
import asyncio
from decimal import Decimal
from uuid import UUID
from datetime import date, timedelta

class SupplierRiskAnalyzer:
    """
    Analizuje wszystkie kategorie ryzyk dostawców (SR01–SR06).
    Integruje dane z: SOP, BOM Engine, ERP, Dun & Bradstreet.
    """

    def __init__(
        self,
        sop_repo:       "SOPRepository",
        bom_repo:       "BOMRepository",
        supplier_repo:  "SupplierRepository",
        geo_risk_svc:   "GeopoliticalRiskService",
        scorer:         "SupplierRiskScorer",
        impact_calc:    "ImpactCalculator",
        db:             "AsyncpgPool",
    ):
        self._sop      = sop_repo
        self._bom      = bom_repo
        self._sup      = supplier_repo
        self._geo      = geo_risk_svc
        self._scorer   = scorer
        self._impact   = impact_calc
        self._db       = db

    async def analyze_all(self) -> list[RiskFactor]:
        suppliers = await self._sup.get_all_active()
        results   = await asyncio.gather(
            *[self._analyze_supplier(s) for s in suppliers],
            return_exceptions=True,
        )
        return [r for batch in results
                if isinstance(batch, list)
                for r in batch]

    async def _analyze_supplier(
        self, supplier: "SupplierProfile"
    ) -> list[RiskFactor]:
        risks: list[RiskFactor] = []

        total_spend = await self._sop.get_total_annual_spend()
        sup_spend   = await self._sop.get_supplier_annual_spend(supplier.supplier_id)
        bom_lines   = await self._bom.get_bom_lines_for_supplier(supplier.supplier_id)
        lead_times  = await self._sop.get_lead_time_history(supplier.supplier_id, days=90)

        # ── SR01: Koncentracja ────────────────────────────────────────
        if total_spend > 0:
            score, impact = await self._scorer.score_concentration(
                supplier.supplier_id, sup_spend, total_spend)
            if score > 10:
                risks.append(self._make_risk(
                    category=RiskCategory.SUPPLIER_CONCENTRATION,
                    entity=supplier,
                    score=score,
                    probability=float(sup_spend / total_spend),
                    impact=impact,
                    evidence={
                        "spend_share_pct": float(sup_spend / total_spend * 100),
                        "annual_spend_eur": float(sup_spend),
                    }
                ))

        # ── SR02: Single source ───────────────────────────────────────
        single_source_lines = [
            bl for bl in bom_lines
            if await self._bom.count_active_suppliers_for_part(bl.part_id) == 1
        ]
        if single_source_lines:
            critical_parts = len(single_source_lines)
            impact = sup_spend * Decimal(str(critical_parts / max(1, len(bom_lines))))
            risks.append(self._make_risk(
                category=RiskCategory.SINGLE_SOURCE,
                entity=supplier,
                score=74.0,
                probability=0.70,
                impact=impact,
                evidence={
                    "single_source_parts": critical_parts,
                    "part_ids": [str(bl.part_id) for bl in single_source_lines[:5]],
                }
            ))

        # ── SR03: Financial health ────────────────────────────────────
        fin_data = await self._sup.get_financial_indicators(supplier.supplier_id)
        if fin_data:
            score, impact = await self._scorer.score_financial_health(
                altman_z=fin_data.get("altman_z"),
                days_overdue=fin_data.get("days_overdue", 0),
                credit_limit_breached=fin_data.get("credit_limit_breached", False),
            )
            if score > 20:
                risks.append(self._make_risk(
                    category=RiskCategory.SUPPLIER_FINANCIAL,
                    entity=supplier,
                    score=score,
                    probability=0.45,
                    impact=impact,
                    evidence=fin_data,
                ))

        # ── SR04: Lead time drift ─────────────────────────────────────
        if len(lead_times) >= 4:
            recent_avg = sum(lead_times[-4:]) / 4
            baseline   = sum(lead_times[:-4]) / max(1, len(lead_times) - 4)
            drift_pct  = (recent_avg - baseline) / max(1, baseline)
            if drift_pct > 0.15:  # > 15% wzrost LT
                prob   = min(0.90, drift_pct * 2)
                impact = sup_spend * Decimal("0.08") * Decimal(str(drift_pct))
                score, _ = RiskScorer().score(prob, impact, 30, 0.65)
                risks.append(self._make_risk(
                    category=RiskCategory.LEAD_TIME_DRIFT,
                    entity=supplier,
                    score=score,
                    probability=prob,
                    impact=impact,
                    evidence={
                        "drift_pct": round(drift_pct * 100, 1),
                        "recent_avg_days": round(recent_avg, 1),
                        "baseline_avg_days": round(baseline, 1),
                    }
                ))

        # ── SR05: Quality risk ────────────────────────────────────────
        quality = await self._sup.get_quality_metrics(supplier.supplier_id, days=180)
        if quality:
            ppm = quality.get("ppm", 0)
            if ppm > 300:
                prob   = min(0.95, ppm / 1000)
                impact = sup_spend * Decimal("0.03")  # 3% rework/return cost
                score, _ = RiskScorer().score(prob, impact, 14, 0.75)
                risks.append(self._make_risk(
                    category=RiskCategory.SUPPLIER_QUALITY,
                    entity=supplier,
                    score=score,
                    probability=prob,
                    impact=impact,
                    evidence={"ppm": ppm, "complaint_rate_pct": quality.get("complaint_rate_pct")},
                ))

        # ── SR06: Geopolitical ────────────────────────────────────────
        geo = await self._geo.get_country_risk(supplier.country_code)
        if geo and geo["risk_tier"] >= 3:
            prob   = {3: 0.25, 4: 0.50, 5: 0.75}.get(geo["risk_tier"], 0.25)
            impact = sup_spend * Decimal("0.40")
            score, _ = RiskScorer().score(prob, impact, 180, 0.30)
            risks.append(self._make_risk(
                category=RiskCategory.GEOPOLITICAL,
                entity=supplier,
                score=score,
                probability=prob,
                impact=impact,
                evidence={"country": supplier.country_code, "risk_tier": geo["risk_tier"],
                          "risk_factors": geo.get("factors", [])},
            ))

        return risks

    def _make_risk(
        self,
        category:    RiskCategory,
        entity:      "SupplierProfile",
        score:       float,
        probability: float,
        impact:      Decimal,
        evidence:    dict,
    ) -> RiskFactor:
        from uuid import uuid4
        _, level = RiskScorer()._level(score), RiskScorer()._level(score)
        return RiskFactor(
            risk_id=uuid4(),
            domain=RiskDomain.SUPPLIER,
            category=category,
            entity_type="SUPPLIER",
            entity_id=entity.supplier_id,
            entity_name=entity.company_name,
            score=score,
            probability=probability,
            impact_eur=impact,
            level=RiskScorer()._level(score),
            evidence=evidence,
        )
```

### 3.2 GeopoliticalRiskService

```python
@dataclass
class CountryRiskProfile:
    country_code:  str
    risk_tier:     int      # 1–5 (1=lowest, 5=highest)
    factors:       list[str]  # np. ["SANCTIONS", "POLITICAL_INSTABILITY"]
    conflict_risk: bool
    export_restrictions: list[str]  # objęte towary

# Wbudowana baza (aktualizowana kwartalnie)
COUNTRY_RISK_DATABASE: dict[str, CountryRiskProfile] = {
    "DE": CountryRiskProfile("DE", 1, [], False, []),
    "PL": CountryRiskProfile("PL", 1, [], False, []),
    "CZ": CountryRiskProfile("CZ", 1, [], False, []),
    "CN": CountryRiskProfile("CN", 3, ["TRADE_WAR", "EXPORT_CONTROLS"], False,
                             ["DUAL_USE_TECH", "SEMICONDUCTORS"]),
    "TW": CountryRiskProfile("TW", 4, ["GEOPOLITICAL_TENSION"], True, []),
    "RU": CountryRiskProfile("RU", 5, ["SANCTIONS", "CONFLICT"], True,
                             ["ALL_GOODS"]),
    "BY": CountryRiskProfile("BY", 5, ["SANCTIONS"], True, ["ALL_GOODS"]),
    "IN": CountryRiskProfile("IN", 2, [], False, []),
    "MX": CountryRiskProfile("MX", 2, ["CARTEL_RISK"], False, []),
    "TR": CountryRiskProfile("TR", 3, ["POLITICAL_INSTABILITY", "INFLATION"], False, []),
    "UA": CountryRiskProfile("UA", 4, ["CONFLICT_ZONE"], True, []),
    "VN": CountryRiskProfile("VN", 2, [], False, []),
}

class GeopoliticalRiskService:
    async def get_country_risk(self, country_code: str) -> dict | None:
        profile = COUNTRY_RISK_DATABASE.get(country_code)
        if not profile:
            return {"risk_tier": 2, "factors": ["UNKNOWN_COUNTRY"]}
        return {
            "risk_tier":           profile.risk_tier,
            "factors":             profile.factors,
            "conflict_risk":       profile.conflict_risk,
            "export_restrictions": profile.export_restrictions,
        }
```

### 3.3 Supplier Risk Heat Map — przykładowy output

```
Supplier Risk Score — Top 10
═══════════════════════════════════════════════════════════════
Supplier              | Score | Level    | Main Risk        | Impact EUR
─────────────────────────────────────────────────────────────────────────
Metalworks GmbH       |  82   | CRITICAL | Single Source    | 420,000
SteelCo Shanghai      |  79   | CRITICAL | Geopolitical     | 1,200,000
Fasteners Ltd.        |  74   | HIGH     | Concentration    | 180,000
Precision Parts PL    |  68   | HIGH     | Lead Time Drift  |  95,000
Cast Components Inc.  |  63   | HIGH     | Financial Health | 250,000
Coating Specialists   |  55   | HIGH     | Quality Risk     |  72,000
Bearings Direct       |  48   | MEDIUM   | Concentration    |  65,000
Springs & Stamps      |  42   | MEDIUM   | Lead Time Drift  |  38,000
Seals & Gaskets       |  31   | MEDIUM   | Quality Risk     |  28,000
Hardware Plus         |  18   | LOW      | —               |  12,000
```
