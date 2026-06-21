"""
Section 1 — Search Space Definition

Buduje przestrzeń poszukiwań ze wszystkich kombinacji:
  • Alternatyw materiałowych (BOM)
  • Alternatyw procesowych (routing)
  • Dostawców per pozycja BOM
  • Opcji wolumenowych (jeśli parametryzowane)

Przestrzeń może być ogromna (wykładnicza) — silnik optymalizacji
eksploruje ją inteligentnie, nie enumeruje wprost.
"""
from __future__ import annotations

import math
import logging
from dataclasses import dataclass, field
from typing import Any

from .models import (
    BOM, BOMItem, BOMAlternative,
    ProcessRouting, ProcessStep, ProcessAlternative,
    SupplierProfile,
    SearchSpace, SearchDimension,
    ConfigurationCandidate, CostBreakdown,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Search Space Builder
# ─────────────────────────────────────────────────────────────────────────────

class SearchSpaceBuilder:
    """
    Constructs a SearchSpace from BOM + routing + supplier data.

    Each BOM item with N alternatives → 1 dimension with N options.
    Each process step with M alternatives → 1 dimension with M options.
    Total combinations = ∏ N_i × ∏ M_j.
    """

    def build(
        self,
        bom: BOM,
        routing: ProcessRouting | None = None,
        suppliers: list[SupplierProfile] | None = None,
    ) -> SearchSpace:
        space = SearchSpace()
        dims: list[SearchDimension] = []

        # BOM material dimensions
        for item in bom.items:
            alts = item.alternatives
            if not alts:
                continue
            dim = SearchDimension(
                dim_id=f"mat_{item.item_id}",
                dim_type="material",
                label=f"Material: {item.description[:40]}",
                n_options=len(alts),
                option_ids=[a.alt_id for a in alts],
                current_idx=self._current_idx(item),
            )
            dims.append(dim)

        # Process step dimensions
        if routing:
            for step in routing.steps:
                alts = step.alternatives
                if not alts:
                    continue
                dim = SearchDimension(
                    dim_id=f"proc_{step.step_id}",
                    dim_type="process",
                    label=f"Process: {step.description[:40]}",
                    n_options=len(alts),
                    option_ids=[a.proc_id for a in alts],
                    current_idx=self._proc_current_idx(step),
                )
                dims.append(dim)

        space.dimensions = dims
        space.compute_size()
        log.info(
            "SearchSpace: %d dimensions, %s combinations",
            len(dims),
            f"{space.total_combinations:,}",
        )
        return space

    def _current_idx(self, item: BOMItem) -> int:
        for i, a in enumerate(item.alternatives):
            if a.alt_id == item.current_alt_id:
                return i
        return 0

    def _proc_current_idx(self, step: ProcessStep) -> int:
        for i, a in enumerate(step.alternatives):
            if a.proc_id == step.current_proc_id:
                return i
        return 0


# ─────────────────────────────────────────────────────────────────────────────
# Configuration Encoder / Decoder
# ─────────────────────────────────────────────────────────────────────────────

class ConfigurationEncoder:
    """
    Encodes a ConfigurationCandidate as a chromosome (list of ints) and
    decodes it back.  Chromosome[i] = chosen option index for dimension[i].
    """

    def __init__(self, space: SearchSpace) -> None:
        self.space = space
        self.dims = space.dimensions

    def encode(self, candidate: ConfigurationCandidate) -> list[int]:
        chromosome = []
        for dim in self.dims:
            chosen_id = candidate.choices.get(dim.dim_id, dim.option_ids[0])
            try:
                idx = dim.option_ids.index(chosen_id)
            except ValueError:
                idx = 0
            chromosome.append(idx)
        return chromosome

    def decode(self, chromosome: list[int]) -> dict[str, str]:
        choices = {}
        for dim, idx in zip(self.dims, chromosome):
            safe_idx = idx % len(dim.option_ids)
            choices[dim.dim_id] = dim.option_ids[safe_idx]
        return choices

    def random_chromosome(self, rng) -> list[int]:
        return [rng.randrange(dim.n_options) for dim in self.dims]

    def n_dims(self) -> int:
        return len(self.dims)


# ─────────────────────────────────────────────────────────────────────────────
# Baseline extractor
# ─────────────────────────────────────────────────────────────────────────────

class BaselineExtractor:
    """Extracts the current (as-is) configuration as the baseline candidate."""

    def extract(
        self,
        bom: BOM,
        routing: ProcessRouting | None,
        space: SearchSpace,
    ) -> ConfigurationCandidate:
        choices: dict[str, str] = {}
        for dim in space.dimensions:
            choices[dim.dim_id] = dim.option_ids[dim.current_idx]
        baseline = ConfigurationCandidate(choices=choices)
        return baseline


# ─────────────────────────────────────────────────────────────────────────────
# Cost Evaluator
# ─────────────────────────────────────────────────────────────────────────────

_COUNTRY_LOGISTICS: dict[str, float] = {
    "DE": 0.02, "PL": 0.03, "CZ": 0.03, "RO": 0.04,
    "CN": 0.12, "IN": 0.10, "MX": 0.08, "US": 0.06,
}

_OVERHEAD_RATE = 0.18   # 18% of material + process


class CostEvaluator:
    """
    Evaluates the total unit cost of a configuration candidate.

    Components:
      material   = Σ (qty × unit_cost) per BOM item
      process    = Σ cost_per_unit per process step
      logistics  = country-based rate × material cost
      quality    = scrap_rate × process_cost
      overhead   = overhead_rate × (material + process)
    """

    def __init__(
        self,
        bom: BOM,
        routing: ProcessRouting | None,
        suppliers: dict[str, SupplierProfile],
        annual_volume: int = 10_000,
    ) -> None:
        self.bom = bom
        self.routing = routing
        self.suppliers = suppliers
        self.annual_volume = annual_volume
        # Index alternatives for fast lookup
        self._mat_alts: dict[str, dict[str, BOMAlternative]] = {}
        for item in bom.items:
            self._mat_alts[item.item_id] = {a.alt_id: a for a in item.alternatives}
        self._proc_alts: dict[str, dict[str, ProcessAlternative]] = {}
        if routing:
            for step in routing.steps:
                self._proc_alts[step.step_id] = {a.proc_id: a for a in step.alternatives}

    def evaluate(self, candidate: ConfigurationCandidate) -> ConfigurationCandidate:
        mat_cost = 0.0
        proc_cost = 0.0
        scrap_cost = 0.0
        tooling_cost = 0.0
        quality_idx_min = 1.0
        co2 = 0.0
        delivery_days = 0.0
        risk = 0.0

        # ── Material cost
        for item in self.bom.items:
            dim_id = f"mat_{item.item_id}"
            alt_id = candidate.choices.get(dim_id)
            if not alt_id:
                continue
            alt = self._mat_alts.get(item.item_id, {}).get(alt_id)
            if not alt:
                continue
            line_cost = item.quantity * alt.unit_cost_eur
            mat_cost += line_cost
            quality_idx_min = min(quality_idx_min, alt.quality_index)
            co2 += item.quantity * alt.co2_kg_per_kg
            delivery_days = max(delivery_days, alt.lead_time_days)
            # Supplier risk contribution
            for sid in alt.supplier_ids[:1]:
                sup = self.suppliers.get(sid)
                if sup:
                    risk = max(risk, sup.financial_risk * 0.4 + sup.geo_risk * 0.35
                               + (1 - sup.otd_pct / 100) * 0.25)

        # ── Process cost
        if self.routing:
            for step in self.routing.steps:
                dim_id = f"proc_{step.step_id}"
                proc_id = candidate.choices.get(dim_id)
                if not proc_id:
                    # Use first alternative as default
                    if step.alternatives:
                        proc_id = step.alternatives[0].proc_id
                    else:
                        continue
                proc = self._proc_alts.get(step.step_id, {}).get(proc_id)
                if not proc:
                    continue
                unit_proc = proc.cost_per_unit
                proc_cost += unit_proc
                # Volume-based tooling amortization
                if self.annual_volume > 0 and proc.tooling_eur > 0:
                    tooling_cost += proc.tooling_eur / self.annual_volume
                scrap_cost += unit_proc * proc.scrap_rate_pct / 100
                quality_idx_min = min(quality_idx_min, proc.quality_index)
                co2 += proc.co2_kg_per_unit

        # ── Logistics
        avg_country = "DE"
        logistics_rate = _COUNTRY_LOGISTICS.get(avg_country, 0.05)
        logistics_cost = mat_cost * logistics_rate

        # ── Overhead
        overhead = (mat_cost + proc_cost) * _OVERHEAD_RATE

        total = mat_cost + proc_cost + logistics_cost + scrap_cost + tooling_cost + overhead

        candidate.material_cost_eur  = round(mat_cost, 4)
        candidate.process_cost_eur   = round(proc_cost, 4)
        candidate.logistics_cost_eur = round(logistics_cost, 4)
        candidate.overhead_eur       = round(overhead, 4)
        candidate.total_cost_eur     = round(total, 4)
        candidate.quality_index      = round(quality_idx_min, 4)
        candidate.risk_score         = round(risk, 4)
        candidate.co2_kg             = round(co2, 4)
        candidate.delivery_days      = round(delivery_days, 1)

        return candidate

    def cost_breakdown(self, candidate: ConfigurationCandidate) -> CostBreakdown:
        bd = CostBreakdown(
            material_eur=candidate.material_cost_eur,
            process_eur=candidate.process_cost_eur,
            logistics_eur=candidate.logistics_cost_eur,
            overhead_eur=candidate.overhead_eur,
            quality_eur=0.0,
            tooling_eur=0.0,
        )
        bd.compute_total()
        return bd
