"""
Section 4 — Scenario Planning

Silnik scenariuszy "co jeśli" dla cyfrowego bliźniaka kosztów:
  - ScenarioPlanner: uruchamia scenariusze zmian materiału, dostawcy, technologii, kraju
  - ScenarioLibrary: rejestr scenariuszy in-memory
  - CompoundScenarioBuilder: łączenie wielu szoków w jeden scenariusz
  - StressTestRunner: batch scenariuszy → najgorszy przypadek

Scenariusze:
  MATERIAL_CHANGE   → zmień materiał w BOM (inny materiał lub cena)
  SUPPLIER_CHANGE   → zmień dostawcę (inny profil marży / lead time / jakości)
  TECHNOLOGY_CHANGE → zmień technologię produkcji (inny profil maszyny)
  COUNTRY_CHANGE    → zmień kraj produkcji (inny profil kosztowy)
  VOLUME_CHANGE     → zmień wolumen produkcji (learning curve + tooling amortization)
  COMPOUND          → kombinacja powyższych
"""
from __future__ import annotations

import copy
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ProductCostModel, CostResult, CostDelta, CostScenario,
    CostChange, ScenarioType, BomLine, RoutingStep,
    SupplierCostProfile, TechnologyProfile, CountryProfile,
)
from .dependency_model import COUNTRY_PROFILES
from .simulation_engine import CostSimulationEngine

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario_id:    str
    scenario_name:  str
    scenario_type:  ScenarioType
    baseline:       CostResult
    scenario:       CostResult
    delta:          CostDelta
    recommendations: list[str] = field(default_factory=list)
    run_time_s:     float = 0.0
    metadata:       dict[str, Any] = field(default_factory=dict)

    @property
    def impact_label(self) -> str:
        pct = self.delta.delta_pct
        if pct > 15:
            return "CRITICAL"
        elif pct > 5:
            return "HIGH"
        elif pct > 1:
            return "MEDIUM"
        elif pct < -5:
            return "SAVING"
        return "LOW"


@dataclass
class StressTestSummary:
    scenario_count:  int
    worst_case:      ScenarioResult | None
    best_case:       ScenarioResult | None
    avg_delta_pct:   float
    critical_count:  int
    summary_table:   list[dict[str, Any]]


