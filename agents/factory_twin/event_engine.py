"""
Section 3 — Event Engine

Silnik zdarzeń fabryki (kalendarz zdarzeń, kolejka priorytetowa):
  - FactoryEvent: zdarzenie z czasem wystąpienia i ładunkiem
  - EventCalendar: heap-based priority queue na czas symulacji
  - EventDispatcher: routing zdarzeń do handlerów
  - FailureEventGenerator: Poisson-based generowanie awarii
  - MaintenanceScheduler: planowanie PM (Preventive Maintenance)
  - QualityEventGenerator: SPC violations, quality alarms
  - OrderEventGenerator: release nowych zleceń, priority change
  - EnergyEventGenerator: spikes, curtailments
  - CascadeEngine: propagacja kaskadowych skutków awarii

Typy zdarzeń (z models.EventType):
  MACHINE_BREAKDOWN, MACHINE_REPAIR, MACHINE_SETUP, MACHINE_SETUP_DONE,
  MAINTENANCE_START, MAINTENANCE_DONE,
  JOB_ARRIVE, JOB_START, JOB_DONE, JOB_SCRAP, JOB_REWORK,
  WORKER_SHIFT_START, WORKER_SHIFT_END, WORKER_BREAK_START, WORKER_BREAK_END,
  MATERIAL_DELIVERY, BUFFER_FULL, BUFFER_EMPTY,
  TRANSPORT_DISPATCH, TRANSPORT_ARRIVE,
  ENERGY_SPIKE, ENERGY_CURTAILMENT,
  ORDER_RELEASED, ORDER_COMPLETED, ORDER_LATE, PRIORITY_CHANGE,
  QUALITY_ALARM, SPC_VIOLATION
"""
from __future__ import annotations

import heapq
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable

from .models import EventType, MachineStatus, FactoryLayout

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Core event primitives
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(order=True)
class FactoryEvent:
    fire_time:  float           # sim-seconds when event fires
    priority:   int = 5         # lower = higher priority (1=critical, 5=low)
    event_type: EventType = field(compare=False, default=EventType.JOB_ARRIVE)
    entity_id:  str       = field(compare=False, default="")
    payload:    dict[str, Any] = field(compare=False, default_factory=dict)
    cascade_parent: str | None = field(compare=False, default=None)

    def __repr__(self) -> str:
        return f"Event({self.event_type.value}@{self.fire_time:.1f}s, {self.entity_id})"


_PRIORITY_MAP: dict[EventType, int] = {
    EventType.MACHINE_BREAKDOWN:  1,
    EventType.QUALITY_ALARM:      1,
    EventType.ENERGY_CURTAILMENT: 1,
    EventType.MACHINE_REPAIR:     2,
    EventType.MAINTENANCE_START:  2,
    EventType.ORDER_LATE:         2,
    EventType.JOB_DONE:           3,
    EventType.JOB_START:          3,
    EventType.MATERIAL_DELIVERY:  3,
    EventType.ORDER_RELEASED:     3,
    EventType.WORKER_SHIFT_START: 3,
    EventType.ENERGY_SPIKE:       4,
    EventType.SPC_VIOLATION:      4,
    EventType.TRANSPORT_ARRIVE:   4,
}


