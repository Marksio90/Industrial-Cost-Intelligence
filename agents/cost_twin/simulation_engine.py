"""
Section 3 — Cost Simulation Engine

Kompletny silnik obliczeniowy kosztu produktu:
  - CostSimulationEngine: pełny breakdown na podstawie BOM + Routing + Country
  - compute_cost_breakdown(): direct material / labor / machine / overhead / logistics / quality / tooling / energy
  - Monte Carlo: losowanie cen surowców + kursów walut + stóp produkcyjnych
  - Learning curve: Wright's formula na robociznę przy skalowaniu wolumenu
  - Sensitivity: OAT perturbation → elastyczność kosztu
"""
from __future__ import annotations

import math
import random
import logging
from dataclasses import dataclass, field
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

from .models import (
    ProductCostModel, CostBreakdown, CostDelta, CostResult,
    BomLine, RoutingStep, CountryProfile, SupplierCostProfile,
    TechnologyProfile, CostDriver,
)
from .dependency_model import COUNTRY_PROFILES, DependencyModelBuilder, CostPropagator
from .causal_graph import CostGraphBuilder

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Transport rates EUR / kg by mode and distance bracket
# ─────────────────────────────────────────────────────────────────────────────

_TRANSPORT_EUR_KG: dict[str, float] = {
    "sea":   0.0008,   # container shipping
    "air":   0.0350,   # air freight
    "rail":  0.0025,   # rail
    "road":  0.0060,   # truck
    "local": 0.0020,   # domestic
}

_PORT_HANDLING_EUR: dict[str, float] = {
    "sea":   180.0,
    "air":    90.0,
    "rail":   40.0,
    "road":   20.0,
    "local":   5.0,
}

_COUNTRY_TRANSPORT_MODE: dict[str, str] = {
    "DE": "road",
    "PL": "road",
    "CZ": "road",
    "RO": "road",
    "MX": "sea",
    "CN": "sea",
    "IN": "sea",
}


# ─────────────────────────────────────────────────────────────────────────────
# Core breakdown computation
# ─────────────────────────────────────────────────────────────────────────────

def compute_material_cost(
    bom: list[BomLine],
    country: CountryProfile,
    supplier_profiles: dict[str, SupplierCostProfile] | None = None,
    price_overrides: dict[str, float] | None = None,
) -> tuple[float, dict[str, float]]:
    """
    Returns (total_material_eur, line_breakdown).
    Applies scrap rate, import duty, supplier margin, FX conversion.
    """
    total = 0.0
    breakdown: dict[str, float] = {}
    price_overrides = price_overrides or {}
    supplier_profiles = supplier_profiles or {}

    for line in bom:
        unit_price = price_overrides.get(line.material_id, line.unit_price)

        # FX: prices assumed in EUR; non-EUR countries add local FX cost
        fx_factor = country.fx_to_eur  # EUR per local unit; if prices in EUR keep 1.0

        # Effective quantity with scrap
        eff_qty = line.quantity * (1.0 + line.scrap_rate)

        # Import duty for non-EU countries (material landed cost)
        duty_factor = 1.0 + country.import_duty_pct / 100.0

        # Supplier margin / tooling amortization
        sup_profile = supplier_profiles.get(line.supplier_id or "", None)
        if sup_profile:
            margin_factor = 1.0 + sup_profile.margin_pct / 100.0
            tooling_add   = sup_profile.tooling_amort_per_unit
        else:
            margin_factor = 1.0
            tooling_add   = 0.0

        line_cost = eff_qty * unit_price * duty_factor * margin_factor + tooling_add
        breakdown[line.material_id] = line_cost
        total += line_cost

    return total, breakdown


