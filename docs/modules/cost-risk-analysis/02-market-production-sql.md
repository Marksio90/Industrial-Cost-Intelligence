# Cost Risk Analysis Engine — Sections 4–6

## 4. Market Risk

### 4.1 MarketRiskAnalyzer

```python
import asyncio
from decimal import Decimal
from datetime import date, timedelta

class MarketRiskAnalyzer:
    """
    Analizuje ryzyka rynkowe (MR01–MR06).
    Integruje dane z PFE (prognozy), CBE (spend), ECB (FX).
    """

    def __init__(
        self,
        pfe_client:    "PFEClient",      # Price Forecasting Engine
        cbe_repo:      "CBERepository",  # Cost Breakdown Engine
        macro_repo:    "MacroRepository",
        fx_svc:        "FXRateService",
        scorer:        "MarketRiskScorer",
        impact_calc:   "ImpactCalculator",
        db:            "AsyncpgPool",
    ):
        self._pfe    = pfe_client
        self._cbe    = cbe_repo
        self._macro  = macro_repo
        self._fx     = fx_svc
        self._scorer = scorer
        self._impact = impact_calc
        self._db     = db

    async def analyze_all(self) -> list[RiskFactor]:
        commodities = ["STEEL_HRC", "STEEL_CRC", "ALUMINUM_LME",
                       "COPPER_LME", "ELECTRICITY_DE", "GAS_TTF"]
        results = await asyncio.gather(
            *[self._analyze_commodity(c) for c in commodities],
            self._analyze_fx_exposure(),
            self._analyze_inflation(),
            return_exceptions=True,
        )
        risks: list[RiskFactor] = []
        for r in results:
            if isinstance(r, list):
                risks.extend(r)
            elif isinstance(r, RiskFactor):
                risks.append(r)
        return risks

    async def _analyze_commodity(self, commodity: str) -> list[RiskFactor]:
        risks: list[RiskFactor] = []

        # Dane historyczne z PFE
        history = await self._pfe.get_price_history(commodity, days=60)
        if len(history) < 20:
            return []

        import numpy as np
        prices    = np.array([float(h["price_eur"]) for h in history])
        returns   = np.diff(np.log(prices + 1e-9))
        vol_30d   = float(np.std(returns[-30:]) * np.sqrt(252))   # annualized
        vol_7d    = float(np.std(returns[-7:])  * np.sqrt(252))

        # Prognoza z PFE
        forecast  = await self._pfe.get_latest_forecast(commodity, horizon=90)
        current_price = Decimal(str(prices[-1]))
        annual_spend  = await self._cbe.get_commodity_annual_spend(commodity)

        # ── MR01: Price Volatility ─────────────────────────────────────
        if vol_30d > 0.05:  # > 5% annualized vol
            score, impact = await self._scorer.score_price_volatility(
                commodity, vol_30d, annual_spend)
            risks.append(RiskFactor(
                risk_id=uuid4(),
                domain=RiskDomain.MARKET,
                category=RiskCategory.PRICE_VOLATILITY,
                entity_type="COMMODITY",
                entity_id=uuid4(),
                entity_name=commodity,
                score=score,
                probability=min(0.90, vol_30d * 4),
                impact_eur=impact,
                level=RiskScorer()._level(score),
                evidence={
                    "vol_30d_annualized": round(vol_30d * 100, 2),
                    "vol_7d_annualized":  round(vol_7d  * 100, 2),
                    "current_price_eur":  float(current_price),
                    "annual_spend_eur":   float(annual_spend),
                },
            ))

        # ── MR02: Price Forecast Upside ────────────────────────────────
        if forecast:
            p30  = forecast.get("price_30d")
            p90  = forecast.get("price_90d")
            if p90 and current_price > 0:
                change_pct = Decimal(str((p90 - float(current_price))
                                          / float(current_price) * 100))
                if change_pct > Decimal("5"):  # prognozy > 5% wzrostu
                    impact = await self._impact.calc_market_impact(
                        commodity, annual_spend, change_pct)
                    score, _ = RiskScorer().score(
                        probability=0.60,
                        impact_eur=impact,
                        velocity_days=90,
                        detectability=0.90,
                    )
                    risks.append(RiskFactor(
                        risk_id=uuid4(),
                        domain=RiskDomain.MARKET,
                        category=RiskCategory.PRICE_FORECAST_UPSIDE,
                        entity_type="COMMODITY",
                        entity_id=uuid4(),
                        entity_name=commodity,
                        score=score,
                        probability=0.60,
                        impact_eur=impact,
                        level=RiskScorer()._level(score),
                        evidence={
                            "forecast_change_pct": float(change_pct),
                            "current_price_eur":   float(current_price),
                            "forecast_90d_eur":    p90,
                            "model": forecast.get("model_type", "ENSEMBLE"),
                        },
                    ))

        # ── MR04: Energy Price Spike ───────────────────────────────────
        if commodity == "ELECTRICITY_DE":
            spike_threshold = Decimal("120")  # EUR/MWh
            if current_price > spike_threshold:
                annual_energy_cost = await self._cbe.get_energy_annual_cost("DE")
                impact = annual_energy_cost * Decimal("0.15")
                score, _ = RiskScorer().score(0.40, impact, 7, 0.95)
                risks.append(RiskFactor(
                    risk_id=uuid4(),
                    domain=RiskDomain.MARKET,
                    category=RiskCategory.ENERGY_PRICE_SPIKE,
                    entity_type="COMMODITY",
                    entity_id=uuid4(),
                    entity_name="ELECTRICITY_DE",
                    score=score,
                    probability=0.40,
                    impact_eur=impact,
                    level=RiskScorer()._level(score),
                    evidence={
                        "current_price_eur_mwh": float(current_price),
                        "threshold_eur_mwh":     float(spike_threshold),
                        "annual_energy_cost_eur": float(annual_energy_cost),
                    },
                ))

        return risks

    async def _analyze_fx_exposure(self) -> RiskFactor | None:
        spend_breakdown = await self._cbe.get_spend_by_currency()
        total_spend     = sum(spend_breakdown.values())
        if total_spend == 0:
            return None

        usd_spend = spend_breakdown.get("USD", Decimal("0"))
        cny_spend = spend_breakdown.get("CNY", Decimal("0"))
        foreign   = usd_spend + cny_spend

        if float(foreign / total_spend) < 0.10:
            return None

        eurusd_vol = await self._macro.get_fx_vol("EURUSD", days=30)
        score, impact = await self._scorer.score_fx_exposure(
            usd_spend, cny_spend, total_spend, eurusd_vol)

        return RiskFactor(
            risk_id=uuid4(),
            domain=RiskDomain.MARKET,
            category=RiskCategory.FX_EXPOSURE,
            entity_type="COMMODITY",
            entity_id=uuid4(),
            entity_name="FX_EXPOSURE",
            score=score,
            probability=min(0.85, float(foreign / total_spend) * 2),
            impact_eur=impact,
            level=RiskScorer()._level(score),
            evidence={
                "usd_spend_eur": float(usd_spend),
                "cny_spend_eur": float(cny_spend),
                "foreign_share_pct": float(foreign / total_spend * 100),
                "eurusd_vol_30d": eurusd_vol,
            },
        )

    async def _analyze_inflation(self) -> RiskFactor | None:
        macro = await self._macro.get_latest_snapshot()
        if not macro:
            return None

        ppi   = macro.ppi_metals_de or 0.0
        hicp  = macro.hicp_eu or 0.0
        delta = ppi - hicp   # PPI - CPI passthrough gap

        if delta < 3.0:   # < 3pp różnicy → brak ryzyka
            return None

        total_spend   = await self._cbe.get_total_material_spend()
        impact_eur    = total_spend * Decimal(str(delta / 100))
        score, _      = RiskScorer().score(0.65, impact_eur, 90, 0.60)

        return RiskFactor(
            risk_id=uuid4(),
            domain=RiskDomain.MARKET,
            category=RiskCategory.INFLATION_PASSTHROUGH,
            entity_type="COMMODITY",
            entity_id=uuid4(),
            entity_name="INFLATION_PASSTHROUGH",
            score=score,
            probability=0.65,
            impact_eur=impact_eur,
            level=RiskScorer()._level(score),
            evidence={
                "ppi_metals_de": ppi,
                "hicp_eu":       hicp,
                "delta_pp":      delta,
            },
        )
```