def make_event(
    event_type: EventType,
    fire_time:  float,
    entity_id:  str,
    payload:    dict[str, Any] | None = None,
    cascade_parent: str | None = None,
) -> FactoryEvent:
    priority = _PRIORITY_MAP.get(event_type, 5)
    return FactoryEvent(
        fire_time      = fire_time,
        priority       = priority,
        event_type     = event_type,
        entity_id      = entity_id,
        payload        = payload or {},
        cascade_parent = cascade_parent,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Event Calendar (min-heap)
# ─────────────────────────────────────────────────────────────────────────────

class EventCalendar:
    """
    Priority queue of FactoryEvents ordered by (fire_time, priority).
    Supports peek, pop_due, schedule, and history tracking.
    """

    def __init__(self) -> None:
        self._heap:    list[FactoryEvent] = []
        self._history: list[FactoryEvent] = []

    def schedule(self, event: FactoryEvent) -> None:
        heapq.heappush(self._heap, event)

    def pop_due(self, current_time: float) -> list[FactoryEvent]:
        """Return all events with fire_time <= current_time."""
        due: list[FactoryEvent] = []
        while self._heap and self._heap[0].fire_time <= current_time:
            ev = heapq.heappop(self._heap)
            self._history.append(ev)
            due.append(ev)
        return due

    def peek_next(self) -> FactoryEvent | None:
        return self._heap[0] if self._heap else None

    def size(self) -> int:
        return len(self._heap)

    def history(self, event_type: EventType | None = None) -> list[FactoryEvent]:
        if event_type:
            return [e for e in self._history if e.event_type == event_type]
        return list(self._history)

    def cancel_for(self, entity_id: str, event_type: EventType) -> int:
        """Remove pending events matching entity_id + type. Returns count removed."""
        before = len(self._heap)
        self._heap = [e for e in self._heap if not (e.entity_id == entity_id and e.event_type == event_type)]
        heapq.heapify(self._heap)
        return before - len(self._heap)


# ─────────────────────────────────────────────────────────────────────────────
# Event Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

Handler = Callable[[FactoryEvent], list[FactoryEvent]]


class EventDispatcher:
    """
    Routes events to registered handlers.
    Handlers may return new events (cascade).
    """

    def __init__(self) -> None:
        self._handlers: dict[EventType, list[Handler]] = {}

    def register(self, event_type: EventType, handler: Handler) -> None:
        self._handlers.setdefault(event_type, []).append(handler)

    def dispatch(self, event: FactoryEvent) -> list[FactoryEvent]:
        cascades: list[FactoryEvent] = []
        for handler in self._handlers.get(event.event_type, []):
            try:
                result = handler(event)
                if result:
                    cascades.extend(result)
            except Exception as exc:
                log.warning("Handler error for %s: %s", event.event_type, exc)
        return cascades


# ─────────────────────────────────────────────────────────────────────────────
# Failure Event Generator (Poisson process)
# ─────────────────────────────────────────────────────────────────────────────

class FailureEventGenerator:
    """
    Generates machine breakdown events using exponential inter-arrival times
    (MTBF parameter from machine failure model).
    """

    def __init__(self, layout: FactoryLayout, rng: random.Random) -> None:
        self._layout = layout
        self._rng    = rng

    def generate_initial(self, calendar: EventCalendar) -> None:
        """Schedule first breakdown for each machine."""
        for mid, machine in self._layout.machines.items():
            ttf_s = self._next_ttf(machine.failure_model.mtbf_hours)
            calendar.schedule(make_event(
                EventType.MACHINE_BREAKDOWN, ttf_s, mid,
                {"mttr_hours": machine.failure_model.mttr_hours}
            ))

    def on_repair_done(self, event: FactoryEvent, calendar: EventCalendar) -> None:
        """After repair, schedule next failure."""
        mid = event.entity_id
        machine = self._layout.machines.get(mid)
        if not machine:
            return
        ttf_s = event.fire_time + self._next_ttf(machine.failure_model.mtbf_hours)
        calendar.schedule(make_event(EventType.MACHINE_BREAKDOWN, ttf_s, mid))

    def _next_ttf(self, mtbf_hours: float) -> float:
        return self._rng.expovariate(1.0 / (mtbf_hours * 3600))


# ─────────────────────────────────────────────────────────────────────────────
# Maintenance Scheduler (Preventive Maintenance)
# ─────────────────────────────────────────────────────────────────────────────

class MaintenanceScheduler:
    """
    Schedules preventive maintenance events at fixed intervals.
    PM interval = MTBF * pm_fraction (default 0.8 = 80% of MTBF).
    """

    def __init__(self, layout: FactoryLayout, pm_fraction: float = 0.8) -> None:
        self._layout = layout
        self._pm_frac = pm_fraction

    def generate_initial(self, calendar: EventCalendar) -> None:
        for mid, machine in self._layout.machines.items():
            interval = machine.failure_model.mtbf_hours * self._pm_frac * 3600
            pm_duration = machine.failure_model.mttr_hours * 0.5 * 3600  # PM half of MTTR
            calendar.schedule(make_event(
                EventType.MAINTENANCE_START, interval, mid,
                {"pm_duration_s": pm_duration, "interval_s": interval}
            ))

    def reschedule(self, event: FactoryEvent, calendar: EventCalendar) -> None:
        """After PM done, schedule next PM."""
        interval = event.payload.get("interval_s", 4 * 3600)
        pm_duration = event.payload.get("pm_duration_s", 1800)
        calendar.schedule(make_event(
            EventType.MAINTENANCE_START,
            event.fire_time + interval,
            event.entity_id,
            {"pm_duration_s": pm_duration, "interval_s": interval}
        ))


# ─────────────────────────────────────────────────────────────────────────────
# Quality Event Generator (SPC)
# ─────────────────────────────────────────────────────────────────────────────

class QualityEventGenerator:
    """
    Generates SPC violation and quality alarm events.
    Models process drift: Cpk degrades over time → violation probability increases.
    """

    def __init__(self, layout: FactoryLayout, rng: random.Random) -> None:
        self._layout = layout
        self._rng    = rng

    def generate_initial(self, calendar: EventCalendar, horizon_s: float) -> None:
        """Generate SPC violation events for machines with Cpk < 1.5."""
        for mid, machine in self._layout.machines.items():
            if machine.cpk < 1.5:
                # Rate proportional to (1.5 - Cpk): lower Cpk → more violations
                rate_per_hour = max(0.1, (1.5 - machine.cpk) * 2)
                t = self._rng.expovariate(rate_per_hour / 3600)
                while t < horizon_s:
                    calendar.schedule(make_event(
                        EventType.SPC_VIOLATION, t, mid,
                        {"cpk": machine.cpk, "sigma_deviation": self._rng.gauss(3.5, 0.5)}
                    ))
                    t += self._rng.expovariate(rate_per_hour / 3600)

    def on_spc_violation(self, event: FactoryEvent) -> list[FactoryEvent]:
        """SPC violation can escalate to quality alarm."""
        if self._rng.random() < 0.3:  # 30% chance of alarm
            return [make_event(
                EventType.QUALITY_ALARM,
                event.fire_time + 300,   # 5 min response time
                event.entity_id,
                {"sigma": event.payload.get("sigma_deviation", 3.5)},
                cascade_parent=event.entity_id,
            )]
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Order Event Generator
# ─────────────────────────────────────────────────────────────────────────────

class OrderEventGenerator:
    """Generates ORDER_RELEASED events at the start and ORDER_LATE at due dates."""

    def __init__(self, layout: FactoryLayout, sim_start: float = 0.0) -> None:
        self._layout    = layout
        self._sim_start = sim_start

    def generate(self, calendar: EventCalendar, horizon_s: float) -> None:
        for oid, order in self._layout.orders.items():
            # Release at t=0
            calendar.schedule(make_event(EventType.ORDER_RELEASED, 0.0, oid,
                {"product_id": order.product_id, "quantity": order.quantity}))
            # Due date lateness check (convert to sim-seconds offset)
            from datetime import datetime, timezone
            now_utc = datetime.now(timezone.utc)
            due_offset_s = (order.due_date - now_utc).total_seconds()
            if 0 < due_offset_s < horizon_s:
                calendar.schedule(make_event(EventType.ORDER_LATE, due_offset_s, oid,
                    {"due_date": order.due_date.isoformat()}))


# ─────────────────────────────────────────────────────────────────────────────
# Energy Event Generator
# ─────────────────────────────────────────────────────────────────────────────

class EnergyEventGenerator:
    """
    Generates energy spike and curtailment events.
    Spikes: random, 2–3× per shift.
    Curtailments: planned demand response windows.
    """

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def generate(self, calendar: EventCalendar, horizon_s: float) -> None:
        # Energy spikes (random, Poisson rate 3 per 8h shift)
        rate = 3 / (8 * 3600)
        t = self._rng.expovariate(rate)
        while t < horizon_s:
            duration = self._rng.uniform(300, 1800)  # 5–30 min spike
            calendar.schedule(make_event(
                EventType.ENERGY_SPIKE, t, "grid",
                {"magnitude_pct": self._rng.uniform(10, 40), "duration_s": duration}
            ))
            t += self._rng.expovariate(rate)

        # Planned curtailments (afternoon peak hours)
        for shift in range(int(horizon_s / (8 * 3600)) + 1):
            peak_start = shift * 8 * 3600 + 6 * 3600  # ~14:00 in 8h sim
            if peak_start < horizon_s:
                calendar.schedule(make_event(
                    EventType.ENERGY_CURTAILMENT, peak_start, "grid",
                    {"reduction_pct": 15.0, "duration_s": 3600}
                ))


# ─────────────────────────────────────────────────────────────────────────────
# Worker shift events
# ─────────────────────────────────────────────────────────────────────────────

class WorkerShiftGenerator:
    """Schedules shift start/end and break events for all workers."""

    def generate(self, layout: FactoryLayout, calendar: EventCalendar, horizon_s: float) -> None:
        shift_duration_s = 8 * 3600
        break_offset_s   = 4 * 3600   # break at midshift
        break_duration_s = 30 * 60

        for wid, worker in layout.workers.items():
            t = 0.0
            while t < horizon_s:
                calendar.schedule(make_event(EventType.WORKER_SHIFT_START, t, wid))
                calendar.schedule(make_event(EventType.WORKER_BREAK_START, t + break_offset_s, wid,
                    {"duration_s": break_duration_s}))
                calendar.schedule(make_event(EventType.WORKER_BREAK_END, t + break_offset_s + break_duration_s, wid))
                calendar.schedule(make_event(EventType.WORKER_SHIFT_END, t + shift_duration_s, wid))
                t += shift_duration_s


# ─────────────────────────────────────────────────────────────────────────────
# Material delivery events
# ─────────────────────────────────────────────────────────────────────────────

class MaterialDeliveryGenerator:
    """Schedules raw material replenishment events for raw material buffers."""

    def __init__(self, rng: random.Random) -> None:
        self._rng = rng

    def generate(self, layout: FactoryLayout, calendar: EventCalendar, horizon_s: float) -> None:
        for bid, buf in layout.buffers.items():
            if buf.buffer_type.value == "raw_material":
                # Delivery every 4h on average
                t = self._rng.expovariate(1.0 / (4 * 3600))
                while t < horizon_s:
                    qty = self._rng.randint(50, 200)
                    calendar.schedule(make_event(
                        EventType.MATERIAL_DELIVERY, t, bid,
                        {"quantity": qty, "product_id": "raw"}
                    ))
                    t += self._rng.expovariate(1.0 / (4 * 3600))


# ─────────────────────────────────────────────────────────────────────────────
# Cascade Engine
# ─────────────────────────────────────────────────────────────────────────────

class CascadeEngine:
    """
    Models ripple effects of breakdowns:
      MACHINE_BREAKDOWN → downstream machines get BLOCKED
      ENERGY_CURTAILMENT → machines with high power get slowed (capacity reduction)
      QUALITY_ALARM → production halt for inspection (partial shutdown)
    """

    def __init__(self, layout: FactoryLayout) -> None:
        self._layout = layout

    def breakdown_cascade(self, event: FactoryEvent) -> list[FactoryEvent]:
        """Downstream machines may get starved when this machine breaks."""
        cascades: list[FactoryEvent] = []
        mid = event.entity_id
        # Find machines that feed into this one (they may get blocked)
        for other_mid in self._layout.machines:
            if other_mid == mid:
                continue
            # Simple heuristic: same cell → cascade
            m1 = self._layout.machines[mid]
            m2 = self._layout.machines[other_mid]
            if m1.cell_id == m2.cell_id:
                cascades.append(make_event(
                    EventType.BUFFER_FULL,
                    event.fire_time + 600,  # 10 min to fill buffer
                    f"wip_{m2.cell_id}",
                    {"caused_by": mid},
                    cascade_parent=mid,
                ))
        return cascades

    def energy_curtailment_cascade(self, event: FactoryEvent) -> list[FactoryEvent]:
        """High-power machines may need to slow down or pause during curtailment."""
        cascades: list[FactoryEvent] = []
        reduction = event.payload.get("reduction_pct", 15.0) / 100.0
        duration  = event.payload.get("duration_s", 3600)

        for mid, machine in self._layout.machines.items():
            if machine.energy.processing_kw > 20.0:  # high-power threshold
                cascades.append(make_event(
                    EventType.PRIORITY_CHANGE,
                    event.fire_time,
                    mid,
                    {"capacity_reduction": reduction, "duration_s": duration},
                    cascade_parent="grid",
                ))
        return cascades

    def quality_alarm_cascade(self, event: FactoryEvent) -> list[FactoryEvent]:
        """Quality alarm on machine → inspection hold on downstream buffer."""
        mid = event.entity_id
        machine = self._layout.machines.get(mid)
        if not machine:
            return []
        return [make_event(
            EventType.BUFFER_FULL,
            event.fire_time + 120,
            f"quarantine_{machine.cell_id}",
            {"inspection_required": True, "source": mid},
            cascade_parent=mid,
        )]


# ─────────────────────────────────────────────────────────────────────────────
# Full Event Engine (orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class EventEngine:
    """
    Orchestrates all event generators and the event calendar.
    Used by external simulation loops or standalone event replay.
    """

    def __init__(self, layout: FactoryLayout, seed: int | None = None) -> None:
        self._layout   = layout
        self._rng      = random.Random(seed)
        self._calendar = EventCalendar()
        self._dispatcher = EventDispatcher()
        self._cascade  = CascadeEngine(layout)

        # Generators
        self._failure_gen    = FailureEventGenerator(layout, self._rng)
        self._pm_sched       = MaintenanceScheduler(layout)
        self._quality_gen    = QualityEventGenerator(layout, self._rng)
        self._order_gen      = OrderEventGenerator(layout)
        self._energy_gen     = EnergyEventGenerator(self._rng)
        self._shift_gen      = WorkerShiftGenerator()
        self._material_gen   = MaterialDeliveryGenerator(self._rng)

        # Register cascades
        self._dispatcher.register(EventType.MACHINE_BREAKDOWN, self._cascade.breakdown_cascade)
        self._dispatcher.register(EventType.ENERGY_CURTAILMENT, self._cascade.energy_curtailment_cascade)
        self._dispatcher.register(EventType.QUALITY_ALARM, self._cascade.quality_alarm_cascade)
        self._dispatcher.register(EventType.SPC_VIOLATION, self._quality_gen.on_spc_violation)

    def initialize(self, horizon_s: float) -> None:
        """Pre-generate all future events up to horizon."""
        self._failure_gen.generate_initial(self._calendar)
        self._pm_sched.generate_initial(self._calendar)
        self._quality_gen.generate_initial(self._calendar, horizon_s)
        self._order_gen.generate(self._calendar, horizon_s)
        self._energy_gen.generate(self._calendar, horizon_s)
        self._shift_gen.generate(self._layout, self._calendar, horizon_s)
        self._material_gen.generate(self._layout, self._calendar, horizon_s)
        log.info("EventEngine initialized: %d events scheduled", self._calendar.size())

    def step(self, current_time: float) -> list[FactoryEvent]:
        """Process all events due at current_time. Returns processed events."""
        due = self._calendar.pop_due(current_time)
        for ev in due:
            cascades = self._dispatcher.dispatch(ev)
            for c in cascades:
                self._calendar.schedule(c)
        return due

    @property
    def calendar(self) -> EventCalendar:
        return self._calendar

    def event_stats(self) -> dict[str, int]:
        hist = self._calendar.history()
        counts: dict[str, int] = {}
        for ev in hist:
            key = ev.event_type.value
            counts[key] = counts.get(key, 0) + 1
        return counts
