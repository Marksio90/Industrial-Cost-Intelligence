"""
Section 4 — Event Simulation

Dyskretna symulacja zdarzeń (Discrete Event Simulation):
  - Kolejka priorytetowa zdarzeń (heapq)
  - Zdarzenia: dostawy, opóźnienia, awarie, spiki cen, FX
  - Losowanie czasu następnego zdarzenia (exp, Poisson)
  - Łańcuchy zdarzeń (event cascade)
  - Rejestr historii zdarzeń
"""
from __future__ import annotations

import heapq
import logging
import math
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable
from uuid import uuid4

from .models import (
    RiskEvent, RiskEventType, ImpactLevel,
    SupplierProfile, SupplierStatus, MaterialSpec,
    SimulationState,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Event priority queue
# ─────────────────────────────────────────────────────────────────────────────

class EventPriority(int, Enum):
    CRITICAL = 1
    HIGH     = 2
    MEDIUM   = 3
    LOW      = 4


@dataclass(order=True)
class ScheduledEvent:
    """Sortable event for the priority queue."""
    fire_day:   int
    priority:   int
    event_id:   str = field(compare=False)
    event_type: RiskEventType = field(compare=False)
    source_id:  str = field(compare=False)
    payload:    dict[str, Any] = field(default_factory=dict, compare=False)
    callback:   Callable | None = field(default=None, repr=False, compare=False)


class EventQueue:
    """Min-heap priority event queue (sorted by fire_day, then priority)."""

    def __init__(self) -> None:
        self._heap: list[ScheduledEvent] = []
        self._history: list[ScheduledEvent] = []

    def schedule(self, event: ScheduledEvent) -> None:
        heapq.heappush(self._heap, event)

    def pop_due(self, current_day: int) -> list[ScheduledEvent]:
        due: list[ScheduledEvent] = []
        while self._heap and self._heap[0].fire_day <= current_day:
            e = heapq.heappop(self._heap)
            due.append(e)
            self._history.append(e)
        return due

    def peek_next(self) -> ScheduledEvent | None:
        return self._heap[0] if self._heap else None

    def size(self) -> int:
        return len(self._heap)

    def history(self) -> list[ScheduledEvent]:
        return list(self._history)


# ─────────────────────────────────────────────────────────────────────────────
# Poisson arrival samplers
# ─────────────────────────────────────────────────────────────────────────────

def _poisson_inter_arrival(rate_per_year: float, rng: random.Random) -> int:
    """Days until next Poisson event with given annual rate."""
    if rate_per_year <= 0:
        return 10_000
    rate_per_day = rate_per_year / 365.0
    u = rng.random()
    days = -math.log(max(u, 1e-10)) / rate_per_day
    return max(1, int(round(days)))


def _sample_magnitude(event_type: RiskEventType, rng: random.Random) -> float:
    """Sample shock magnitude for a given event type."""
    magnitudes: dict[RiskEventType, tuple[float, float]] = {
        RiskEventType.PRICE_SPIKE:          (5.0, 30.0),    # % increase
        RiskEventType.PRICE_DROP:           (3.0, 15.0),
        RiskEventType.LEAD_TIME_INCREASE:   (5.0, 30.0),    # extra days
        RiskEventType.MOQ_INCREASE:         (1.5, 3.0),     # multiplier
        RiskEventType.FX_SHOCK:             (2.0, 15.0),    # %
        RiskEventType.LOGISTICS_DISRUPTION: (3.0, 21.0),    # days
        RiskEventType.QUALITY_FAILURE:      (0.05, 0.20),   # defect rate
        RiskEventType.DEMAND_SURGE:         (10.0, 40.0),   # %
    }
    lo, hi = magnitudes.get(event_type, (1.0, 10.0))
    return rng.uniform(lo, hi)


# ─────────────────────────────────────────────────────────────────────────────
# Event handlers (state mutations)
# ─────────────────────────────────────────────────────────────────────────────

class EventHandler:
    """Applies scheduled events to SimulationState."""

    def __init__(self, state: SimulationState) -> None:
        self._state = state

    def handle(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        """
        Apply event to state. Returns list of cascaded follow-up events.
        """
        method = getattr(self, f"_handle_{event.event_type.value}", self._handle_default)
        return method(event)

    def _handle_supplier_bankrupt(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        sup = self._state.suppliers.get(event.source_id)
        if sup:
            sup.status = SupplierStatus.BANKRUPT
            log.info("Day %s: Supplier %s → BANKRUPT", event.fire_day, sup.name)
        return []

    def _handle_supplier_capacity_cut(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        sup = self._state.suppliers.get(event.source_id)
        if sup:
            cut_pct = float(event.payload.get("magnitude", 30))
            sup.capacity_units_per_month *= (1 - cut_pct / 100)
            sup.status = SupplierStatus.DEGRADED
        return []

    def _handle_price_spike(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        magnitude = float(event.payload.get("magnitude", 10.0))
        mat = self._state.materials.get(event.source_id)
        if mat:
            mat.price_model.base_price *= (1 + magnitude / 100)
        return []

    def _handle_price_drop(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        magnitude = float(event.payload.get("magnitude", 5.0))
        mat = self._state.materials.get(event.source_id)
        if mat:
            mat.price_model.base_price *= (1 - magnitude / 100)
        return []

    def _handle_lead_time_increase(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        sup = self._state.suppliers.get(event.source_id)
        if sup:
            sup.lead_time.mean_days += float(event.payload.get("magnitude", 10.0))
        return []

    def _handle_moq_increase(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        sup = self._state.suppliers.get(event.source_id)
        if sup:
            sup.moq *= float(event.payload.get("magnitude", 2.0))
        return []

    def _handle_fx_shock(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        pair      = event.source_id     # e.g. "EUR/PLN"
        magnitude = float(event.payload.get("magnitude", 5.0))
        fx = self._state.fx_rates.get(pair)
        if fx:
            fx.rate *= (1 + magnitude / 100)
        return []

    def _handle_logistics_disruption(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        extra = float(event.payload.get("magnitude", 7.0))
        for sup in self._state.suppliers.values():
            if sup.country == event.payload.get("country"):
                sup.lead_time.mean_days += extra
        return []

    def _handle_quality_failure(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        sup = self._state.suppliers.get(event.source_id)
        if sup:
            sup.quality_defect_rate = min(0.50, float(event.payload.get("magnitude", 0.10)))
        return []

    def _handle_geopolitical_event(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        affected_countries = event.payload.get("countries", [])
        cascades: list[ScheduledEvent] = []
        for sup in self._state.suppliers.values():
            if sup.country in affected_countries:
                sup.geo_risk_score = min(1.0, sup.geo_risk_score + 0.30)
                # Cascade: logistics disruption +14 days
                cascades.append(ScheduledEvent(
                    fire_day=event.fire_day + 3,
                    priority=EventPriority.HIGH,
                    event_id=str(uuid4()),
                    event_type=RiskEventType.LOGISTICS_DISRUPTION,
                    source_id=sup.supplier_id,
                    payload={"magnitude": 14.0, "country": sup.country},
                ))
        return cascades

    def _handle_natural_disaster(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        affected = event.payload.get("supplier_ids", [])
        cascades: list[ScheduledEvent] = []
        for sid in affected:
            cascades.append(ScheduledEvent(
                fire_day=event.fire_day,
                priority=EventPriority.CRITICAL,
                event_id=str(uuid4()),
                event_type=RiskEventType.SUPPLIER_CAPACITY_CUT,
                source_id=sid,
                payload={"magnitude": 60.0},
            ))
        return cascades

    def _handle_demand_surge(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        magnitude = float(event.payload.get("magnitude", 20.0))
        for inv in self._state.inventory.values():
            inv.demand_per_day *= (1 + magnitude / 100)
        return []

    def _handle_regulatory_change(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        affected_materials = event.payload.get("material_ids", [])
        for mid in affected_materials:
            mat = self._state.materials.get(mid)
            if mat:
                mat.price_model.base_price *= 1.05    # 5% compliance cost
        return []

    def _handle_default(self, event: ScheduledEvent) -> list[ScheduledEvent]:
        log.debug("Unhandled event type: %s", event.event_type.value)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# EventGenerator — stochastic background events
# ─────────────────────────────────────────────────────────────────────────────

# Annual event rates per supplier / material
_SUPPLIER_EVENT_RATES: dict[RiskEventType, float] = {
    RiskEventType.SUPPLIER_CAPACITY_CUT: 0.15,    # 15% chance per year
    RiskEventType.LEAD_TIME_INCREASE:    0.30,
    RiskEventType.QUALITY_FAILURE:       0.10,
    RiskEventType.MOQ_INCREASE:          0.05,
}

_MATERIAL_EVENT_RATES: dict[RiskEventType, float] = {
    RiskEventType.PRICE_SPIKE: 1.5,    # ~1.5 spikes/year on average
    RiskEventType.PRICE_DROP:  0.8,
}

_MARKET_EVENT_RATES: dict[RiskEventType, float] = {
    RiskEventType.FX_SHOCK:             2.0,
    RiskEventType.LOGISTICS_DISRUPTION: 0.5,
    RiskEventType.GEOPOLITICAL_EVENT:   0.2,
    RiskEventType.NATURAL_DISASTER:     0.1,
    RiskEventType.DEMAND_SURGE:         0.3,
}


class EventGenerator:
    """
    Pre-generates a calendar of stochastic risk events
    for the full simulation horizon.
    """

    def __init__(self, state: SimulationState, horizon_days: int, seed: int = 42) -> None:
        self._state   = state
        self._horizon = horizon_days
        self._rng     = random.Random(seed)

    def generate_all(self) -> EventQueue:
        queue = EventQueue()

        # Supplier events
        for sup_id, sup in self._state.suppliers.items():
            if sup.status in (SupplierStatus.BANKRUPT, SupplierStatus.BLACKLISTED):
                continue
            for evt_type, base_rate in _SUPPLIER_EVENT_RATES.items():
                rate = base_rate * (1 + sup.geo_risk_score)   # geo-adjusted rate
                self._schedule_poisson(queue, evt_type, sup_id, rate, EventPriority.MEDIUM)

        # Material events
        for mat_id, mat in self._state.materials.items():
            for evt_type, base_rate in _MATERIAL_EVENT_RATES.items():
                rate = base_rate * (1 + mat.price_model.volatility)
                self._schedule_poisson(queue, evt_type, mat_id, rate, EventPriority.MEDIUM)

        # Market-level events
        for evt_type, rate in _MARKET_EVENT_RATES.items():
            source_id = "global"
            if evt_type == RiskEventType.FX_SHOCK:
                for pair in self._state.fx_rates:
                    self._schedule_poisson(queue, evt_type, pair, rate, EventPriority.HIGH)
            else:
                self._schedule_poisson(queue, evt_type, source_id, rate, EventPriority.LOW)

        return queue

    def _schedule_poisson(
        self,
        queue:    EventQueue,
        evt_type: RiskEventType,
        source:   str,
        rate:     float,
        priority: int,
    ) -> None:
        day = 0
        while True:
            day += _poisson_inter_arrival(rate, self._rng)
            if day > self._horizon:
                break
            magnitude = _sample_magnitude(evt_type, self._rng)
            queue.schedule(ScheduledEvent(
                fire_day=day,
                priority=priority,
                event_id=str(uuid4()),
                event_type=evt_type,
                source_id=source,
                payload={"magnitude": magnitude},
            ))


# ─────────────────────────────────────────────────────────────────────────────
# EventDrivenSimulator — combines DES with SimulationEngine
# ─────────────────────────────────────────────────────────────────────────────

class EventDrivenSimulator:
    """
    Wraps SimulationEngine with a DES event calendar.

    Events fire at the correct simulation day, mutate state,
    may cascade into follow-up events.
    """

    def __init__(
        self,
        state:         SimulationState,
        horizon_days:  int = 365,
        seed:          int = 42,
        inject_events: list[ScheduledEvent] | None = None,
    ) -> None:
        from .simulation_engine import SimulationEngine
        self._engine   = SimulationEngine(state, seed=seed)
        self._handler  = EventHandler(self._engine.state)
        self._gen      = EventGenerator(state, horizon_days, seed=seed + 1)
        self._horizon  = horizon_days
        self._queue    = self._gen.generate_all()
        # Inject custom events (from scenario)
        for e in (inject_events or []):
            self._queue.schedule(e)

    def run(self) -> dict[str, Any]:
        all_triggered: list[dict[str, Any]] = []

        for _day in range(self._horizon):
            # Fire due events
            due = self._queue.pop_due(self._engine.day + 1)
            for event in due:
                cascades = self._handler.handle(event)
                for c in cascades:
                    self._queue.schedule(c)
                all_triggered.append({
                    "day":        event.fire_day,
                    "event_type": event.event_type.value,
                    "source_id":  event.source_id,
                    "payload":    event.payload,
                })
            self._engine.step()

        summary = self._engine.summary()
        summary["events_triggered"] = all_triggered
        summary["event_count"]      = len(all_triggered)
        return summary

    @property
    def daily_kpis(self):
        return self._engine.daily_kpis

    @property
    def risk_events(self):
        return self._engine.risk_events