# ─────────────────────────────────────────────────────────────────────────────
# ScenarioPlanner
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioPlanner:
    """
    Executes what-if scenarios on a ProductCostModel.

    Usage:
        planner = ScenarioPlanner()
        result = planner.run_material_change(model, "steel_plate", new_price=2.50)
    """

    def __init__(self) -> None:
        self._engine = CostSimulationEngine()

    # ── Baseline ──────────────────────────────────────────────────────────────

    def _compute_baseline(
        self,
        model:             ProductCostModel,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> CostResult:
        return self._engine.compute(
            model, model.production_country, supplier_profiles, tech_profiles
        )

    # ── Material change ───────────────────────────────────────────────────────

    def run_material_change(
        self,
        model:             ProductCostModel,
        material_id:       str,
        new_price:         float | None = None,
        new_material_id:   str   | None = None,
        new_scrap_rate:    float | None = None,
        price_delta_pct:   float | None = None,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
        scenario_name:     str   = "Material Change",
    ) -> ScenarioResult:
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, supplier_profiles, tech_profiles)

        # Clone BOM with modification
        new_bom: list[BomLine] = []
        found   = False
        for line in model.bom_lines:
            if line.material_id == material_id:
                found = True
                new_line = copy.copy(line)
                if new_material_id:
                    new_line.material_id = new_material_id
                if new_price is not None:
                    new_line.unit_price = new_price
                elif price_delta_pct is not None:
                    new_line.unit_price = line.unit_price * (1.0 + price_delta_pct / 100.0)
                if new_scrap_rate is not None:
                    new_line.scrap_rate = new_scrap_rate
                new_bom.append(new_line)
            else:
                new_bom.append(copy.copy(line))

        if not found:
            raise ValueError(f"Material '{material_id}' not found in BOM")

        new_model = copy.copy(model)
        new_model.bom_lines = new_bom

        scenario_result = self._engine.compute(
            new_model, model.production_country, supplier_profiles, tech_profiles
        )
        delta = self._engine.compute_delta(baseline, scenario_result)

        recs = self._material_recs(delta, material_id, new_price or 0, baseline)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.MATERIAL_CHANGE,
            baseline      = baseline,
            scenario      = scenario_result,
            delta         = delta,
            recommendations = recs,
            run_time_s    = time.perf_counter() - t0,
            metadata      = {"material_id": material_id, "new_price": new_price, "price_delta_pct": price_delta_pct},
        )

    # ── Supplier change ───────────────────────────────────────────────────────

    def run_supplier_change(
        self,
        model:              ProductCostModel,
        old_supplier_id:    str,
        new_supplier_id:    str,
        new_supplier_profile: SupplierCostProfile,
        old_supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile] | None = None,
        scenario_name:     str = "Supplier Change",
    ) -> ScenarioResult:
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, old_supplier_profiles, tech_profiles)

        # Replace supplier in BOM lines
        new_bom = []
        for line in model.bom_lines:
            new_line = copy.copy(line)
            if line.supplier_id == old_supplier_id:
                new_line.supplier_id = new_supplier_id
                # If new supplier has price adjustment, use it
                if new_supplier_profile.margin_pct != (old_supplier_profiles or {}).get(old_supplier_id, SupplierCostProfile()).margin_pct:
                    pass  # price stays, margin changes via profile
            new_bom.append(new_line)

        new_model = copy.copy(model)
        new_model.bom_lines = new_bom

        new_profiles = dict(old_supplier_profiles or {})
        new_profiles[new_supplier_id] = new_supplier_profile
        # Remove old supplier
        new_profiles.pop(old_supplier_id, None)

        scenario_result = self._engine.compute(
            new_model, model.production_country, new_profiles, tech_profiles
        )
        delta = self._engine.compute_delta(baseline, scenario_result)
        recs  = self._supplier_recs(delta, new_supplier_profile)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.SUPPLIER_CHANGE,
            baseline      = baseline,
            scenario      = scenario_result,
            delta         = delta,
            recommendations = recs,
            run_time_s    = time.perf_counter() - t0,
            metadata      = {"old_supplier": old_supplier_id, "new_supplier": new_supplier_id},
        )

    # ── Technology change ─────────────────────────────────────────────────────

    def run_technology_change(
        self,
        model:              ProductCostModel,
        process_type:       str,
        new_tech_profile:   TechnologyProfile,
        old_tech_profiles:  dict[str, TechnologyProfile] | None = None,
        supplier_profiles:  dict[str, SupplierCostProfile] | None = None,
        scenario_name:      str = "Technology Change",
    ) -> ScenarioResult:
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, supplier_profiles, old_tech_profiles)

        new_tech = dict(old_tech_profiles or {})
        new_tech[process_type] = new_tech_profile

        # Optionally update routing cycle times if tech profile has faster cycle
        new_routing = []
        for step in model.routing:
            new_step = copy.copy(step)
            if step.process_type == process_type and new_tech_profile.cycle_time_s:
                new_step.cycle_time_s = new_tech_profile.cycle_time_s
            new_routing.append(new_step)

        new_model = copy.copy(model)
        new_model.routing = new_routing

        scenario_result = self._engine.compute(
            new_model, model.production_country, supplier_profiles, new_tech
        )
        delta = self._engine.compute_delta(baseline, scenario_result)
        recs  = self._technology_recs(delta, new_tech_profile)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.TECHNOLOGY_CHANGE,
            baseline      = baseline,
            scenario      = scenario_result,
            delta         = delta,
            recommendations = recs,
            run_time_s    = time.perf_counter() - t0,
            metadata      = {"process_type": process_type, "automation_level": new_tech_profile.automation_level},
        )

    # ── Country change ────────────────────────────────────────────────────────

    def run_country_change(
        self,
        model:             ProductCostModel,
        new_country_code:  str,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
        scenario_name:     str = "Country Change",
    ) -> ScenarioResult:
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, supplier_profiles, tech_profiles)

        if new_country_code not in COUNTRY_PROFILES:
            raise ValueError(f"Unknown country '{new_country_code}'. Available: {list(COUNTRY_PROFILES)}")

        new_model = copy.copy(model)
        new_model.production_country = new_country_code

        scenario_result = self._engine.compute(
            new_model, new_country_code, supplier_profiles, tech_profiles
        )
        delta = self._engine.compute_delta(baseline, scenario_result)

        old_c = COUNTRY_PROFILES.get(model.production_country, COUNTRY_PROFILES["DE"])
        new_c = COUNTRY_PROFILES[new_country_code]
        recs  = self._country_recs(delta, old_c, new_c)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.COUNTRY_CHANGE,
            baseline      = baseline,
            scenario      = scenario_result,
            delta         = delta,
            recommendations = recs,
            run_time_s    = time.perf_counter() - t0,
            metadata      = {
                "old_country": model.production_country,
                "new_country": new_country_code,
                "wage_diff_pct": round((new_c.avg_wage_eur_h - old_c.avg_wage_eur_h) / old_c.avg_wage_eur_h * 100, 1),
            },
        )

    # ── Volume change ─────────────────────────────────────────────────────────

    def run_volume_change(
        self,
        model:             ProductCostModel,
        new_volume:        int,
        learning_rate:     float = 0.85,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
        scenario_name:     str = "Volume Change",
    ) -> ScenarioResult:
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, supplier_profiles, tech_profiles)

        scaled = self._engine.scale_to_volume(
            baseline, new_volume, model.annual_volume, learning_rate
        )
        delta = self._engine.compute_delta(baseline, scaled)
        recs  = self._volume_recs(delta, model.annual_volume, new_volume)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.VOLUME_CHANGE,
            baseline      = baseline,
            scenario      = scaled,
            delta         = delta,
            recommendations = recs,
            run_time_s    = time.perf_counter() - t0,
            metadata      = {
                "old_volume": model.annual_volume,
                "new_volume": new_volume,
                "learning_rate": learning_rate,
            },
        )

    # ── Compound ──────────────────────────────────────────────────────────────

    def run_compound(
        self,
        model:               ProductCostModel,
        changes:             list[CostChange],
        supplier_profiles:   dict[str, SupplierCostProfile] | None = None,
        tech_profiles:       dict[str, TechnologyProfile]   | None = None,
        scenario_name:       str = "Compound Scenario",
    ) -> ScenarioResult:
        """Apply multiple changes at once."""
        t0 = time.perf_counter()
        baseline = self._compute_baseline(model, supplier_profiles, tech_profiles)

        new_model = copy.deepcopy(model)
        new_sup = dict(supplier_profiles or {})
        new_tech = dict(tech_profiles or {})

        for change in changes:
            if change.change_type == "material_price":
                for line in new_model.bom_lines:
                    if line.material_id == change.target_id:
                        line.unit_price = change.new_value if change.new_value else line.unit_price * (1 + change.delta_pct / 100.0)
            elif change.change_type == "country":
                new_model.production_country = change.target_id
            elif change.change_type == "volume":
                new_model.annual_volume = int(change.new_value or new_model.annual_volume)
            elif change.change_type == "supplier_margin":
                if change.target_id in new_sup:
                    new_sup[change.target_id].margin_pct = change.new_value or new_sup[change.target_id].margin_pct

        scenario_result = self._engine.compute(
            new_model, new_model.production_country, new_sup, new_tech
        )
        delta = self._engine.compute_delta(baseline, scenario_result)

        return ScenarioResult(
            scenario_id   = str(uuid.uuid4()),
            scenario_name = scenario_name,
            scenario_type = ScenarioType.COMPOUND,
            baseline      = baseline,
            scenario      = scenario_result,
            delta         = delta,
            recommendations = self._compound_recs(delta, changes),
            run_time_s    = time.perf_counter() - t0,
            metadata      = {"change_count": len(changes)},
        )

    # ── Recommendation generators ─────────────────────────────────────────────

    def _material_recs(self, delta: CostDelta, material_id: str, new_price: float, baseline: CostResult) -> list[str]:
        recs = []
        if delta.delta_pct > 5:
            recs.append(f"Rozważ zamiennik dla materiału '{material_id}' — wzrost kosztu {delta.delta_pct:.1f}%")
        if delta.material_delta_eur > 50:
            recs.append("Negocjuj wolumenowe rabaty z dostawcą materiału")
        if delta.delta_pct > 10:
            recs.append("Sprawdź możliwość hedgingu ceny surowca (kontrakty terminowe)")
        if delta.delta_pct < -2:
            recs.append(f"Zmiana materiału generuje oszczędność {abs(delta.delta_eur):.2f} EUR/szt — rekomendowana")
        return recs

    def _supplier_recs(self, delta: CostDelta, profile: SupplierCostProfile) -> list[str]:
        recs = []
        if delta.delta_pct > 3:
            recs.append(f"Nowy dostawca kosztuje więcej (+{delta.delta_pct:.1f}%) — negocjuj warunki")
        if profile.defect_rate > 0.01:
            recs.append(f"Wskaźnik defektów dostawcy {profile.defect_rate*100:.1f}% — wymagaj SLA jakościowego")
        if profile.qualification_eur > 5000:
            recs.append(f"Koszt kwalifikacji {profile.qualification_eur:.0f} EUR — amortyzuj przez min. 3 lata")
        if delta.delta_pct < -3:
            recs.append(f"Zmiana dostawcy generuje oszczędność {abs(delta.delta_eur):.2f} EUR/szt")
        return recs

    def _technology_recs(self, delta: CostDelta, tech: TechnologyProfile) -> list[str]:
        recs = []
        if tech.capex_eur > 0 and tech.payback_years > 0:
            recs.append(f"CAPEX {tech.capex_eur:,.0f} EUR — zwrot w {tech.payback_years:.1f} lat przy obecnym wolumenie")
        if tech.automation_level > 0.7:
            recs.append("Wysoka automatyzacja — zaplanuj program szkoleń dla operatorów")
        if delta.delta_pct < -5:
            recs.append(f"Nowa technologia redukuje koszt o {abs(delta.delta_pct):.1f}% — priorytet wdrożenia")
        if delta.delta_pct > 8:
            recs.append("Wzrost kosztu przy nowej technologii — zweryfikuj parametry maszyny")
        return recs

    def _country_recs(self, delta: CostDelta, old_c: CountryProfile, new_c: CountryProfile) -> list[str]:
        recs = []
        if delta.delta_pct < -10:
            recs.append(f"Relokacja do {new_c.country_code} redukuje koszt o {abs(delta.delta_pct):.1f}% — silna rekomendacja")
        if new_c.risk_level.value >= 3:
            recs.append(f"Ryzyko kraju {new_c.country_code} = {new_c.risk_level.name} — wymagaj dual-source lub stock buffer")
        if new_c.import_duty_pct > 5:
            recs.append(f"Cło importowe {new_c.import_duty_pct}% — sprawdź umowy handlowe i reguły origin")
        if delta.logistics_delta_eur > 100:
            recs.append(f"Wzrost kosztów logistyki +{delta.logistics_delta_eur:.0f} EUR/szt — zoptymalizuj transport")
        wage_diff = new_c.avg_wage_eur_h - old_c.avg_wage_eur_h
        if wage_diff < -5:
            recs.append(f"Stawka płac niższa o {abs(wage_diff):.1f} EUR/h — duży potencjał oszczędności na robociźnie")
        return recs

    def _volume_recs(self, delta: CostDelta, old_vol: int, new_vol: int) -> list[str]:
        recs = []
        if new_vol > old_vol:
            recs.append(f"Wzrost wolumenu {old_vol}→{new_vol} szt — efekt skali redukuje koszt jednostkowy")
            recs.append("Zweryfikuj dostępność mocy produkcyjnych i lead time dostawców")
        else:
            recs.append(f"Redukcja wolumenu {old_vol}→{new_vol} szt — koszt stały rośnie na jednostkę")
            recs.append("Rozważ outsourcing lub shared production przy niskim wolumenie")
        if abs(delta.delta_pct) > 15:
            recs.append("Duży wpływ wolumenu — modeluj krzywe uczenia i punkty progu rentowności")
        return recs

    def _compound_recs(self, delta: CostDelta, changes: list[CostChange]) -> list[str]:
        recs = []
        if delta.delta_pct > 10:
            recs.append(f"Skumulowany wzrost kosztu {delta.delta_pct:.1f}% — pilna optymalizacja wymagana")
        if delta.delta_pct < -5:
            recs.append(f"Scenariusz generuje oszczędność {abs(delta.delta_eur):.2f} EUR/szt — rekomenduj wdrożenie")
        recs.append(f"Zastosowano {len(changes)} zmian jednocześnie — przeanalizuj efekty interakcji")
        return recs