### 4.2 Commodity Scarcity Monitor

```python
class CommodityScarcityMonitor:
    """
    Monitoruje poziomy zapasów LME/CME dla kluczowych metali.
    Tygodniowy import z LME Warehouse Report.
    """

    SCARCITY_THRESHOLD_WEEKS = 6.0

    async def check_scarcity(
        self,
        commodity: str,
        lme_inventory_t: float,
        global_demand_weekly_t: float,
    ) -> RiskFactor | None:
        weeks_of_supply = lme_inventory_t / max(global_demand_weekly_t, 1)
        if weeks_of_supply >= self.SCARCITY_THRESHOLD_WEEKS:
            return None

        scarcity_ratio = weeks_of_supply / self.SCARCITY_THRESHOLD_WEEKS
        prob   = min(0.90, 1 - scarcity_ratio)
        annual_spend = await self._cbe.get_commodity_annual_spend(commodity)
        impact = annual_spend * Decimal("0.20") * Decimal(str(prob))
        score, _ = RiskScorer().score(prob, impact, velocity_days=21,
                                      detectability=0.85)
        return RiskFactor(
            risk_id=uuid4(),
            domain=RiskDomain.MARKET,
            category=RiskCategory.COMMODITY_SCARCITY,
            entity_type="COMMODITY",
            entity_id=uuid4(),
            entity_name=commodity,
            score=score,
            probability=prob,
            impact_eur=impact,
            level=RiskScorer()._level(score),
            evidence={
                "lme_inventory_t":        lme_inventory_t,
                "weekly_demand_t":        global_demand_weekly_t,
                "weeks_of_supply":        round(weeks_of_supply, 1),
                "scarcity_threshold_wk":  self.SCARCITY_THRESHOLD_WEEKS,
            },
        )
```

