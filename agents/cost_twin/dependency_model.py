"""
Section 2 — Dependency Model

Model zależności kosztowych — jak parametry wejściowe
propagują się przez BOM, routing i overhead.

Rodzaje zależności:
  LinearDependency       — y = a * x + b
  ProportionalDependency — y = k * x
  ElasticityDependency   — y = y0 * (x/x0)^e (power-law)
  StepDependency         — skok przy progach (MOQ, volume breaks)
  LearningCurveDep       — Wright's learning curve y = y0 * n^(-b)
  CountryDependency      — łączy kraj produkcji z stawkami (wage, energy, duty)
  VolumeDependency       — economies of scale, fixed cost spreading
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    ProductCostModel, BomLine, RoutingStep, CostBreakdown,
    CountryProfile, SupplierCostProfile, TechnologyProfile,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Dependency types
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Dependency:
    dep_id:   str
    name:     str
    input_id: str   # driver / parameter that changes
    output_id: str  # cost element that responds

    def compute(self, x: float) -> float:
        raise NotImplementedError


@dataclass
class LinearDependency(Dependency):
    """y = slope * x + intercept"""
    slope:     float = 1.0
    intercept: float = 0.0

    def compute(self, x: float) -> float:
        return self.slope * x + self.intercept


@dataclass
class ProportionalDependency(Dependency):
    """y = factor * x"""
    factor: float = 1.0

    def compute(self, x: float) -> float:
        return self.factor * x


@dataclass
class ElasticityDependency(Dependency):
    """y = y0 * (x / x0)^elasticity  — power-law response"""
    x0:          float = 1.0
    y0:          float = 1.0
    elasticity:  float = 1.0   # 1=linear, <1=inelastic, >1=elastic

    def compute(self, x: float) -> float:
        if self.x0 == 0:
            return self.y0
        return self.y0 * (x / self.x0) ** self.elasticity


@dataclass
class StepDependency(Dependency):
    """
    Piecewise constant — breaks define thresholds.
    breaks: list of (x_threshold, y_value)
    """
    breaks: list[tuple[float, float]] = field(default_factory=list)
    default_y: float = 0.0

    def compute(self, x: float) -> float:
        result = self.default_y
        for threshold, y in sorted(self.breaks):
            if x >= threshold:
                result = y
        return result


@dataclass
class LearningCurveDependency(Dependency):
    """
    Wright's learning curve: y_n = y1 * n^(-b)
    where b = log(learning_rate) / log(2)
    learning_rate: 0.80 = 20% cost reduction per doubling of output
    """
    y1:            float = 100.0   # cost at first unit
    learning_rate: float = 0.85    # 15% reduction per doubling
    current_n:     float = 1.0     # current cumulative output

    def compute(self, n: float) -> float:
        b = -math.log(self.learning_rate) / math.log(2)
        return self.y1 * n ** (-b)

    def cost_at_volume(self, volume: float) -> float:
        return self.compute(self.current_n + volume)


@dataclass
class VolumeDependency(Dependency):
    """
    Spreading of fixed costs over volume + variable component.
    unit_cost(v) = variable + fixed / v
    """
    variable_cost: float = 0.0
    fixed_cost:    float = 0.0

    def compute(self, volume: float) -> float:
        if volume <= 0:
            return self.variable_cost + self.fixed_cost
        return self.variable_cost + self.fixed_cost / volume


# ─────────────────────────────────────────────────────────────────────────────
# DependencyRegistry — maps (parameter, element) pairs to dependencies
# ─────────────────────────────────────────────────────────────────────────────

class DependencyRegistry:
    """
    Central registry of cost dependencies for a product model.
    Used by the simulation engine to resolve how parameter changes
    propagate to individual cost elements.
    """

    def __init__(self) -> None:
        self._deps: dict[str, list[Dependency]] = {}   # input_id → deps

    def register(self, dep: Dependency) -> None:
        self._deps.setdefault(dep.input_id, []).append(dep)

    def get_deps(self, input_id: str) -> list[Dependency]:
        return self._deps.get(input_id, [])

    def resolve(self, input_id: str, new_value: float) -> dict[str, float]:
        """Resolve how changing input_id to new_value affects all outputs."""
        return {
            dep.output_id: dep.compute(new_value)
            for dep in self._deps.get(input_id, [])
        }

    def all_inputs(self) -> list[str]:
        return list(self._deps.keys())


# ─────────────────────────────────────────────────────────────────────────────
# DependencyModelBuilder — builds DependencyRegistry from ProductCostModel
# ─────────────────────────────────────────────────────────────────────────────

class DependencyModelBuilder:
    """
    Constructs a DependencyRegistry for a product by analysing:
      - BOM (material price → material cost, scrap → waste)
      - Routing (labor rate × time → labor cost, machine rate × time → machine cost)
      - Volume (fixed tooling spreading, learning curve)
      - Country profile (labor/energy rates, duties)
    """

    def build(
        self,
        model:   ProductCostModel,
        country: CountryProfile | None = None,
    ) -> DependencyRegistry:
        reg = DependencyRegistry()
        self._add_material_deps(reg, model)
        self._add_routing_deps(reg, model, country)
        self._add_volume_deps(reg, model)
        self._add_country_deps(reg, model, country)
        return reg

    def _add_material_deps(self, reg: DependencyRegistry, model: ProductCostModel) -> None:
        for i, line in enumerate(model.bom_lines):
            input_id  = f"mat_price_{line.material_id}"
            output_id = f"mat_cost_{line.material_id}"
            # unit_cost = price * quantity * (1 + scrap)
            effective_qty = line.quantity * (1 + line.scrap_rate)
            reg.register(ProportionalDependency(
                dep_id=f"dep_mat_{i}",
                name=f"{line.material_name} price → cost",
                input_id=input_id,
                output_id=output_id,
                factor=effective_qty,
            ))
            # Scrap rate dependency
            reg.register(ProportionalDependency(
                dep_id=f"dep_scrap_{i}",
                name=f"{line.material_name} scrap → waste cost",
                input_id=f"scrap_rate_{line.material_id}",
                output_id=f"waste_cost_{line.material_id}",
                factor=line.unit_price * line.quantity,
            ))

    def _add_routing_deps(
        self,
        reg:     DependencyRegistry,
        model:   ProductCostModel,
        country: CountryProfile | None,
    ) -> None:
        for step in model.routing:
            labor_h   = step.cycle_time_s / 3600.0 + step.setup_time_min / 60.0
            # Labor rate → direct labor cost
            reg.register(ProportionalDependency(
                dep_id=f"dep_labor_{step.step_id}",
                name=f"{step.operation_name} labor rate → cost",
                input_id="labor_rate",
                output_id=f"labor_cost_{step.step_id}",
                factor=labor_h,
            ))
            # Machine rate → machine cost (driven by energy)
            reg.register(ProportionalDependency(
                dep_id=f"dep_mach_{step.step_id}",
                name=f"{step.operation_name} machine rate → cost",
                input_id="machine_rate",
                output_id=f"machine_cost_{step.step_id}",
                factor=labor_h,
            ))
            # Yield — rework cost proportional to defect rate
            reg.register(ProportionalDependency(
                dep_id=f"dep_yield_{step.step_id}",
                name=f"{step.operation_name} defect rate → rework",
                input_id=f"defect_rate_{step.step_id}",
                output_id=f"rework_cost_{step.step_id}",
                factor=step.labor_rate * labor_h * 2.0,   # rework = 2× cycle time
            ))

        # Energy price → machine rate (via energy intensity)
        if country:
            avg_machine_kw = 25.0   # typical machining center kW
            reg.register(ProportionalDependency(
                dep_id="dep_energy_machine",
                name="Energy price → machine rate",
                input_id="energy_price",
                output_id="machine_rate",
                factor=avg_machine_kw / 1000.0,   # kW → proportion of EUR/kWh
            ))

    def _add_volume_deps(self, reg: DependencyRegistry, model: ProductCostModel) -> None:
        # Tooling amortisation — fixed / volume
        total_tooling = sum(step.setup_time_min * step.machine_rate / 60.0 for step in model.routing) * 0.1
        reg.register(VolumeDependency(
            dep_id="dep_tooling_volume",
            name="Volume → tooling per unit",
            input_id="annual_volume",
            output_id="tooling_per_unit",
            variable_cost=0.0,
            fixed_cost=total_tooling * 12,   # annualised tooling
        ))
        # Learning curve on labor
        reg.register(LearningCurveDependency(
            dep_id="dep_learning",
            name="Cumulative volume → labor learning",
            input_id="cumulative_volume",
            output_id="labor_learning_factor",
            y1=1.0,
            learning_rate=0.90,
            current_n=model.annual_volume,
        ))

    def _add_country_deps(
        self,
        reg:     DependencyRegistry,
        model:   ProductCostModel,
        country: CountryProfile | None,
    ) -> None:
        if not country:
            return
        # Labor wage → direct labor
        social_mult = 1 + country.social_charge_pct / 100
        reg.register(LinearDependency(
            dep_id="dep_country_labor",
            name=f"{country.country_name} wage → labor cost",
            input_id="country_wage",
            output_id="labor_rate",
            slope=social_mult,
            intercept=0.0,
        ))
        # Import duty → material landed cost
        reg.register(ProportionalDependency(
            dep_id="dep_import_duty",
            name="Import duty → material cost",
            input_id="material_cost_base",
            output_id="material_cost_with_duty",
            factor=1.0 + country.import_duty_pct / 100,
        ))
        # CO2 tax → energy surcharge
        co2_per_kwh = 0.0004   # tCO2/kWh typical grid
        reg.register(ProportionalDependency(
            dep_id="dep_co2",
            name="CO2 tax → energy surcharge",
            input_id="co2_price",
            output_id="energy_surcharge",
            factor=co2_per_kwh,
        ))
        # FX → material cost (for imported components)
        reg.register(ElasticityDependency(
            dep_id="dep_fx",
            name="FX rate → import material cost",
            input_id="fx_rate",
            output_id="imported_material_cost",
            x0=country.fx_to_eur,
            y0=model.direct_material_eur * 0.4,   # assume 40% imported
            elasticity=1.0,
        ))


# ─────────────────────────────────────────────────────────────────────────────
# CostPropagator — resolves parameter change into full cost delta
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ParameterChange:
    input_id:  str
    old_value: float
    new_value: float
    unit:      str = ""


class CostPropagator:
    """
    Given a set of parameter changes and a DependencyRegistry,
    computes the full cost delta per element.
    """

    def __init__(self, registry: DependencyRegistry) -> None:
        self._registry = registry

    def propagate(
        self,
        changes: list[ParameterChange],
        baseline: CostBreakdown,
        country: CountryProfile | None = None,
    ) -> dict[str, float]:
        """
        Returns dict of output_id → new_cost_value.
        """
        updates: dict[str, float] = {}
        for change in changes:
            resolved = self._registry.resolve(change.input_id, change.new_value)
            updates.update(resolved)
        return updates

    def delta_summary(
        self,
        changes:  list[ParameterChange],
        baseline: CostBreakdown,
        country:  CountryProfile | None = None,
    ) -> list[dict[str, Any]]:
        """Human-readable delta per driver."""
        deltas: list[dict[str, Any]] = []
        for change in changes:
            delta_pct = (change.new_value - change.old_value) / max(abs(change.old_value), 1e-9) * 100
            deps = self._registry.get_deps(change.input_id)
            for dep in deps:
                old_out = dep.compute(change.old_value)
                new_out = dep.compute(change.new_value)
                deltas.append({
                    "driver":      change.input_id,
                    "output":      dep.output_id,
                    "dep_name":    dep.name,
                    "old_value":   round(old_out, 4),
                    "new_value":   round(new_out, 4),
                    "delta_eur":   round(new_out - old_out, 4),
                    "delta_pct":   round((new_out - old_out) / max(abs(old_out), 1e-9) * 100, 2),
                    "input_delta_pct": round(delta_pct, 2),
                })
        return sorted(deltas, key=lambda x: -abs(x["delta_eur"]))


# ─────────────────────────────────────────────────────────────────────────────
# Standard country profiles
# ─────────────────────────────────────────────────────────────────────────────

COUNTRY_PROFILES: dict[str, CountryProfile] = {
    "DE": CountryProfile(
        country_code="DE", country_name="Germany", currency="EUR", fx_to_eur=1.0,
        avg_wage_eur_h=35.0, social_charge_pct=21.0,
        electricity_eur_kwh=0.20, gas_eur_mwh=50.0,
        corporate_tax_pct=30.0, import_duty_pct=2.5, vat_pct=19.0,
        avg_inland_transport_eur_t=80.0, port_handling_eur_t=40.0,
        co2_tax_eur_t=80.0, risk_level=CountryRiskLevel.VERY_LOW,
        political_risk_score=0.05, logistics_risk_score=0.08,
        factory_overhead_pct=0.30, admin_overhead_pct=0.12,
    ),
    "PL": CountryProfile(
        country_code="PL", country_name="Poland", currency="PLN", fx_to_eur=4.25,
        avg_wage_eur_h=14.0, social_charge_pct=19.5,
        electricity_eur_kwh=0.14, gas_eur_mwh=40.0,
        corporate_tax_pct=19.0, import_duty_pct=2.5, vat_pct=23.0,
        avg_inland_transport_eur_t=60.0, port_handling_eur_t=35.0,
        co2_tax_eur_t=80.0, risk_level=CountryRiskLevel.LOW,
        political_risk_score=0.15, logistics_risk_score=0.12,
        factory_overhead_pct=0.25, admin_overhead_pct=0.10,
    ),
    "CN": CountryProfile(
        country_code="CN", country_name="China", currency="CNY", fx_to_eur=7.8,
        avg_wage_eur_h=5.5,  social_charge_pct=40.0,
        electricity_eur_kwh=0.08, gas_eur_mwh=25.0,
        corporate_tax_pct=25.0, import_duty_pct=5.0, vat_pct=13.0,
        avg_inland_transport_eur_t=35.0, port_handling_eur_t=55.0, customs_clearance_eur=300.0,
        co2_tax_eur_t=10.0, risk_level=CountryRiskLevel.MEDIUM,
        political_risk_score=0.35, logistics_risk_score=0.25,
        factory_overhead_pct=0.18, admin_overhead_pct=0.08,
    ),
    "MX": CountryProfile(
        country_code="MX", country_name="Mexico", currency="MXN", fx_to_eur=18.5,
        avg_wage_eur_h=4.0,  social_charge_pct=32.0,
        electricity_eur_kwh=0.07, gas_eur_mwh=18.0,
        corporate_tax_pct=30.0, import_duty_pct=3.0, vat_pct=16.0,
        avg_inland_transport_eur_t=45.0, port_handling_eur_t=50.0,
        co2_tax_eur_t=3.0, risk_level=CountryRiskLevel.MEDIUM,
        political_risk_score=0.30, logistics_risk_score=0.28,
        factory_overhead_pct=0.20, admin_overhead_pct=0.09,
    ),
    "IN": CountryProfile(
        country_code="IN", country_name="India", currency="INR", fx_to_eur=89.0,
        avg_wage_eur_h=2.5,  social_charge_pct=28.0,
        electricity_eur_kwh=0.09, gas_eur_mwh=20.0,
        corporate_tax_pct=22.0, import_duty_pct=7.5, vat_pct=18.0,
        avg_inland_transport_eur_t=40.0, port_handling_eur_t=60.0, customs_clearance_eur=250.0,
        co2_tax_eur_t=2.0, risk_level=CountryRiskLevel.MEDIUM,
        political_risk_score=0.25, logistics_risk_score=0.30,
        factory_overhead_pct=0.22, admin_overhead_pct=0.10,
    ),
    "CZ": CountryProfile(
        country_code="CZ", country_name="Czech Republic", currency="CZK", fx_to_eur=24.5,
        avg_wage_eur_h=13.0, social_charge_pct=24.5,
        electricity_eur_kwh=0.13, gas_eur_mwh=38.0,
        corporate_tax_pct=21.0, import_duty_pct=2.5, vat_pct=21.0,
        avg_inland_transport_eur_t=65.0, port_handling_eur_t=40.0,
        co2_tax_eur_t=80.0, risk_level=CountryRiskLevel.LOW,
        political_risk_score=0.12, logistics_risk_score=0.10,
        factory_overhead_pct=0.25, admin_overhead_pct=0.10,
    ),
    "RO": CountryProfile(
        country_code="RO", country_name="Romania", currency="RON", fx_to_eur=5.0,
        avg_wage_eur_h=8.0,  social_charge_pct=37.25,
        electricity_eur_kwh=0.10, gas_eur_mwh=30.0,
        corporate_tax_pct=16.0, import_duty_pct=2.5, vat_pct=19.0,
        avg_inland_transport_eur_t=55.0, port_handling_eur_t=38.0,
        co2_tax_eur_t=80.0, risk_level=CountryRiskLevel.LOW,
        political_risk_score=0.20, logistics_risk_score=0.15,
        factory_overhead_pct=0.22, admin_overhead_pct=0.09,
    ),
}