def compute_labor_and_machine_cost(
    routing: list[RoutingStep],
    country: CountryProfile,
    tech_profiles: dict[str, TechnologyProfile] | None = None,
    volume: int = 1,
) -> tuple[float, float, float, dict[str, dict]]:
    """
    Returns (labor_eur, machine_eur, energy_eur, step_breakdown).
    """
    tech_profiles = tech_profiles or {}
    labor_total   = 0.0
    machine_total = 0.0
    energy_total  = 0.0
    breakdown: dict[str, dict] = {}

    for step in sorted(routing, key=lambda s: s.sequence):
        tech = tech_profiles.get(step.process_type, None)

        cycle_h    = step.cycle_time_s / 3600.0
        setup_h    = (step.setup_time_min / 60.0) / max(volume, 1)  # setup spread over volume

        # Labor rate: use step rate or country wage × social charge
        if step.labor_rate_eur_h is not None:
            labor_rate = step.labor_rate_eur_h
        else:
            labor_rate = country.avg_wage_eur_h * (1.0 + country.social_charge_pct / 100.0)

        # Machine rate: step → tech profile → default
        if step.machine_rate_eur_h is not None:
            machine_rate = step.machine_rate_eur_h
        elif tech:
            machine_rate = tech.machine_rate_eur_h
        else:
            machine_rate = 25.0  # default

        # Energy from tech profile
        if tech:
            energy_kwh_h = tech.energy_kwh_per_h
        else:
            energy_kwh_h = 5.0  # default kWh/h

        # Yield adjustment: cost per good part = cost / yield
        yield_pct = step.yield_pct if step.yield_pct else (tech.yield_pct if tech else 0.98)
        yield_factor = 1.0 / max(yield_pct, 0.50)

        labor_step   = (cycle_h + setup_h) * labor_rate * yield_factor
        machine_step = (cycle_h + setup_h) * machine_rate * yield_factor
        energy_step  = (cycle_h + setup_h) * energy_kwh_h * country.electricity_eur_kwh * yield_factor

        labor_total   += labor_step
        machine_total += machine_step
        energy_total  += energy_step

        breakdown[step.step_id] = {
            "labor_eur":   labor_step,
            "machine_eur": machine_step,
            "energy_eur":  energy_step,
            "cycle_h":     cycle_h,
            "setup_h":     setup_h,
            "yield":       yield_pct,
        }

    return labor_total, machine_total, energy_total, breakdown


def compute_tooling_cost(
    routing: list[RoutingStep],
    tech_profiles: dict[str, TechnologyProfile] | None = None,
    annual_volume: int = 10_000,
) -> float:
    """Tooling amortization per unit = tooling_cost / min(life, annual_volume)."""
    tech_profiles = tech_profiles or {}
    total = 0.0
    for step in routing:
        tech = tech_profiles.get(step.process_type)
        if tech and tech.tooling_cost_eur > 0 and tech.tooling_life_units > 0:
            per_unit = tech.tooling_cost_eur / tech.tooling_life_units
            total += per_unit
    return total


def compute_logistics_cost(
    product_weight_kg: float,
    country: CountryProfile,
    destination_country: str = "DE",
) -> float:
    """Simplified logistics: transport + port handling + customs clearance."""
    mode = _COUNTRY_TRANSPORT_MODE.get(country.country_code, "sea")

    transport = product_weight_kg * _TRANSPORT_EUR_KG[mode]
    handling  = _PORT_HANDLING_EUR[mode] / max(product_weight_kg, 1.0)  # spread over shipment
    customs   = 50.0 if country.country_code not in {"DE", "PL", "CZ", "RO"} else 0.0

    return transport + handling + customs


def compute_overhead_cost(
    labor_eur:   float,
    machine_eur: float,
    country:     CountryProfile,
) -> tuple[float, float]:
    """Factory overhead + admin overhead."""
    factory_oh = (labor_eur + machine_eur) * country.factory_overhead_pct / 100.0
    admin_oh   = (labor_eur + machine_eur) * country.admin_overhead_pct   / 100.0
    return factory_oh, admin_oh