# ─────────────────────────────────────────────────────────────────────────────
# Scenario Library (in-memory registry)
# ─────────────────────────────────────────────────────────────────────────────

class ScenarioLibrary:
    """
    In-memory store for scenario definitions and results.
    Keyed by tenant_id.
    """

    def __init__(self) -> None:
        self._scenarios: dict[str, dict[str, CostScenario]] = {}
        self._results:   dict[str, dict[str, ScenarioResult]] = {}

    def save(self, tenant_id: str, scenario: CostScenario) -> None:
        self._scenarios.setdefault(tenant_id, {})[scenario.scenario_id] = scenario

    def get(self, tenant_id: str, scenario_id: str) -> CostScenario | None:
        return self._scenarios.get(tenant_id, {}).get(scenario_id)

    def list_all(self, tenant_id: str) -> list[CostScenario]:
        return list(self._scenarios.get(tenant_id, {}).values())

    def delete(self, tenant_id: str, scenario_id: str) -> bool:
        store = self._scenarios.get(tenant_id, {})
        if scenario_id in store:
            del store[scenario_id]
            self._results.get(tenant_id, {}).pop(scenario_id, None)
            return True
        return False

    def save_result(self, tenant_id: str, result: ScenarioResult) -> None:
        self._results.setdefault(tenant_id, {})[result.scenario_id] = result

    def get_result(self, tenant_id: str, scenario_id: str) -> ScenarioResult | None:
        return self._results.get(tenant_id, {}).get(scenario_id)

    def list_results(self, tenant_id: str) -> list[ScenarioResult]:
        return list(self._results.get(tenant_id, {}).values())


