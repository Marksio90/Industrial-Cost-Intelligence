"""
Section 2 — Simulation Model

Dyskretny symulator fabryki (DES — Discrete Event Simulation):
  - SimulationClock: zegar symulacji (sekunda = tik)
  - MachineSimulator: symulacja cyklu maszyny (cycle, setup, breakdown, repair)
  - WorkerSimulator: symulacja pracownika (praca, przerwa, zmiana, zmęczenie)
  - BufferSimulator: symulacja bufora (produkcja WIP, blokowanie)
  - EnergySimulator: zużycie energii per maszyna i per tik
  - TransportSimulator: AGV / wózki (czas przejazdu, kolejka zadań)
  - FactorySimulator: orchestracja — krok po kroku przez czas symulacji
"""
from __future__ import annotations

import logging
import math
import random
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .models import (
    FactoryLayout, Machine, Worker, Buffer, TransportUnit,
    ProductionOrder, ProductDefinition, RoutingStep,
    MachineStatus, WorkerStatus, BufferType,
    SimulationConfig, SimulationResult, FactoryKPI, MachineKPI,
    FailureDistribution, ShiftType, EventType,
)
from .factory_graph import FactoryGraph, FactoryGraphBuilder

log = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Job (work item flowing through the factory)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Job:
    job_id:         str
    order_id:       str
    product_id:     str
    routing:        list[RoutingStep]
    current_step:   int = 0
    arrived_at:     float = 0.0   # sim time (seconds)
    completed_at:   float = -1.0
    scrapped:       bool  = False
    rework_count:   int   = 0
    family:         str   = ""

    @property
    def is_done(self) -> bool:
        return self.current_step >= len(self.routing) or self.scrapped

    @property
    def next_step(self) -> RoutingStep | None:
        if self.current_step < len(self.routing):
            return self.routing[self.current_step]
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Failure sampling
# ─────────────────────────────────────────────────────────────────────────────

def sample_ttf(machine: Machine, rng: random.Random) -> float:
    """Sample time-to-failure in seconds."""
    fm = machine.failure_model
    if fm.distribution == FailureDistribution.EXPONENTIAL:
        return rng.expovariate(1.0 / (fm.mtbf_hours * 3600))
    elif fm.distribution == FailureDistribution.WEIBULL:
        # Weibull via inverse CDF: (-ln(U))^(1/β) × η
        # η (scale) = mtbf / Γ(1+1/β), approximated
        beta = fm.weibull_shape
        scale = fm.mtbf_hours * 3600 / math.gamma(1 + 1 / beta)
        u = rng.random()
        return scale * (-math.log(max(u, 1e-12))) ** (1 / beta)
    elif fm.distribution == FailureDistribution.NORMAL:
        return max(0.0, rng.gauss(fm.mtbf_hours * 3600, fm.mtbf_hours * 3600 * 0.2))
    else:  # LOGNORMAL
        mu  = math.log(fm.mtbf_hours * 3600)
        sig = 0.3
        return math.exp(rng.gauss(mu, sig))


def sample_ttr(machine: Machine, rng: random.Random) -> float:
    """Sample repair time in seconds (lognormal around MTTR)."""
    mu  = math.log(max(machine.failure_model.mttr_hours * 3600, 1.0))
    sig = 0.4
    return math.exp(rng.gauss(mu, sig))


# ─────────────────────────────────────────────────────────────────────────────
# Per-machine state tracker
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MachineState:
    machine_id: str
    status:     MachineStatus = MachineStatus.IDLE
    # Current job processing
    current_job: Job | None = None
    job_end_time: float = 0.0          # sim-seconds when job finishes
    # Setup
    setup_end_time: float = 0.0
    # Failure scheduling
    next_failure_time: float = float("inf")
    repair_end_time:   float = 0.0
    # Maintenance
    next_maintenance_time: float = float("inf")
    # Accumulators
    time_processing: float = 0.0
    time_idle:       float = 0.0
    time_setup:      float = 0.0
    time_breakdown:  float = 0.0
    time_starved:    float = 0.0
    time_blocked:    float = 0.0
    breakdown_count: int   = 0
    parts_produced:  int   = 0
    defects:         int   = 0
    energy_kwh:      float = 0.0
    # Queue
    queue: deque = field(default_factory=deque)

    @property
    def oee_components(self) -> tuple[float, float, float]:
        total = max(self.time_processing + self.time_idle + self.time_setup +
                    self.time_breakdown + self.time_starved + self.time_blocked, 1.0)
        avail = 1.0 - self.time_breakdown / total
        perf  = self.time_processing / max(self.time_processing + self.time_idle +
                                            self.time_starved + self.time_blocked, 1.0)
        qual  = 1.0 - (self.defects / max(self.parts_produced, 1))
        return avail, perf, qual