---

## 5. Production Risk

### 5.1 ProductionRiskAnalyzer

```python
class ProductionRiskAnalyzer:
    """
    Analizuje ryzyka produkcyjne (PR01–PR06).
    Integruje dane z MES (Manufacturing Execution System), CBE i BOM Engine.
    """

    def __init__(
        self,
        mes_repo:    "MESRepository",    # OEE, scrap, throughput
        cbe_repo:    "CBERepository",
        bom_repo:    "BOMRepository",
        tooling_repo: "ToolingRepository",
        hr_repo:     "HRRepository",
        scorer:      RiskScorer,
        impact_calc: ImpactCalculator,
    ):
        self._mes     = mes_repo
        self._cbe     = cbe_repo
        self._bom     = bom_repo
        self._tooling = tooling_repo
        self._hr      = hr_repo
        self._scorer  = scorer
        self._impact  = impact_calc

    async def analyze_all(self) -> list[RiskFactor]:
        machines  = await self._mes.get_all_machines()
        locations = ["DE", "PL"]
        results   = await asyncio.gather(
            *[self._analyze_machine(m) for m in machines],
            *[self._analyze_labor(loc) for loc in locations],
            self._analyze_process_yield(),
            return_exceptions=True,
        )
        risks: list[RiskFactor] = []
        for r in results:
            if isinstance(r, list):
                risks.extend(r)
            elif isinstance(r, RiskFactor):
                risks.append(r)
        return risks

    async def _analyze_machine(self, machine: dict) -> list[RiskFactor]:
        risks: list[RiskFactor] = []
        machine_id = machine["machine_id"]

        # Pobierz metryki z ostatnich 14 dni
        oee_history = await self._mes.get_oee_history(machine_id, days=14)
        if not oee_history:
            return []

        import numpy as np
        oee_values   = [float(o["oee"]) for o in oee_history]
        oee_current  = oee_values[-1]
        oee_avg      = float(np.mean(oee_values[-3:]))   # avg 3 dni
        oee_baseline = float(np.mean(oee_values[:-3]))

        daily_throughput = await self._cbe.get_machine_daily_throughput_eur(machine_id)

        # ── PR01: OEE Degradation ─────────────────────────────────────
        if oee_avg < 0.75:
            impact = await self._impact.calc_production_impact(
                machine_id=machine_id,
                downtime_days=Decimal("30"),
                daily_throughput_eur=daily_throughput,
                oee_current=oee_avg,
            )
            score, _ = self._scorer.score(
                probability=min(0.90, (0.85 - oee_avg) * 5),
                impact_eur=impact,
                velocity_days=3,
                detectability=0.70,
            )
            risks.append(RiskFactor(
                risk_id=uuid4(),
                domain=RiskDomain.PRODUCTION,
                category=RiskCategory.OEE_DEGRADATION,
                entity_type="MACHINE",
                entity_id=machine_id,
                entity_name=machine["machine_code"],
                score=score,
                probability=min(0.90, (0.85 - oee_avg) * 5),
                impact_eur=impact,
                level=RiskScorer()._level(score),
                evidence={
                    "oee_current":  round(oee_current, 3),
                    "oee_3d_avg":   round(oee_avg, 3),
                    "oee_baseline": round(oee_baseline, 3),
                    "oee_target":   0.85,
                },
            ))

        # ── PR02: Bottleneck Machine ──────────────────────────────────
        throughput_share = await self._mes.get_throughput_share(machine_id)
        if throughput_share > 0.35:
            impact = daily_throughput * Decimal("30") * Decimal(str(throughput_share))
            score, _ = self._scorer.score(0.35, impact, 1, 0.60)
            risks.append(RiskFactor(
                risk_id=uuid4(),
                domain=RiskDomain.PRODUCTION,
                category=RiskCategory.BOTTLENECK_MACHINE,
                entity_type="MACHINE",
                entity_id=machine_id,
                entity_name=machine["machine_code"],
                score=score,
                probability=0.35,
                impact_eur=impact,
                level=RiskScorer()._level(score),
                evidence={
                    "throughput_share_pct": round(throughput_share * 100, 1),
                    "daily_throughput_eur": float(daily_throughput),
                    "alternative_machines": await self._mes.count_alternatives(
                        machine["machine_type"]),
                },
            ))

        # ── PR03: Tooling Shortage ─────────────────────────────────────
        tooling = await self._tooling.get_machine_tooling(machine_id)
        for tool in tooling:
            life_remaining_pct = tool["life_remaining_pct"]
            stock_available    = tool["stock_count"]
            if life_remaining_pct < 0.20 and stock_available == 0:
                impact = daily_throughput * Decimal("7")  # 7 dni przestoju
                score, _ = self._scorer.score(0.70, impact, 3, 0.65)
                risks.append(RiskFactor(
                    risk_id=uuid4(),
                    domain=RiskDomain.PRODUCTION,
                    category=RiskCategory.TOOLING_SHORTAGE,
                    entity_type="MACHINE",
                    entity_id=machine_id,
                    entity_name=machine["machine_code"],
                    score=score,
                    probability=0.70,
                    impact_eur=impact,
                    level=RiskScorer()._level(score),
                    evidence={
                        "tool_id":            str(tool["tool_id"]),
                        "tool_code":          tool["tool_code"],
                        "life_remaining_pct": life_remaining_pct,
                        "stock_count":        stock_available,
                        "lead_time_days":     tool["lead_time_days"],
                    },
                ))

        # ── PR04: Scrap Rate Spike ─────────────────────────────────────
        scrap = await self._mes.get_scrap_history(machine_id, days=30)
        if len(scrap) >= 7:
            recent_scrap = sum(scrap[-7:]) / 7
            baseline_scrap = sum(scrap[:-7]) / max(1, len(scrap) - 7)
            if baseline_scrap > 0 and recent_scrap > baseline_scrap * 2:
                impact = daily_throughput * Decimal(str(recent_scrap - baseline_scrap)) * Decimal("30")
                score, _ = self._scorer.score(
                    probability=0.75,
                    impact_eur=impact,
                    velocity_days=2,
                    detectability=0.80,
                )
                risks.append(RiskFactor(
                    risk_id=uuid4(),
                    domain=RiskDomain.PRODUCTION,
                    category=RiskCategory.SCRAP_RATE_SPIKE,
                    entity_type="MACHINE",
                    entity_id=machine_id,
                    entity_name=machine["machine_code"],
                    score=score,
                    probability=0.75,
                    impact_eur=impact,
                    level=RiskScorer()._level(score),
                    evidence={
                        "recent_scrap_pct":   round(recent_scrap * 100, 2),
                        "baseline_scrap_pct": round(baseline_scrap * 100, 2),
                        "multiplier":         round(recent_scrap / max(baseline_scrap, 1e-9), 1),
                    },
                ))

        return risks

    async def _analyze_labor(self, location_code: str) -> RiskFactor | None:
        hr = await self._hr.get_availability(location_code)
        if not hr:
            return None
        availability = hr["direct_operator_availability"]
        if availability >= 0.85:
            return None
        daily_throughput = await self._cbe.get_location_daily_throughput_eur(location_code)
        impact = daily_throughput * Decimal(str(0.85 - availability)) * Decimal("30")
        score, _ = self._scorer.score(0.65, impact, 7, 0.55)
        return RiskFactor(
            risk_id=uuid4(),
            domain=RiskDomain.PRODUCTION,
            category=RiskCategory.LABOR_SHORTAGE,
            entity_type="MACHINE",
            entity_id=uuid4(),
            entity_name=f"LABOR_{location_code}",
            score=score,
            probability=0.65,
            impact_eur=impact,
            level=RiskScorer()._level(score),
            evidence={
                "location":       location_code,
                "availability":   round(availability, 3),
                "target":         0.85,
                "headcount_gap":  hr.get("headcount_gap"),
                "sick_leave_pct": hr.get("sick_leave_pct"),
            },
        )

    async def _analyze_process_yield(self) -> list[RiskFactor]:
        risks: list[RiskFactor] = []
        processes = await self._mes.get_all_processes()
        for proc in processes:
            yield_current = proc["yield_pct"]
            yield_target  = proc["yield_target_pct"]
            if yield_current < yield_target - 5:
                gap    = yield_target - yield_current
                impact = Decimal(str(proc["annual_revenue_eur"])) * Decimal(str(gap / 100))
                score, _ = self._scorer.score(0.60, impact, 5, 0.70)
                risks.append(RiskFactor(
                    risk_id=uuid4(),
                    domain=RiskDomain.PRODUCTION,
                    category=RiskCategory.PROCESS_YIELD_DROP,
                    entity_type="MACHINE",
                    entity_id=proc["process_id"],
                    entity_name=proc["process_name"],
                    score=score,
                    probability=0.60,
                    impact_eur=impact,
                    level=RiskScorer()._level(score),
                    evidence={
                        "yield_current_pct": yield_current,
                        "yield_target_pct":  yield_target,
                        "gap_pp":            gap,
                    },
                ))
        return risks
```

