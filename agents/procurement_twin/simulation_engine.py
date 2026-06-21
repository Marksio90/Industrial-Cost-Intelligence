"""
Section 1 — Simulation Engine

Dyskretna symulacja czasu zakupowego (krok = 1 dzień):
  - Stan: dostawcy, ceny, zapasy, zamówienia
  - Przejścia: składanie zamówień, dostawy, zużycie, re-order
  - Polityka zakupów: EOQ, safety stock, re-order point
  - Ceny aktualizowane co dzień (GBM + sezonowość)
  - Lead time losowany z rozkładu per dostawca
  - Fallback na alternatywnych dostawców gdy primary down
"""
from __future__ import annotations

import copy
import logging
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import (
    SupplierProfile, SupplierStatus, MaterialSpec,
    ProcurementOrder, InventoryState, SimulationState,
    DailyKPI, FXRate, RiskEvent, RiskEventType, ImpactLevel,
    PriceDistribution, LeadTimeDistribution, DistributionType,
)

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Stochastic samplers
# ─────────────────────────────────────────────────────────────────────────────

def _sample_price(model: PriceDistribution, day: int, rng: random.Random) -> float:
    """GBM daily price step + seasonality."""
    dt       = 1.0 / 252.0          # trading day fraction
    drift    = (model.mean_pct_yoy / 100.0 - 0.5 * model.volatility ** 2) * dt
    shock    = model.volatility * math.sqrt(dt) * rng.gauss(0, 1)
    # GBM
    factor   = math.exp(drift + shock)
    price    = model.base_price * factor
    # Seasonality
    month    = (day % 365) // 30
    if model.seasonal_idx:
        price *= model.seasonal_idx[min(month, len(model.seasonal_idx) - 1)]
    # Bounds
    floor   = model.base_price * (1 + model.floor_pct)
    ceiling = model.base_price * (1 + model.ceiling_pct)
    return max(floor, min(ceiling, price))


def _sample_lead_time(model: LeadTimeDistribution, rng: random.Random) -> int:
    """Sample lead time in days from the given distribution."""
    if model.distribution == DistributionType.NORMAL:
        days = model.nominal_days + model.mean_days + rng.gauss(0, model.std_days)
    elif model.distribution == DistributionType.LOGNORMAL:
        mu  = math.log(max(model.nominal_days + model.mean_days, 1))
        sig = model.std_days / max(model.nominal_days, 1)
        days = math.exp(mu + sig * rng.gauss(0, 1))
    elif model.distribution == DistributionType.TRIANGULAR:
        lo  = model.min_days
        hi  = model.max_days
        mid = model.nominal_days + model.mean_days
        days = rng.triangular(lo, hi, mid)
    else:
        days = model.nominal_days + model.mean_days + rng.uniform(-model.std_days, model.std_days)
    return max(model.min_days, min(model.max_days, int(round(days))))


def _sample_supplier_failure(supplier: SupplierProfile, rng: random.Random) -> bool:
    """Return True if supplier goes bankrupt on this day."""
    daily_prob = 1.0 - (1.0 - supplier.bankruptcy_prob_annual) ** (1.0 / 365.0)
    return rng.random() < daily_prob


def _sample_quality_ok(supplier: SupplierProfile, rng: random.Random) -> bool:
    return rng.random() >= supplier.quality_defect_rate


def _sample_on_time(supplier: SupplierProfile, rng: random.Random) -> bool:
    return rng.random() < supplier.on_time_delivery_pct


def _gbm_fx(rate: FXRate, rng: random.Random) -> float:
    """Single GBM step for FX rate."""
    dt    = 1.0 / 252.0
    drift = (rate.drift - 0.5 * rate.volatility ** 2) * dt
    shock = rate.volatility * math.sqrt(dt) * rng.gauss(0, 1)
    return rate.rate * math.exp(drift + shock)


# ─────────────────────────────────────────────────────────────────────────────
# EOQ / procurement policy
# ─────────────────────────────────────────────────────────────────────────────

def compute_eoq(
    annual_demand: float,
    order_cost:    float,
    holding_cost_pct: float,
    unit_price:    float,
) -> float:
    """Economic Order Quantity — Wilson formula."""
    denom = holding_cost_pct * unit_price
    if denom <= 0 or annual_demand <= 0:
        return 0.0
    return math.sqrt((2 * annual_demand * order_cost) / denom)