# ─────────────────────────────────────────────────────────────────────────────
# Factory Simulator (main engine)
# ─────────────────────────────────────────────────────────────────────────────

class FactorySimulator:
    """
    Discrete-event simulator for the factory floor.

    Flow per tick (dt seconds):
      1. Release new jobs from production orders
      2. Process machine states (breakdown, setup, cycle, finish)
      3. Move finished jobs to next step
      4. Update worker fatigue + availability
      5. Update buffer levels
      6. Simulate transport
      7. Record energy
      8. Log events and KPI snapshot
    """

    def __init__(
        self,
        layout: FactoryLayout,
        config: SimulationConfig,
    ) -> None:
        self._layout  = layout
        self._config  = config
        self._rng     = random.Random(config.seed)
        self._graph   = FactoryGraphBuilder().build(layout)

        # Time
        self._t: float = 0.0
        self._dt: float = config.time_step_s

        # Job tracking
        self._jobs:    dict[str, Job]         = {}
        self._pending: list[Job]              = []   # not yet assigned
        self._done:    list[Job]              = []

        # Machine states
        self._mstate: dict[str, MachineState] = {}
        for mid in layout.machines:
            ms = MachineState(mid)
            m  = layout.machines[mid]
            ms.next_failure_time = sample_ttf(m, self._rng) if config.simulate_failures else float("inf")
            ms.next_maintenance_time = m.failure_model.mtbf_hours * 3600 * 0.8  # PM at 80% MTBF
            self._mstate[mid] = ms

        # Event log
        self._event_log: list[dict[str, Any]] = []
        # KPI timeseries (snapshot every 60 simulated seconds)
        self._kpi_ts:    list[dict[str, Any]] = []
        self._last_kpi_snap: float = 0.0

        # Job counter
        self._job_seq: int = 0

    # ── Job creation ──────────────────────────────────────────────────────────

    def _release_jobs(self) -> None:
        """Convert production orders into jobs at t=0 and as due dates approach."""
        for order_id, order in self._layout.orders.items():
            if order.status != "pending":
                continue
            prod = self._layout.products.get(order.product_id)
            if not prod:
                continue
            # Release at start or if order is overdue
            for i in range(order.quantity - order.completed_qty - len([
                j for j in self._pending if j.order_id == order_id
            ]) - len([
                j for j in self._jobs.values() if j.order_id == order_id and not j.is_done
            ])):
                self._job_seq += 1
                job = Job(
                    job_id    = f"job_{order_id}_{self._job_seq:04d}",
                    order_id  = order_id,
                    product_id= order.product_id,
                    routing   = list(prod.routing),
                    arrived_at= self._t,
                    family    = prod.family,
                )
                self._pending.append(job)
                self._jobs[job.job_id] = job
            order.status = "in_progress"

    # ── Machine processing ────────────────────────────────────────────────────

    def _process_machines(self) -> None:
        for mid, ms in self._mstate.items():
            machine = self._layout.machines[mid]
            t  = self._t
            dt = self._dt

            # ── Failure check ────────────────────────────────────────────────
            if self._config.simulate_failures and t >= ms.next_failure_time and ms.status not in {
                MachineStatus.BREAKDOWN, MachineStatus.MAINTENANCE, MachineStatus.OFFLINE
            }:
                # Breakdown!
                prev_job = ms.current_job
                ms.current_job = None
                ms.status = MachineStatus.BREAKDOWN
                ms.repair_end_time = t + sample_ttr(machine, self._rng)
                ms.breakdown_count += 1
                ms.next_failure_time = float("inf")
                self._log(EventType.MACHINE_BREAKDOWN, mid, {"repair_end": ms.repair_end_time})
                # Re-queue interrupted job
                if prev_job:
                    ms.queue.appendleft(prev_job)

            # ── Repair done ──────────────────────────────────────────────────
            elif ms.status == MachineStatus.BREAKDOWN and t >= ms.repair_end_time:
                ms.status = MachineStatus.IDLE
                ms.next_failure_time = t + sample_ttf(machine, self._rng)
                self._log(EventType.MACHINE_REPAIR, mid, {})

            # ── Scheduled maintenance ────────────────────────────────────────
            elif t >= ms.next_maintenance_time and ms.status == MachineStatus.IDLE:
                ms.status = MachineStatus.MAINTENANCE
                ms.repair_end_time = t + 1800  # 30 min PM
                ms.next_maintenance_time = t + machine.failure_model.mtbf_hours * 3600
                self._log(EventType.MAINTENANCE_START, mid, {})

            elif ms.status == MachineStatus.MAINTENANCE and t >= ms.repair_end_time:
                ms.status = MachineStatus.IDLE
                self._log(EventType.MAINTENANCE_DONE, mid, {})

            # ── Setup done ───────────────────────────────────────────────────
            elif ms.status == MachineStatus.SETUP and t >= ms.setup_end_time:
                ms.status = MachineStatus.IDLE

            # ── Job completion ───────────────────────────────────────────────
            elif ms.status == MachineStatus.PROCESSING and t >= ms.job_end_time:
                job = ms.current_job
                if job:
                    step = job.routing[job.current_step]
                    # Quality check
                    if self._rng.random() < machine.defect_rate:
                        ms.defects += 1
                        # Rework or scrap
                        if self._rng.random() < 0.6:  # 60% rework
                            job.rework_count += 1
                            ms.queue.append(job)
                            self._log(EventType.JOB_REWORK, mid, {"job_id": job.job_id})
                        else:
                            job.scrapped = True
                            self._done.append(job)
                            self._log(EventType.JOB_SCRAP, mid, {"job_id": job.job_id})
                    else:
                        ms.parts_produced += 1
                        job.current_step += 1
                        if job.is_done:
                            job.completed_at = t
                            self._done.append(job)
                            self._log(EventType.JOB_DONE, mid, {"job_id": job.job_id, "lead_time_h": (t - job.arrived_at) / 3600})
                            # Update order
                            order = self._layout.orders.get(job.order_id)
                            if order:
                                order.completed_qty += 1
                                order.remaining_qty  = max(0, order.remaining_qty - 1)
                        else:
                            # Move to next machine in routing
                            next_step = job.routing[job.current_step]
                            next_ms   = self._mstate.get(next_step.machine_id)
                            if next_ms:
                                next_ms.queue.append(job)
                    ms.current_job = None
                ms.status = MachineStatus.IDLE

            # ── Start new job ────────────────────────────────────────────────
            if ms.status == MachineStatus.IDLE and ms.queue:
                job = ms.queue.popleft()
                step = job.routing[job.current_step]

                # Setup check
                setup_needed = 0.0
                if machine.current_family and machine.current_family != job.family:
                    setup_needed = machine.setup_matrix.get(machine.current_family, job.family, step.setup_time_s)

                if setup_needed > 0:
                    ms.status = MachineStatus.SETUP
                    ms.setup_end_time = t + setup_needed
                    ms.queue.appendleft(job)  # re-add for after setup
                    self._log(EventType.MACHINE_SETUP, mid, {"setup_s": setup_needed})
                else:
                    ms.current_job = job
                    ms.status      = MachineStatus.PROCESSING
                    ms.job_end_time = t + step.cycle_time_s
                    machine.current_family = job.family
                    self._log(EventType.JOB_START, mid, {"job_id": job.job_id})

            elif ms.status == MachineStatus.IDLE and not ms.queue:
                # Check if there are pending jobs for this machine
                for j in list(self._pending):
                    if j.next_step and j.next_step.machine_id == mid:
                        self._pending.remove(j)
                        ms.queue.append(j)
                        break

            # ── Accumulate time ──────────────────────────────────────────────
            if ms.status == MachineStatus.PROCESSING:
                ms.time_processing += dt
            elif ms.status in {MachineStatus.IDLE}:
                if ms.queue:
                    ms.time_starved += dt  # actually waiting for assignment
                else:
                    ms.time_idle += dt
            elif ms.status == MachineStatus.SETUP:
                ms.time_setup += dt
            elif ms.status in {MachineStatus.BREAKDOWN, MachineStatus.MAINTENANCE}:
                ms.time_breakdown += dt
            elif ms.status == MachineStatus.BLOCKED:
                ms.time_blocked += dt

            # ── Energy ───────────────────────────────────────────────────────
            if self._config.simulate_energy:
                e_profile = machine.energy
                if ms.status == MachineStatus.PROCESSING:
                    kw = e_profile.processing_kw
                elif ms.status == MachineStatus.SETUP:
                    kw = e_profile.setup_kw
                elif ms.status in {MachineStatus.BREAKDOWN, MachineStatus.MAINTENANCE}:
                    kw = e_profile.breakdown_kw
                else:
                    kw = e_profile.idle_kw
                ms.energy_kwh += kw * (dt / 3600.0)

            # Sync graph node status
            self._graph.update_node_state(
                mid,
                utilization  = ms.time_processing / max(self._t, 1.0),
                queue        = len(ms.queue),
                status       = ms.status.value,
            )

    # ── Buffer update ─────────────────────────────────────────────────────────

    def _update_buffers(self) -> None:
        for buf_id, buf in self._layout.buffers.items():
            if buf.buffer_type == BufferType.WIP:
                # Count jobs waiting at machines feeding into or out of this buffer
                qty = sum(1 for j in self._jobs.values() if not j.is_done and not j.scrapped)
                buf.current_qty = min(qty, buf.capacity)
            self._graph.update_node_state(
                buf_id,
                utilization  = buf.fill_pct,
                queue        = buf.current_qty,
                status       = "full" if buf.is_full else "ok",
            )

    # ── Worker update ─────────────────────────────────────────────────────────

    def _update_workers(self) -> None:
        if not self._config.simulate_workers:
            return
        hours = self._t / 3600.0
        for worker in self._layout.workers.values():
            # Fatigue model: increases 5% per hour, resets on break
            worker.fatigue_factor = max(0.5, 1.0 - 0.05 * (hours % 8))
            # Shift end check (simplified: 8h window)
            if hours % 8 < self._dt / 3600.0:
                worker.hours_worked_today = 0.0

    # ── KPI snapshot ──────────────────────────────────────────────────────────

    def _snapshot_kpi(self) -> None:
        if self._t - self._last_kpi_snap < 60.0:
            return
        self._last_kpi_snap = self._t

        total_e   = sum(ms.energy_kwh for ms in self._mstate.values())
        total_pp  = sum(ms.parts_produced for ms in self._mstate.values())
        wip_total = sum(1 for j in self._jobs.values() if not j.is_done and not j.scrapped)
        utils     = [ms.time_processing / max(self._t - self._config.warm_up_hours * 3600, 1.0)
                     for ms in self._mstate.values()]
        avg_oee   = sum(utils) / len(utils) if utils else 0.0

        self._kpi_ts.append({
            "t_h":         round(self._t / 3600, 2),
            "throughput":  total_pp,
            "wip":         wip_total,
            "energy_kwh":  round(total_e, 2),
            "avg_oee":     round(avg_oee, 3),
        })

    # ── Event log ─────────────────────────────────────────────────────────────

    def _log(self, event_type: EventType, entity_id: str, data: dict[str, Any]) -> None:
        if len(self._event_log) > 100_000:  # cap
            return
        self._event_log.append({
            "t_s":       round(self._t, 1),
            "t_h":       round(self._t / 3600, 3),
            "type":      event_type.value,
            "entity_id": entity_id,
            **data,
        })

    # ── Main simulation loop ──────────────────────────────────────────────────

    def run(self) -> SimulationResult:
        wall_t0 = time.perf_counter()
        horizon = self._config.horizon_hours * 3600

        # Release all orders as jobs at t=0
        self._release_jobs()
        # Enqueue first step
        for job in list(self._pending):
            if job.next_step:
                ms = self._mstate.get(job.next_step.machine_id)
                if ms:
                    ms.queue.append(job)
                    self._pending.remove(job)

        while self._t <= horizon:
            self._process_machines()
            self._update_buffers()
            self._update_workers()
            self._snapshot_kpi()
            self._t += self._dt

        wall_time = time.perf_counter() - wall_t0
        kpi = self._build_kpi()
        gantt = self._build_gantt()

        log.info(
            "Simulation %s done in %.2fs: throughput=%d, OEE=%.1f%%",
            self._config.sim_id, wall_time, kpi.throughput_total, kpi.avg_oee
        )

        return SimulationResult(
            sim_id          = self._config.sim_id,
            config          = self._config,
            factory_kpi     = kpi,
            event_log       = self._event_log[-5000:],  # last 5k events
            gantt_data      = gantt,
            kpi_timeseries  = self._kpi_ts,
            run_time_s      = wall_time,
        )

    # ── KPI aggregation ───────────────────────────────────────────────────────

    def _build_kpi(self) -> FactoryKPI:
        effective_time = max(self._config.horizon_hours - self._config.warm_up_hours, 0.1) * 3600

        machine_kpis: dict[str, MachineKPI] = {}
        total_energy  = 0.0
        oee_list: list[float] = []

        for mid, ms in self._mstate.items():
            avail, perf, qual = ms.oee_components
            oee = avail * perf * qual * 100
            kpi_m = MachineKPI(
                machine_id       = mid,
                utilization_pct  = round(ms.time_processing / max(effective_time, 1.0) * 100, 2),
                availability_pct = round(avail * 100, 2),
                performance_pct  = round(perf * 100, 2),
                quality_pct      = round(qual * 100, 2),
                oee              = round(oee, 2),
                throughput_h     = round(ms.parts_produced / max(self._config.horizon_hours, 0.1), 2),
                starved_pct      = round(ms.time_starved / max(effective_time, 1.0) * 100, 2),
                blocked_pct      = round(ms.time_blocked / max(effective_time, 1.0) * 100, 2),
                breakdown_count  = ms.breakdown_count,
                total_downtime_h = round(ms.time_breakdown / 3600, 2),
                energy_kwh       = round(ms.energy_kwh, 2),
                defect_count     = ms.defects,
            )
            kpi_m.compute_oee()
            machine_kpis[mid] = kpi_m
            total_energy += ms.energy_kwh
            oee_list.append(oee)

        # Throughput
        done_good = [j for j in self._done if not j.scrapped]
        done_scrap = [j for j in self._done if j.scrapped]
        throughput = len(done_good)
        scrap_rate = len(done_scrap) / max(len(self._done), 1)

        # Lead times
        lead_times = [
            (j.completed_at - j.arrived_at) / 3600
            for j in done_good if j.completed_at > 0
        ]
        avg_lt = sum(lead_times) / len(lead_times) if lead_times else 0.0

        # Order lateness
        late = 0
        total_orders = len(self._layout.orders)
        end_datetime = datetime.now(timezone.utc) + timedelta(seconds=self._t)
        for order in self._layout.orders.values():
            if order.completed_qty < order.quantity and end_datetime > order.due_date:
                late += 1

        # Energy cost
        tariff_avg = sum(self._layout.energy_tariff.values()) / max(len(self._layout.energy_tariff), 1) if self._layout.energy_tariff else 0.15
        energy_cost = total_energy * tariff_avg

        avg_oee = sum(oee_list) / len(oee_list) if oee_list else 0.0
        bottleneck = max(machine_kpis, key=lambda m: machine_kpis[m].utilization_pct, default="")

        return FactoryKPI(
            simulation_id    = self._config.sim_id,
            sim_time_h       = round(self._config.horizon_hours, 2),
            throughput_total = throughput,
            throughput_h     = round(throughput / max(self._config.horizon_hours, 0.1), 2),
            makespan_h       = round(max(lead_times, default=0.0), 2),
            avg_wip          = round(sum(ms.queue_length for ms in self._mstate.values()) / max(len(self._mstate), 1), 1),
            avg_lead_time_h  = round(avg_lt, 2),
            total_energy_kwh = round(total_energy, 2),
            energy_cost_eur  = round(energy_cost, 2),
            avg_oee          = round(avg_oee, 2),
            machine_kpis     = machine_kpis,
            late_orders      = late,
            total_orders     = total_orders,
            on_time_delivery = round((1 - late / max(total_orders, 1)) * 100, 1),
            total_scrap      = len(done_scrap),
            scrap_rate       = round(scrap_rate * 100, 2),
            bottleneck_id    = bottleneck,
            wip_by_buffer    = {bid: b.current_qty for bid, b in self._layout.buffers.items()},
        )

    def _build_gantt(self) -> list[dict[str, Any]]:
        """Simplified Gantt: from event log extract JOB_START → JOB_DONE pairs."""
        starts: dict[str, dict] = {}
        gantt: list[dict[str, Any]] = []

        for ev in self._event_log:
            if ev["type"] == EventType.JOB_START.value:
                starts[ev.get("job_id", "")] = ev
            elif ev["type"] in {EventType.JOB_DONE.value, EventType.JOB_SCRAP.value}:
                jid = ev.get("job_id", "")
                start = starts.pop(jid, None)
                if start:
                    gantt.append({
                        "job_id":     jid,
                        "machine_id": ev["entity_id"],
                        "start_h":    start["t_h"],
                        "end_h":      ev["t_h"],
                        "scrapped":   ev["type"] == EventType.JOB_SCRAP.value,
                    })
        return gantt[:2000]

    @property
    def graph(self) -> FactoryGraph:
        return self._graph