### 5.2 MaterialRiskAnalyzer

```python
class MaterialRiskAnalyzer:
    """
    Analizuje ryzyka materiałowe (MT01–MT06).
    Integruje dane z BOM Engine, SOP, ERP inventory.
    """

    def __init__(
        self,
        bom_repo:     "BOMRepository",
        sop_repo:     "SOPRepository",
        inventory_repo: "InventoryRepository",
        cert_repo:    "CertificationRepository",
        scorer:       RiskScorer,
        impact_calc:  ImpactCalculator,
    ):
        self._bom   = bom_repo
        self._sop   = sop_repo
        self._inv   = inventory_repo
        self._cert  = cert_repo
        self._scorer = scorer
        self._impact = impact_calc

    async def analyze_all(self) -> list[RiskFactor]:
        materials = await self._bom.get_all_unique_materials()
        results   = await asyncio.gather(
            *[self._analyze_material(m) for m in materials],
            return_exceptions=True,
        )
        risks: list[RiskFactor] = []
        for r in results:
            if isinstance(r, list):
                risks.extend(r)
        return risks

    async def _analyze_material(self, material: dict) -> list[RiskFactor]:
        risks:   list[RiskFactor] = []
        mat_id   = material["material_id"]
        mat_name = material["designation"]

        # ── MT01: BOM Coverage Gap ─────────────────────────────────────
        active_suppliers = await self._sop.count_active_suppliers_for_material(mat_id)
        if active_suppliers == 0:
            annual_usage_eur = await self._bom.get_annual_material_spend(mat_id)
            score, _ = self._scorer.score(0.80, annual_usage_eur * Decimal("1.5"),
                                           14, 0.40)
            risks.append(self._make(
                RiskCategory.BOM_COVERAGE_GAP, mat_id, mat_name,
                score, 0.80, annual_usage_eur * Decimal("1.5"),
                {"active_suppliers": 0},
            ))

        # ── MT02: Stock Depletion ──────────────────────────────────────
        inv = await self._inv.get_stock_status(mat_id)
        if inv:
            stock_days    = inv["stock_days"]
            reorder_point = inv["reorder_point_days"]
            if stock_days < reorder_point * 1.5:
                daily_cost = await self._bom.get_daily_material_cost(mat_id)
                impact     = daily_cost * Decimal(str(
                    max(0, reorder_point - stock_days)))
                score, _ = self._scorer.score(
                    min(0.90, (reorder_point * 1.5 - stock_days) / reorder_point),
                    impact, 7, 0.65)
                risks.append(self._make(
                    RiskCategory.STOCK_DEPLETION, mat_id, mat_name,
                    score,
                    min(0.90, (reorder_point * 1.5 - stock_days) / reorder_point),
                    impact,
                    {"stock_days": stock_days, "reorder_point": reorder_point},
                ))

        # ── MT04: Certification Expiry ─────────────────────────────────
        certs = await self._cert.get_expiring_certs(mat_id, days_ahead=60)
        for cert in certs:
            days_left = (cert["expiry_date"] - date.today()).days
            annual_usage = await self._bom.get_annual_material_spend(mat_id)
            prob   = 1.0 - days_left / 60
            impact = annual_usage * Decimal("0.10")
            score, _ = self._scorer.score(prob, impact, days_left, 0.75)
            risks.append(self._make(
                RiskCategory.MATERIAL_CERTIFICATION_EXP,
                mat_id, mat_name, score, prob, impact,
                {"cert_id": str(cert["cert_id"]),
                 "cert_type": cert["cert_type"],
                 "expiry_date": cert["expiry_date"].isoformat(),
                 "days_left": days_left},
            ))

        # ── MT05: Long Lead Time ───────────────────────────────────────
        lead_time = material.get("avg_lead_time_days", 0)
        if lead_time > 90:
            annual_spend = await self._bom.get_annual_material_spend(mat_id)
            impact = annual_spend * Decimal("0.05")
            score, _ = self._scorer.score(0.55, impact, lead_time, 0.50)
            risks.append(self._make(
                RiskCategory.LONG_LEAD_TIME, mat_id, mat_name,
                score, 0.55, impact,
                {"lead_time_days": lead_time},
            ))

        return risks

    def _make(
        self, cat: RiskCategory, eid: UUID, ename: str,
        score: float, prob: float, impact: Decimal, evidence: dict,
    ) -> RiskFactor:
        return RiskFactor(
            risk_id=uuid4(), domain=RiskDomain.MATERIAL,
            category=cat, entity_type="MATERIAL",
            entity_id=eid, entity_name=ename,
            score=score, probability=prob, impact_eur=impact,
            level=RiskScorer()._level(score), evidence=evidence,
        )
```