def compute_safety_stock(
    daily_demand: float,
    lead_time_days: int,
    demand_std: float,
    lead_time_std: float,
    service_z: float = 1.645,   # Z for 95% service level
) -> float:
    """Safety stock via demand × lead-time variance formula."""
    ss = service_z * math.sqrt(
        lead_time_days * demand_std ** 2
        + daily_demand ** 2 * lead_time_std ** 2
    )
    return max(0.0, ss)


# ─────────────────────────────────────────────────────────────────────────────
# SimulationEngine
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PendingDelivery:
    """Tracks an in-flight order that will arrive on `arrival_day`."""
    order_id:     str
    material_id:  str
    supplier_id:  str
    quantity:     float
    unit_price:   float
    arrival_day:  int
    is_emergency: bool = False


class SimulationEngine:
    """
    Discrete-time (daily) procurement simulation engine.

    Usage
    -----
    engine = SimulationEngine(state, seed=42)
    engine.step()          # advance 1 day
    engine.run(days=365)   # run full horizon
    kpis = engine.daily_kpis
    """

    ORDER_COST_EUR    = 150.0     # fixed cost per purchase order
    HOLDING_COST_PCT  = 0.25     # 25% of value / year
    EMERGENCY_MARKUP  = 0.20     # 20% price premium for emergency buys
    SERVICE_Z         = 1.645    # 95% service level
    DEMAND_STD_RATIO  = 0.15    # demand std = 15% of mean

    def __init__(
        self,
        state: SimulationState,
        seed:  int = 42,
    ) -> None:
        self.state       = copy.deepcopy(state)
        self.rng         = random.Random(seed)
        self.day         = 0
        self.daily_kpis: list[DailyKPI] = []
        self.risk_events: list[RiskEvent] = []
        self._pending:   list[PendingDelivery] = []
        self._prices:    dict[str, float] = {}       # material_id → current price
        self._fx:        dict[str, float] = {}       # "EUR/PLN" → rate
        self._baseline_price: dict[str, float] = {}
        self._emergency_orders = 0
        self._supplier_switches = 0
        self._initialize()

    def _initialize(self) -> None:
        for mat_id, mat in self.state.materials.items():
            self._prices[mat_id] = mat.price_model.base_price
            self._baseline_price[mat_id] = mat.price_model.base_price
        for pair, fx in self.state.fx_rates.items():
            self._fx[pair] = fx.rate
        # Init inventory
        for mat_id, mat in self.state.materials.items():
            daily = mat.annual_usage / 365.0
            if mat_id not in self.state.inventory:
                self.state.inventory[mat_id] = InventoryState(
                    material_id=mat_id,
                    on_hand=mat.safety_stock * 2,
                    on_order=0.0,
                    safety_stock=mat.safety_stock,
                    demand_per_day=daily,
                )

    # ── Public ────────────────────────────────────────────────────────────────

    def step(self) -> DailyKPI:
        """Advance simulation by one day. Returns KPI snapshot."""
        self.day += 1
        self._update_prices()
        self._update_fx()
        self._check_supplier_failures()
        self._process_deliveries()
        self._consume_inventory()
        self._trigger_reorders()
        kpi = self._compute_kpi()
        self.daily_kpis.append(kpi)
        return kpi

    def run(self, days: int) -> list[DailyKPI]:
        """Run for `days` steps and return all daily KPIs."""
        for _ in range(days):
            self.step()
        return self.daily_kpis

    def apply_shock(
        self,
        target_type: str,
        target_id:   str,
        parameter:   str,
        value:       Any,
        ramp_days:   int = 0,
    ) -> None:
        """Apply a scenario shock to the simulation state."""
        if target_type == "supplier":
            self._shock_supplier(target_id, parameter, value)
        elif target_type == "material":
            self._shock_material(target_id, parameter, value)
        elif target_type == "fx":
            self._shock_fx(target_id, parameter, value)
        elif target_type == "market":
            self._shock_market(target_id, parameter, value)

    # ── Price & FX updates ────────────────────────────────────────────────────

    def _update_prices(self) -> None:
        for mat_id, mat in self.state.materials.items():
            self._prices[mat_id] = _sample_price(mat.price_model, self.day, self.rng)
            mat.price_model.base_price = self._prices[mat_id]

    def _update_fx(self) -> None:
        for pair, fx in self.state.fx_rates.items():
            new_rate = _gbm_fx(fx, self.rng)
            fx.rate  = new_rate
            self._fx[pair] = new_rate

    # ── Supplier failure ──────────────────────────────────────────────────────

    def _check_supplier_failures(self) -> None:
        for sup_id, sup in self.state.suppliers.items():
            if sup.status in (SupplierStatus.BANKRUPT, SupplierStatus.BLACKLISTED):
                continue
            if _sample_supplier_failure(sup, self.rng):
                sup.status = SupplierStatus.BANKRUPT
                event = RiskEvent(
                    event_type=RiskEventType.SUPPLIER_BANKRUPT,
                    tenant_id=self.state.tenant_id,
                    sim_day=self.day,
                    source_id=sup_id,
                    source_name=sup.name,
                    impact_level=ImpactLevel.CRITICAL,
                    affected_ids=list(sup.material_ids),
                    parameters={"supplier": sup.name, "day": self.day},
                )
                self.risk_events.append(event)
                log.debug("Day %d: Supplier %s went bankrupt", self.day, sup.name)

    # ── Deliveries ────────────────────────────────────────────────────────────

    def _process_deliveries(self) -> None:
        arrived   = [p for p in self._pending if p.arrival_day <= self.day]
        remaining = [p for p in self._pending if p.arrival_day > self.day]

        for delivery in arrived:
            inv = self.state.inventory.get(delivery.material_id)
            if inv:
                inv.on_hand  += delivery.quantity
                inv.on_order -= delivery.quantity
                inv.on_order  = max(0.0, inv.on_order)
            # update order status
            for order in self.state.orders:
                if order.order_id == delivery.order_id:
                    order.status = "delivered"
                    order.actual_delivery = datetime.now(timezone.utc)
                    actual_days = self.day - (self.day - (delivery.arrival_day - (self.day - self.day)))
                    break
        self._pending = remaining

    # ── Consumption ───────────────────────────────────────────────────────────

    def _consume_inventory(self) -> None:
        for mat_id, inv in self.state.inventory.items():
            demand = inv.demand_per_day * (1 + self.rng.gauss(0, self.DEMAND_STD_RATIO))
            demand = max(0.0, demand)
            consumed = min(demand, inv.on_hand)
            inv.on_hand = max(0.0, inv.on_hand - consumed)
            # Compute days of supply
            total_avail = inv.on_hand + inv.on_order
            inv.days_of_supply = total_avail / max(inv.demand_per_day, 0.001)
            # Stockout risk — probability inventory hits zero in 30 days
            inv.stockout_risk = max(0.0, 1.0 - inv.days_of_supply / 30.0)

    # ── Re-order logic ────────────────────────────────────────────────────────

    def _trigger_reorders(self) -> None:
        for mat_id, mat in self.state.materials.items():
            inv = self.state.inventory.get(mat_id)
            if not inv:
                continue
            position = inv.on_hand + inv.on_order
            rop      = mat.reorder_point or (inv.demand_per_day * 30 + inv.safety_stock)
            if position > rop:
                continue
            # Find best available supplier
            supplier = self._find_best_supplier(mat)
            if not supplier:
                # Emergency: try any supplier with markup
                supplier = self._find_any_supplier(mat)
                is_emergency = True
            else:
                is_emergency = False

            if not supplier:
                log.debug("Day %d: No supplier for %s — stockout risk", self.day, mat.name)
                continue

            # EOQ
            annual_demand = inv.demand_per_day * 365
            eoq = compute_eoq(
                annual_demand,
                self.ORDER_COST_EUR,
                self.HOLDING_COST_PCT,
                self._prices.get(mat_id, mat.price_model.base_price),
            )
            qty = max(eoq, supplier.moq, inv.demand_per_day * 7)    # at least 1-week supply
            if is_emergency:
                self._emergency_orders += 1

            unit_price = self._prices.get(mat_id, mat.price_model.base_price)
            if is_emergency:
                unit_price *= (1 + self.EMERGENCY_MARKUP)

            lead_days = _sample_lead_time(supplier.lead_time, self.rng)
            if not _sample_on_time(supplier, self.rng):
                lead_days += int(self.rng.triangular(1, 20, 5))

            order = ProcurementOrder(
                material_id=mat_id,
                supplier_id=supplier.supplier_id,
                quantity=qty,
                unit=mat.unit,
                unit_price=unit_price,
                currency="EUR",
                total_cost=qty * unit_price,
                order_date=datetime.now(timezone.utc),
            )
            self.state.orders.append(order)
            inv.on_order += qty
            self._pending.append(PendingDelivery(
                order_id=order.order_id,
                material_id=mat_id,
                supplier_id=supplier.supplier_id,
                quantity=qty,
                unit_price=unit_price,
                arrival_day=self.day + lead_days,
                is_emergency=is_emergency,
            ))

    def _find_best_supplier(self, mat: MaterialSpec) -> SupplierProfile | None:
        """Active, lowest-risk supplier for this material."""
        candidates = []
        for sid in mat.supplier_ids:
            sup = self.state.suppliers.get(sid)
            if sup and sup.status not in (SupplierStatus.BANKRUPT, SupplierStatus.BLACKLISTED, SupplierStatus.SANCTIONED):
                score = sup.quality_score if hasattr(sup, "quality_score") else 0.5
                score = (1 - sup.geo_risk_score) * 0.5 + sup.on_time_delivery_pct * 0.5
                candidates.append((score, sup))
        if not candidates:
            return None
        candidates.sort(key=lambda x: -x[0])
        chosen = candidates[0][1]
        if chosen.supplier_id != mat.primary_supplier_id:
            self._supplier_switches += 1
        return chosen

    def _find_any_supplier(self, mat: MaterialSpec) -> SupplierProfile | None:
        """Any supplier, even degraded (for emergency orders)."""
        for sid in mat.supplier_ids:
            sup = self.state.suppliers.get(sid)
            if sup and sup.status not in (SupplierStatus.BANKRUPT, SupplierStatus.SANCTIONED, SupplierStatus.BLACKLISTED):
                return sup
        return None

    # ── KPI computation ───────────────────────────────────────────────────────

    def _compute_kpi(self) -> DailyKPI:
        recent_orders = [o for o in self.state.orders if o.status == "delivered"][-100:]
        total_spend   = sum(o.total_cost for o in recent_orders)
        avg_price     = (sum(o.unit_price for o in recent_orders) / len(recent_orders)) if recent_orders else 0.0
        delivered_lt  = [
            (p.arrival_day - self.day + len(self._pending))
            for p in self._pending
        ]
        avg_lt = sum(delivered_lt) / len(delivered_lt) if delivered_lt else 0.0

        active_sups    = sum(1 for s in self.state.suppliers.values() if s.status == SupplierStatus.ACTIVE)
        stockouts      = sum(1 for inv in self.state.inventory.values() if inv.on_hand <= 0)
        total_mats     = len(self.state.inventory)
        service_level  = 1.0 - (stockouts / max(total_mats, 1))

        at_risk = sum(
            self._prices.get(mid, 0) * inv.on_order
            for mid, inv in self.state.inventory.items()
            for sup in [self.state.suppliers.get(
                self.state.materials[mid].primary_supplier_id or "", None
            )]
            if sup and sup.geo_risk_score > 0.5
        )

        inv_value = sum(
            self._prices.get(mid, 0) * inv.on_hand
            for mid, inv in self.state.inventory.items()
        )

        return DailyKPI(
            day=self.day,
            total_spend_eur=round(total_spend, 2),
            avg_unit_price_eur=round(avg_price, 4),
            avg_lead_time_days=round(avg_lt, 1),
            service_level=round(service_level, 4),
            at_risk_spend_eur=round(at_risk, 2),
            active_supplier_count=active_sups,
            stockout_materials=stockouts,
            inventory_value_eur=round(inv_value, 2),
        )

    # ── Shock handlers ────────────────────────────────────────────────────────

    def _shock_supplier(self, supplier_id: str, parameter: str, value: Any) -> None:
        sup = self.state.suppliers.get(supplier_id)
        if not sup:
            return
        if parameter == "status":
            sup.status = SupplierStatus(value)
        elif parameter == "moq_multiplier":
            sup.moq *= float(value)
        elif parameter == "lead_time_delta":
            sup.lead_time.mean_days += float(value)
        elif parameter == "capacity_cut_pct":
            sup.capacity_units_per_month *= (1 - float(value) / 100)
        elif parameter == "price_markup_pct":
            for mat_id in sup.material_ids:
                mat = self.state.materials.get(mat_id)
                if mat:
                    mat.price_model.base_price *= (1 + float(value) / 100)
                    self._prices[mat_id] = mat.price_model.base_price

    def _shock_material(self, material_id: str, parameter: str, value: Any) -> None:
        mat = self.state.materials.get(material_id)
        if not mat:
            # Apply to all materials matching class (if target_id is a class name)
            for mid, m in self.state.materials.items():
                if parameter == "price_delta_pct":
                    m.price_model.base_price *= (1 + float(value) / 100)
                    self._prices[mid] = m.price_model.base_price
            return
        if parameter == "price_delta_pct":
            mat.price_model.base_price *= (1 + float(value) / 100)
            self._prices[material_id] = mat.price_model.base_price
        elif parameter == "demand_delta_pct":
            inv = self.state.inventory.get(material_id)
            if inv:
                inv.demand_per_day *= (1 + float(value) / 100)
        elif parameter == "volatility_delta":
            mat.price_model.volatility += float(value)

    def _shock_fx(self, pair: str, parameter: str, value: Any) -> None:
        fx = self.state.fx_rates.get(pair)
        if not fx:
            return
        if parameter == "rate_delta_pct":
            fx.rate *= (1 + float(value) / 100)
            self._fx[pair] = fx.rate
        elif parameter == "volatility_delta":
            fx.volatility += float(value)
        elif parameter == "rate_set":
            fx.rate = float(value)
            self._fx[pair] = fx.rate

    def _shock_market(self, commodity: str, parameter: str, value: Any) -> None:
        """Apply commodity-level shock to all linked materials."""
        for mat in self.state.materials.values():
            if mat.commodity_code == commodity or commodity in mat.tags:
                self._shock_material(mat.material_id, parameter, value)

    # ── Snapshot ──────────────────────────────────────────────────────────────

    def snapshot(self) -> SimulationState:
        s = copy.deepcopy(self.state)
        s.day = self.day
        return s

    def summary(self) -> dict[str, Any]:
        if not self.daily_kpis:
            return {}
        total_cost = sum(
            o.total_cost for o in self.state.orders if o.status == "delivered"
        )
        service_lvls = [k.service_level for k in self.daily_kpis]
        lead_times   = [k.avg_lead_time_days for k in self.daily_kpis if k.avg_lead_time_days > 0]
        stockout_days = sum(1 for k in self.daily_kpis if k.stockout_materials > 0)
        return {
            "days_simulated":     self.day,
            "total_cost_eur":     round(total_cost, 2),
            "total_orders":       len(self.state.orders),
            "emergency_orders":   self._emergency_orders,
            "supplier_switches":  self._supplier_switches,
            "stockout_days":      stockout_days,
            "min_service_level":  round(min(service_lvls), 4) if service_lvls else 0.0,
            "avg_service_level":  round(sum(service_lvls) / len(service_lvls), 4) if service_lvls else 0.0,
            "max_lead_time_days": round(max(lead_times), 1) if lead_times else 0.0,
            "avg_lead_time_days": round(sum(lead_times) / len(lead_times), 1) if lead_times else 0.0,
            "risk_events":        len(self.risk_events),
        }


# ─────────────────────────────────────────────────────────────────────────────
# SimulationStateBuilder — builds a realistic initial state
# ─────────────────────────────────────────────────────────────────────────────

class SimulationStateBuilder:
    """Fluent builder for SimulationState."""

    def __init__(self, tenant_id: str = "tenant-demo") -> None:
        self._state = SimulationState(tenant_id=tenant_id)

    def add_supplier(self, profile: SupplierProfile) -> "SimulationStateBuilder":
        self._state.suppliers[profile.supplier_id] = profile
        return self

    def add_material(self, spec: MaterialSpec) -> "SimulationStateBuilder":
        self._state.materials[spec.material_id] = spec
        return self

    def add_fx_rate(self, pair: str, rate: float, volatility: float = 0.08) -> "SimulationStateBuilder":
        base, quote = pair.split("/")
        self._state.fx_rates[pair] = FXRate(base=base, quote=quote, rate=rate, volatility=volatility)
        return self

    def build(self) -> SimulationState:
        return self._state