# ─────────────────────────────────────────────────────────────────────────────
# Compound builder (fluent API)
# ─────────────────────────────────────────────────────────────────────────────

class CompoundScenarioBuilder:
    """Fluent builder for compound cost scenarios."""

    def __init__(self, name: str = "Compound") -> None:
        self._name    = name
        self._changes: list[CostChange] = []

    def material_price_change(self, material_id: str, delta_pct: float) -> "CompoundScenarioBuilder":
        self._changes.append(CostChange(
            change_type = "material_price",
            target_id   = material_id,
            delta_pct   = delta_pct,
        ))
        return self

    def country_relocation(self, new_country: str) -> "CompoundScenarioBuilder":
        self._changes.append(CostChange(
            change_type = "country",
            target_id   = new_country,
        ))
        return self

    def volume_change(self, new_volume: int) -> "CompoundScenarioBuilder":
        self._changes.append(CostChange(
            change_type = "volume",
            target_id   = "volume",
            new_value   = float(new_volume),
        ))
        return self

    def supplier_margin(self, supplier_id: str, new_margin_pct: float) -> "CompoundScenarioBuilder":
        self._changes.append(CostChange(
            change_type = "supplier_margin",
            target_id   = supplier_id,
            new_value   = new_margin_pct,
        ))
        return self

    def run(
        self,
        model:             ProductCostModel,
        planner:           ScenarioPlanner | None = None,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
    ) -> ScenarioResult:
        planner = planner or ScenarioPlanner()
        return planner.run_compound(model, self._changes, supplier_profiles, tech_profiles, self._name)