---

## 6. SQL Schema

### 6.1 Schemat PostgreSQL 16 — `crae`

```sql
-- ================================================================
--  COST RISK ANALYSIS ENGINE — schemat `crae`
--  PostgreSQL 16, extensions: pgcrypto, pg_trgm, ltree
-- ================================================================

CREATE SCHEMA IF NOT EXISTS crae;

-- ─────────────────────────────────────────────────────────────────
-- ENUMy
-- ─────────────────────────────────────────────────────────────────

CREATE TYPE crae.risk_domain AS ENUM (
    'SUPPLIER', 'MARKET', 'PRODUCTION', 'MATERIAL'
);

CREATE TYPE crae.risk_category AS ENUM (
    'SR01','SR02','SR03','SR04','SR05','SR06',
    'MR01','MR02','MR03','MR04','MR05','MR06',
    'PR01','PR02','PR03','PR04','PR05','PR06',
    'MT01','MT02','MT03','MT04','MT05','MT06'
);

CREATE TYPE crae.risk_level AS ENUM (
    'LOW', 'MEDIUM', 'HIGH', 'CRITICAL'
);

CREATE TYPE crae.risk_status AS ENUM (
    'OPEN', 'MITIGATED', 'ACCEPTED', 'CLOSED', 'ESCALATED'
);

CREATE TYPE crae.entity_type AS ENUM (
    'SUPPLIER', 'MATERIAL', 'MACHINE', 'COMMODITY', 'PROCESS', 'LOCATION'
);

CREATE TYPE crae.alert_channel AS ENUM (
    'EMAIL', 'SLACK', 'WEBHOOK', 'KAFKA', 'DASHBOARD'
);

-- ─────────────────────────────────────────────────────────────────
-- Tabela: czynniki ryzyka
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.risk_factors (
    risk_id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    domain           crae.risk_domain   NOT NULL,
    category         crae.risk_category NOT NULL,
    entity_type      crae.entity_type   NOT NULL,
    entity_id        UUID          NOT NULL,
    entity_name      TEXT          NOT NULL,
    score            NUMERIC(6,2)  NOT NULL CHECK (score BETWEEN 0 AND 100),
    probability      NUMERIC(4,3)  NOT NULL CHECK (probability BETWEEN 0 AND 1),
    impact_eur       NUMERIC(16,2) NOT NULL,
    level            crae.risk_level
        GENERATED ALWAYS AS (
            CASE
                WHEN score <= 25 THEN 'LOW'::crae.risk_level
                WHEN score <= 50 THEN 'MEDIUM'::crae.risk_level
                WHEN score <= 75 THEN 'HIGH'::crae.risk_level
                ELSE 'CRITICAL'::crae.risk_level
            END
        ) STORED,
    status           crae.risk_status   NOT NULL DEFAULT 'OPEN',
    detected_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),
    due_date         DATE,
    assigned_to      TEXT,
    mitigation_plan  TEXT,
    residual_score   NUMERIC(6,2)  CHECK (residual_score BETWEEN 0 AND 100),
    evidence         JSONB         NOT NULL DEFAULT '{}',
    analysis_run_id  UUID,         -- powiązanie z przebiegiem analizy
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX crae_rf_domain_idx      ON crae.risk_factors (domain, level, score DESC);
CREATE INDEX crae_rf_entity_idx      ON crae.risk_factors (entity_type, entity_id);
CREATE INDEX crae_rf_category_idx    ON crae.risk_factors (category);
CREATE INDEX crae_rf_status_open_idx ON crae.risk_factors (status, detected_at DESC)
    WHERE status IN ('OPEN', 'ESCALATED');
CREATE INDEX crae_rf_impact_idx      ON crae.risk_factors (impact_eur DESC);
CREATE INDEX crae_rf_evidence_gin    ON crae.risk_factors USING GIN (evidence);

-- ─────────────────────────────────────────────────────────────────
-- Tabela: przebieg analizy (run log)
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.analysis_runs (
    run_id           UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    run_date         DATE          NOT NULL DEFAULT CURRENT_DATE,
    triggered_by     TEXT          NOT NULL DEFAULT 'schedule',
    status           TEXT          NOT NULL DEFAULT 'RUNNING',
    domains_analyzed JSONB         NOT NULL DEFAULT '["SUPPLIER","MARKET","PRODUCTION","MATERIAL"]',
    risk_count_new   INT           NOT NULL DEFAULT 0,
    risk_count_closed INT          NOT NULL DEFAULT 0,
    risk_count_escalated INT       NOT NULL DEFAULT 0,
    portfolio_score  NUMERIC(6,2),
    var_95_eur       NUMERIC(16,2),
    expected_loss_eur NUMERIC(16,2),
    duration_s       NUMERIC(8,3),
    error_message    TEXT,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────
-- Tabela: portfolio ryzyk (snapshoty dzienne)
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.risk_portfolios (
    portfolio_id     UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id           UUID          NOT NULL REFERENCES crae.analysis_runs,
    portfolio_date   DATE          NOT NULL DEFAULT CURRENT_DATE UNIQUE,
    total_risks      INT           NOT NULL,
    critical_count   INT           NOT NULL DEFAULT 0,
    high_count       INT           NOT NULL DEFAULT 0,
    medium_count     INT           NOT NULL DEFAULT 0,
    low_count        INT           NOT NULL DEFAULT 0,
    total_impact_eur NUMERIC(16,2) NOT NULL,
    composite_score  NUMERIC(6,2)  NOT NULL,
    supplier_score   NUMERIC(6,2),
    market_score     NUMERIC(6,2),
    production_score NUMERIC(6,2),
    material_score   NUMERIC(6,2),
    var_95_eur       NUMERIC(16,2),
    cvar_95_eur      NUMERIC(16,2),
    expected_loss_eur NUMERIC(16,2),
    top_risks        JSONB         NOT NULL DEFAULT '[]',  -- top 10 risk_ids
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX crae_portfolio_date_idx ON crae.risk_portfolios (portfolio_date DESC);

-- ─────────────────────────────────────────────────────────────────
-- Tabela: historia mitygacji
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.mitigation_actions (
    action_id        UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_id          UUID          NOT NULL REFERENCES crae.risk_factors ON DELETE CASCADE,
    action_type      TEXT          NOT NULL,   -- "DUAL_SOURCE", "HEDGE", "SAFETY_STOCK", etc.
    description      TEXT          NOT NULL,
    assigned_to      TEXT          NOT NULL,
    due_date         DATE          NOT NULL,
    status           TEXT          NOT NULL DEFAULT 'PLANNED',
    cost_eur         NUMERIC(14,2),
    expected_score_reduction NUMERIC(5,2),
    completed_at     TIMESTAMPTZ,
    actual_score_after NUMERIC(6,2),
    notes            TEXT,
    created_by       TEXT          NOT NULL,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

-- ─────────────────────────────────────────────────────────────────
-- Tabela: alerty
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.risk_alerts (
    alert_id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    risk_id          UUID          REFERENCES crae.risk_factors,
    alert_type       TEXT          NOT NULL,   -- "NEW_CRITICAL", "SCORE_SPIKE", "PORTFOLIO"
    severity         TEXT          NOT NULL,   -- "INFO", "WARNING", "CRITICAL"
    title            TEXT          NOT NULL,
    message          TEXT          NOT NULL,
    channels         crae.alert_channel[]  NOT NULL DEFAULT '{}',
    recipients       JSONB         NOT NULL DEFAULT '[]',
    sent_at          TIMESTAMPTZ,
    acknowledged_at  TIMESTAMPTZ,
    acknowledged_by  TEXT,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX crae_alerts_risk_idx    ON crae.risk_alerts (risk_id);
CREATE INDEX crae_alerts_unsent_idx  ON crae.risk_alerts (created_at)
    WHERE sent_at IS NULL;

-- ─────────────────────────────────────────────────────────────────
-- Tabela: outbox Kafka
-- ─────────────────────────────────────────────────────────────────

CREATE TABLE crae.outbox_events (
    event_id         UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    topic            TEXT          NOT NULL,
    key              TEXT          NOT NULL,
    payload          JSONB         NOT NULL,
    published        BOOLEAN       NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT now()
);

CREATE INDEX crae_outbox_unpublished ON crae.outbox_events (created_at)
    WHERE published = FALSE;

-- ─────────────────────────────────────────────────────────────────
-- Triggery
-- ─────────────────────────────────────────────────────────────────

-- updated_at
CREATE OR REPLACE FUNCTION crae.set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END; $$;

CREATE TRIGGER trg_rf_updated_at
    BEFORE UPDATE ON crae.risk_factors
    FOR EACH ROW EXECUTE FUNCTION crae.set_updated_at();

-- Outbox: nowy CRITICAL risk
CREATE OR REPLACE FUNCTION crae.publish_critical_risk()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.level = 'CRITICAL' AND
       (TG_OP = 'INSERT' OR OLD.level != 'CRITICAL') THEN
        INSERT INTO crae.outbox_events (topic, key, payload)
        VALUES (
            'crae.risk.critical',
            NEW.risk_id::TEXT,
            jsonb_build_object(
                'risk_id',     NEW.risk_id,
                'domain',      NEW.domain,
                'category',    NEW.category,
                'entity_name', NEW.entity_name,
                'score',       NEW.score,
                'impact_eur',  NEW.impact_eur,
                'detected_at', NEW.detected_at
            )
        );
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_critical_risk
    AFTER INSERT OR UPDATE OF score ON crae.risk_factors
    FOR EACH ROW EXECUTE FUNCTION crae.publish_critical_risk();

-- Outbox: eskalacja
CREATE OR REPLACE FUNCTION crae.publish_risk_escalated()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = 'ESCALATED' AND OLD.status != 'ESCALATED' THEN
        INSERT INTO crae.outbox_events (topic, key, payload)
        VALUES (
            'crae.risk.escalated',
            NEW.risk_id::TEXT,
            jsonb_build_object(
                'risk_id',   NEW.risk_id,
                'score',     NEW.score,
                'level',     NEW.level,
                'due_date',  NEW.due_date
            )
        );
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_risk_escalated
    AFTER UPDATE OF status ON crae.risk_factors
    FOR EACH ROW EXECUTE FUNCTION crae.publish_risk_escalated();

-- ─────────────────────────────────────────────────────────────────
-- Widoki
-- ─────────────────────────────────────────────────────────────────

CREATE OR REPLACE VIEW crae.v_risk_dashboard AS
SELECT
    rf.domain,
    rf.category,
    rf.level,
    COUNT(*)                              AS risk_count,
    SUM(rf.impact_eur)                    AS total_impact_eur,
    AVG(rf.score)                         AS avg_score,
    MAX(rf.score)                         AS max_score,
    SUM(CASE WHEN rf.status = 'OPEN' THEN 1 ELSE 0 END)      AS open_count,
    SUM(CASE WHEN rf.status = 'MITIGATED' THEN 1 ELSE 0 END) AS mitigated_count
FROM crae.risk_factors rf
WHERE rf.status IN ('OPEN', 'ESCALATED', 'MITIGATED')
GROUP BY rf.domain, rf.category, rf.level
ORDER BY max_score DESC;

CREATE OR REPLACE VIEW crae.v_top_risks AS
SELECT
    rf.risk_id,
    rf.domain,
    rf.category,
    rf.entity_name,
    rf.score,
    rf.level,
    rf.probability,
    rf.impact_eur,
    rf.status,
    rf.detected_at,
    rf.due_date,
    rf.assigned_to,
    rf.mitigation_plan IS NOT NULL AS has_mitigation
FROM crae.risk_factors rf
WHERE rf.status IN ('OPEN', 'ESCALATED')
ORDER BY rf.score DESC, rf.impact_eur DESC
LIMIT 50;

CREATE OR REPLACE VIEW crae.v_portfolio_trend AS
SELECT
    portfolio_date,
    composite_score,
    supplier_score,
    market_score,
    production_score,
    material_score,
    critical_count,
    high_count,
    total_impact_eur,
    var_95_eur,
    expected_loss_eur
FROM crae.risk_portfolios
ORDER BY portfolio_date DESC;
```

### 6.2 Indeksy dodatkowe i partycjonowanie

```sql
-- Composite index dla heat map (domain × level × impact)
CREATE INDEX crae_rf_heat_map_idx
    ON crae.risk_factors (domain, level, impact_eur DESC)
    WHERE status IN ('OPEN', 'ESCALATED');

-- Partial: tylko CRITICAL i HIGH dla alertów
CREATE INDEX crae_rf_high_critical_idx
    ON crae.risk_factors (detected_at DESC)
    WHERE level IN ('HIGH', 'CRITICAL') AND status = 'OPEN';

-- Partycjonowanie risk_portfolios (miesięczne) przy L3+
-- ALTER TABLE crae.risk_portfolios PARTITION BY RANGE (portfolio_date);
```