def compute_quality_cost(
    material_eur: float,
    labor_eur:    float,
    machine_eur:  float,
    routing:      list[RoutingStep],
    supplier_profiles: dict[str, SupplierCostProfile] | None = None,
) -> float:
    """Quality / rework cost: defect rate × rework cost fraction."""
    total_manufacturing = material_eur + labor_eur + machine_eur
    avg_defect_rate = 0.005  # 0.5% default

    if supplier_profiles:
        rates = [p.defect_rate for p in supplier_profiles.values() if p.defect_rate > 0]
        if rates:
            avg_defect_rate = sum(rates) / len(rates)

    rework_cost_factor = 0.60  # rework costs 60% of original manufacturing cost
    return total_manufacturing * avg_defect_rate * rework_cost_factor


def compute_tax_and_finance_cost(
    total_manufacturing: float,
    country:             CountryProfile,
    financing_rate:      float = 0.04,  # 4% annual cost of capital
    payment_days:        int   = 60,
) -> tuple[float, float]:
    """CO2 tax (energy-proportional) + financing cost."""
    # CO2 surcharge (rough: 50 kWh/unit assumption if not provided)
    co2_cost = 0.05 * country.co2_tax_eur_t  # 50 kg CO2 / 1000 = 0.05 t per unit

    # Financing: working capital tied up for payment_days
    financing_cost = total_manufacturing * financing_rate * (payment_days / 365.0)

    return co2_cost, financing_cost


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class CostSimulationEngine:
    """
    Full cost breakdown engine for a ProductCostModel.

    Usage:
        engine = CostSimulationEngine()
        result = engine.compute(model, country_code="PL")
    """

    def compute(
        self,
        model:             ProductCostModel,
        country_code:      str | None = None,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:     dict[str, TechnologyProfile]   | None = None,
        price_overrides:   dict[str, float] | None = None,
        volume_override:   int | None = None,
    ) -> CostResult:
        country_code = country_code or model.production_country
        country      = COUNTRY_PROFILES.get(country_code, COUNTRY_PROFILES["DE"])
        volume       = volume_override or model.annual_volume

        # ── Material ──────────────────────────────────────────────────────────
        mat_total, mat_lines = compute_material_cost(
            model.bom_lines, country, supplier_profiles, price_overrides
        )

        # ── Labor + Machine + Energy ──────────────────────────────────────────
        labor, machine, energy, routing_steps = compute_labor_and_machine_cost(
            model.routing, country, tech_profiles, volume
        )

        # ── Tooling ───────────────────────────────────────────────────────────
        tooling = compute_tooling_cost(model.routing, tech_profiles, volume)

        # ── Overhead ──────────────────────────────────────────────────────────
        factory_oh, admin_oh = compute_overhead_cost(labor, machine, country)

        # ── Logistics ─────────────────────────────────────────────────────────
        weight_kg = getattr(model, "product_weight_kg", 1.0)
        logistics = compute_logistics_cost(weight_kg, country)

        # ── Quality ───────────────────────────────────────────────────────────
        quality = compute_quality_cost(mat_total, labor, machine, model.routing, supplier_profiles)

        # ── Tax / Finance ─────────────────────────────────────────────────────
        mfg_total   = mat_total + labor + machine + energy + tooling + factory_oh + admin_oh
        co2, finance = compute_tax_and_finance_cost(mfg_total, country)

        # ── Total ─────────────────────────────────────────────────────────────
        subtotal = mfg_total + logistics + quality + co2 + finance

        # Margin
        margin_pct = getattr(model, "target_margin_pct", 15.0)
        margin_eur = subtotal * margin_pct / 100.0
        total_price = subtotal + margin_eur

        breakdown = CostBreakdown(
            direct_material_eur   = mat_total,
            direct_labor_eur      = labor,
            machine_cost_eur      = machine,
            energy_cost_eur       = energy,
            tooling_cost_eur      = tooling,
            factory_overhead_eur  = factory_oh,
            admin_overhead_eur    = admin_oh,
            logistics_eur         = logistics,
            quality_cost_eur      = quality,
            co2_tax_eur           = co2,
            finance_cost_eur      = finance,
            margin_eur            = margin_eur,
            total_cost_eur        = subtotal,
            total_price_eur       = total_price,
        )

        return CostResult(
            model_id      = getattr(model, "model_id", "unknown"),
            country_code  = country_code,
            annual_volume = volume,
            breakdown     = breakdown,
            material_lines= mat_lines,
            routing_steps = routing_steps,
        )

    def compute_delta(
        self,
        baseline: CostResult,
        scenario: CostResult,
    ) -> CostDelta:
        b = baseline.breakdown
        s = scenario.breakdown

        delta_total = s.total_cost_eur - b.total_cost_eur
        delta_pct   = (delta_total / b.total_cost_eur * 100.0) if b.total_cost_eur else 0.0

        return CostDelta(
            baseline_total_eur  = b.total_cost_eur,
            scenario_total_eur  = s.total_cost_eur,
            delta_eur           = delta_total,
            delta_pct           = delta_pct,
            material_delta_eur  = s.direct_material_eur   - b.direct_material_eur,
            labor_delta_eur     = s.direct_labor_eur       - b.direct_labor_eur,
            logistics_delta_eur = s.logistics_eur          - b.logistics_eur,
            overhead_delta_eur  = (s.factory_overhead_eur + s.admin_overhead_eur) -
                                  (b.factory_overhead_eur + b.admin_overhead_eur),
            quality_delta_eur   = s.quality_cost_eur       - b.quality_cost_eur,
            tax_delta_eur       = s.co2_tax_eur            - b.co2_tax_eur,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Monte Carlo
    # ─────────────────────────────────────────────────────────────────────────

    def monte_carlo(
        self,
        model:           ProductCostModel,
        country_code:    str,
        n_runs:          int = 1000,
        price_vol:       float = 0.10,   # ±10% price volatility
        fx_vol:          float = 0.05,   # ±5% FX volatility
        labor_vol:       float = 0.03,   # ±3% labor rate volatility
        seed:            int | None = None,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:   dict[str, TechnologyProfile] | None = None,
    ) -> MCSimResult:
        rng = random.Random(seed)
        country = COUNTRY_PROFILES.get(country_code, COUNTRY_PROFILES["DE"])

        results: list[float] = []

        for _ in range(n_runs):
            # Perturb prices
            price_ov: dict[str, float] = {}
            for line in model.bom_lines:
                shock = 1.0 + rng.gauss(0, price_vol)
                price_ov[line.material_id] = line.unit_price * max(shock, 0.2)

            # Perturb country (FX + wage)
            perturbed = CountryProfile(
                country_code        = country.country_code,
                currency            = country.currency,
                fx_to_eur           = country.fx_to_eur * (1.0 + rng.gauss(0, fx_vol)),
                avg_wage_eur_h      = country.avg_wage_eur_h * (1.0 + rng.gauss(0, labor_vol)),
                social_charge_pct   = country.social_charge_pct,
                electricity_eur_kwh = country.electricity_eur_kwh * (1.0 + rng.gauss(0, 0.02)),
                corporate_tax_pct   = country.corporate_tax_pct,
                import_duty_pct     = country.import_duty_pct,
                co2_tax_eur_t       = country.co2_tax_eur_t,
                risk_level          = country.risk_level,
                factory_overhead_pct= country.factory_overhead_pct,
                admin_overhead_pct  = country.admin_overhead_pct,
            )

            # Compute cost with perturbed inputs
            mat_total, _ = compute_material_cost(model.bom_lines, perturbed, supplier_profiles, price_ov)
            labor, machine, energy, _ = compute_labor_and_machine_cost(model.routing, perturbed, tech_profiles, model.annual_volume)
            tooling = compute_tooling_cost(model.routing, tech_profiles, model.annual_volume)
            factory_oh, admin_oh = compute_overhead_cost(labor, machine, perturbed)
            weight_kg = getattr(model, "product_weight_kg", 1.0)
            logistics = compute_logistics_cost(weight_kg, perturbed)
            quality   = compute_quality_cost(mat_total, labor, machine, model.routing, supplier_profiles)
            co2, finance = compute_tax_and_finance_cost(mat_total + labor + machine + energy + tooling + factory_oh + admin_oh, perturbed)
            total = mat_total + labor + machine + energy + tooling + factory_oh + admin_oh + logistics + quality + co2 + finance
            results.append(total)

        results.sort()
        n = len(results)
        mean   = sum(results) / n
        var_95 = results[int(n * 0.95)]
        var_99 = results[int(n * 0.99)]
        p5     = results[int(n * 0.05)]
        p50    = results[n // 2]
        p95    = results[int(n * 0.95)]

        # CVaR 95%: average of top 5%
        tail   = results[int(n * 0.95):]
        cvar_95 = sum(tail) / len(tail) if tail else var_95

        variance = sum((x - mean) ** 2 for x in results) / n
        std      = math.sqrt(variance)

        return MCSimResult(
            n_runs   = n_runs,
            mean_eur = mean,
            std_eur  = std,
            p5_eur   = p5,
            p50_eur  = p50,
            p95_eur  = p95,
            var_95   = var_95,
            var_99   = var_99,
            cvar_95  = cvar_95,
            samples  = results,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Sensitivity (OAT)
    # ─────────────────────────────────────────────────────────────────────────

    def sensitivity_oat(
        self,
        model:           ProductCostModel,
        country_code:    str,
        delta_pct:       float = 10.0,
        supplier_profiles: dict[str, SupplierCostProfile] | None = None,
        tech_profiles:   dict[str, TechnologyProfile] | None = None,
    ) -> list[SensitivityEntry]:
        """
        One-At-a-Time sensitivity: perturb each material price and each cost driver by ±delta_pct.
        Returns list sorted by |swing_eur| descending.
        """
        baseline = self.compute(model, country_code, supplier_profiles, tech_profiles)
        base_cost = baseline.breakdown.total_cost_eur
        entries: list[SensitivityEntry] = []
        factor = delta_pct / 100.0

        # Material prices
        for line in model.bom_lines:
            # +delta
            hi_ov = {l.material_id: l.unit_price for l in model.bom_lines}
            hi_ov[line.material_id] = line.unit_price * (1 + factor)
            lo_ov = dict(hi_ov)
            lo_ov[line.material_id] = line.unit_price * (1 - factor)

            hi = self.compute(model, country_code, supplier_profiles, tech_profiles, hi_ov).breakdown.total_cost_eur
            lo = self.compute(model, country_code, supplier_profiles, tech_profiles, lo_ov).breakdown.total_cost_eur

            entries.append(SensitivityEntry(
                driver    = f"material_price:{line.material_id}",
                hi_eur    = hi,
                lo_eur    = lo,
                swing_eur = hi - lo,
                elasticity= ((hi - lo) / base_cost) / (2 * factor) if base_cost else 0.0,
            ))

        # Country wage
        country = COUNTRY_PROFILES.get(country_code, COUNTRY_PROFILES["DE"])
        for attr, label in [("avg_wage_eur_h", "labor_rate"), ("electricity_eur_kwh", "electricity_price")]:
            base_val = getattr(country, attr)
            for sign, tag in [(+1, "hi"), (-1, "lo")]:
                perturbed = CountryProfile(**{**country.__dict__, attr: base_val * (1 + sign * factor)})
                c = self.compute(model, country_code, supplier_profiles, tech_profiles)
                # Use propagator for country-level sensitivity
                c_total = c.breakdown.total_cost_eur

            # Approximate swing via dependency model
            dep = DependencyModelBuilder().build(model, country)
            propagator = CostPropagator(dep)
            hi_inputs = {attr: base_val * (1 + factor)}
            lo_inputs = {attr: base_val * (1 - factor)}
            hi_costs = propagator.propagate(hi_inputs)
            lo_costs = propagator.propagate(lo_inputs)
            hi_total = sum(hi_costs.values())
            lo_total = sum(lo_costs.values())
            swing = abs(hi_total - lo_total)

            entries.append(SensitivityEntry(
                driver    = label,
                hi_eur    = base_cost + swing / 2,
                lo_eur    = base_cost - swing / 2,
                swing_eur = swing,
                elasticity= (swing / base_cost) / (2 * factor) if base_cost else 0.0,
            ))

        entries.sort(key=lambda e: abs(e.swing_eur), reverse=True)
        return entries

    # ─────────────────────────────────────────────────────────────────────────
    # Learning curve scaling
    # ─────────────────────────────────────────────────────────────────────────

    def scale_to_volume(
        self,
        base_result:    CostResult,
        new_volume:     int,
        base_volume:    int,
        learning_rate:  float = 0.85,
    ) -> CostResult:
        """Apply Wright's learning curve to labor cost when volume changes."""
        b = base_result.breakdown
        if base_volume <= 0 or new_volume <= 0:
            return base_result

        exponent  = -math.log(1 / learning_rate) / math.log(2)
        lc_factor = (new_volume / base_volume) ** exponent

        # Fixed costs spread differently
        new_tooling = b.tooling_cost_eur * (base_volume / new_volume)  # amortized over more units

        new_breakdown = CostBreakdown(
            direct_material_eur  = b.direct_material_eur,
            direct_labor_eur     = b.direct_labor_eur     * lc_factor,
            machine_cost_eur     = b.machine_cost_eur,
            energy_cost_eur      = b.energy_cost_eur,
            tooling_cost_eur     = new_tooling,
            factory_overhead_eur = b.factory_overhead_eur * lc_factor,
            admin_overhead_eur   = b.admin_overhead_eur   * lc_factor,
            logistics_eur        = b.logistics_eur,
            quality_cost_eur     = b.quality_cost_eur     * lc_factor,
            co2_tax_eur          = b.co2_tax_eur,
            finance_cost_eur     = b.finance_cost_eur,
            margin_eur           = 0.0,
            total_cost_eur       = 0.0,
            total_price_eur      = 0.0,
        )
        new_breakdown.total_cost_eur = (
            new_breakdown.direct_material_eur
            + new_breakdown.direct_labor_eur
            + new_breakdown.machine_cost_eur
            + new_breakdown.energy_cost_eur
            + new_breakdown.tooling_cost_eur
            + new_breakdown.factory_overhead_eur
            + new_breakdown.admin_overhead_eur
            + new_breakdown.logistics_eur
            + new_breakdown.quality_cost_eur
            + new_breakdown.co2_tax_eur
            + new_breakdown.finance_cost_eur
        )
        margin_pct = (b.margin_eur / b.total_cost_eur * 100.0) if b.total_cost_eur else 15.0
        new_breakdown.margin_eur    = new_breakdown.total_cost_eur * margin_pct / 100.0
        new_breakdown.total_price_eur = new_breakdown.total_cost_eur + new_breakdown.margin_eur

        return CostResult(
            model_id      = base_result.model_id,
            country_code  = base_result.country_code,
            annual_volume = new_volume,
            breakdown     = new_breakdown,
            material_lines= base_result.material_lines,
            routing_steps = base_result.routing_steps,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Result dataclasses
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MCSimResult:
    n_runs:   int
    mean_eur: float
    std_eur:  float
    p5_eur:   float
    p50_eur:  float
    p95_eur:  float
    var_95:   float
    var_99:   float
    cvar_95:  float
    samples:  list[float] = field(default_factory=list)

    def histogram(self, bins: int = 20) -> dict:
        if not self.samples:
            return {}
        lo, hi = self.samples[0], self.samples[-1]
        if lo == hi:
            return {lo: len(self.samples)}
        width = (hi - lo) / bins
        counts: dict[float, int] = {}
        for s in self.samples:
            bucket = lo + int((s - lo) / width) * width
            counts[round(bucket, 2)] = counts.get(round(bucket, 2), 0) + 1
        return counts


@dataclass
class SensitivityEntry:
    driver:     str
    hi_eur:     float
    lo_eur:     float
    swing_eur:  float
    elasticity: float