# ─────────────────────────────────────────────────────────────────────────────
# Stress test
# ─────────────────────────────────────────────────────────────────────────────

def run_stress_test(
    model:             ProductCostModel,
    planner:           ScenarioPlanner | None = None,
    supplier_profiles: dict[str, SupplierCostProfile] | None = None,
    tech_profiles:     dict[str, TechnologyProfile]   | None = None,
) -> StressTestSummary:
    """
    Batch of predefined stress scenarios:
      - Steel +30%
      - All material prices +20%
      - Country relocation to cheapest (CN)
      - Volume halved
      - Volume doubled
    """
    planner = planner or ScenarioPlanner()
    results: list[ScenarioResult] = []

    # 1. All material prices +20%
    try:
        changes_20 = [
            CostChange(change_type="material_price", target_id=line.material_id, delta_pct=20.0)
            for line in model.bom_lines
        ]
        r = planner.run_compound(model, changes_20, supplier_profiles, tech_profiles, "All Materials +20%")
        results.append(r)
    except Exception as exc:
        log.warning("Stress test scenario failed: %s", exc)

    # 2. Material prices +30% (worst case)
    try:
        changes_30 = [
            CostChange(change_type="material_price", target_id=line.material_id, delta_pct=30.0)
            for line in model.bom_lines
        ]
        r = planner.run_compound(model, changes_30, supplier_profiles, tech_profiles, "All Materials +30%")
        results.append(r)
    except Exception as exc:
        log.warning("Stress test scenario failed: %s", exc)

    # 3. Country change to low-cost
    for country in ["CN", "MX", "IN"]:
        try:
            r = planner.run_country_change(model, country, supplier_profiles, tech_profiles, f"Relocation to {country}")
            results.append(r)
        except Exception as exc:
            log.warning("Stress test country %s failed: %s", country, exc)

    # 4. Volume halved
    try:
        r = planner.run_volume_change(model, model.annual_volume // 2, supplier_profiles=supplier_profiles, tech_profiles=tech_profiles, scenario_name="Volume -50%")
        results.append(r)
    except Exception as exc:
        log.warning("Volume halved scenario failed: %s", exc)

    # 5. Volume doubled
    try:
        r = planner.run_volume_change(model, model.annual_volume * 2, supplier_profiles=supplier_profiles, tech_profiles=tech_profiles, scenario_name="Volume +100%")
        results.append(r)
    except Exception as exc:
        log.warning("Volume doubled scenario failed: %s", exc)

    if not results:
        return StressTestSummary(0, None, None, 0.0, 0, [])

    worst = max(results, key=lambda r: r.delta.delta_pct)
    best  = min(results, key=lambda r: r.delta.delta_pct)
    avg   = sum(r.delta.delta_pct for r in results) / len(results)
    critical = sum(1 for r in results if r.delta.delta_pct > 15)

    table = [
        {
            "scenario":   r.scenario_name,
            "type":       r.scenario_type.value,
            "delta_eur":  round(r.delta.delta_eur, 2),
            "delta_pct":  round(r.delta.delta_pct, 2),
            "impact":     r.impact_label,
        }
        for r in sorted(results, key=lambda r: r.delta.delta_pct, reverse=True)
    ]

    return StressTestSummary(
        scenario_count = len(results),
        worst_case     = worst,
        best_case      = best,
        avg_delta_pct  = round(avg, 2),
        critical_count = critical,
        summary_table  = table,
    )
