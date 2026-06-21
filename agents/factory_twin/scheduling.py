"""
Section 4 — Scheduling Layer

Warstwa planowania produkcji:
  - JobQueue: kolejka zleceń per maszyna z wyborem reguły szeregowania
  - Dispatcher: przydzielanie job-ów do maszyn (dispatching)
  - SchedulingRules: FIFO, SPT, LPT, EDD, SLACK, CriticalRatio, Priority
  - SequenceDependentSetup: minimalizacja czasu przezbrojeń (TSP heuristic)
  - ShiftAwareScheduler: uwzględnia okna zmianowe i przerwy
  - SchedulePlan: wygenerowany plan z Gantt data
  - RealTimeRescheduler: reaguje na zdarzenia (awaria → przezbrojenienie)
  - CapacityPlanner: CRP (Capacity Requirements Planning) per maszyna
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .models import (
    FactoryLayout, ProductionOrder, ProductDefinition,
    Machine, Worker, RoutingStep,
    SchedulingRule, ShiftType, MachineStatus,
)
from .simulation_model import Job

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Job priority score (lower = higher priority = process first)
# ─────────────────────────────────────────────────────────────────────────────

def _job_score(job: Job, rule: SchedulingRule, current_time_s: float, layout: FactoryLayout) -> float:
    order = layout.orders.get(job.order_id)
    step  = job.next_step

    if rule == SchedulingRule.FIFO:
        return job.arrived_at

    elif rule == SchedulingRule.SPT:
        return step.cycle_time_s if step else 0.0

    elif rule == SchedulingRule.LPT:
        return -(step.cycle_time_s if step else 0.0)

    elif rule == SchedulingRule.EDD:
        if order:
            return order.due_date.timestamp()
        return float("inf")

    elif rule == SchedulingRule.SLACK:
        if order and step:
            remaining_time_s = step.cycle_time_s * max(order.remaining_qty, 1)
            slack = (order.due_date.timestamp() - datetime.now(timezone.utc).timestamp()) - remaining_time_s
            return slack  # lower slack → higher priority (process first)
        return 0.0

    elif rule == SchedulingRule.CRITICAL_RATIO:
        if order and step:
            time_to_due = max((order.due_date.timestamp() - datetime.now(timezone.utc).timestamp()), 0.001)
            remaining_s = step.cycle_time_s * max(order.remaining_qty, 1)
            return time_to_due / max(remaining_s, 0.001)
        return 1.0

    elif rule == SchedulingRule.PRIORITY:
        return order.priority if order else 5

    return job.arrived_at


# ─────────────────────────────────────────────────────────────────────────────
# Job Queue (per machine)
# ─────────────────────────────────────────────────────────────────────────────

class JobQueue:
    """
    Priority queue of jobs waiting for a specific machine.
    Supports dynamic re-ordering when rule or due dates change.
    """

    def __init__(self, machine_id: str, rule: SchedulingRule = SchedulingRule.FIFO) -> None:
        self._machine_id = machine_id
        self._rule       = rule
        self._jobs:  list[Job] = []

    def push(self, job: Job) -> None:
        self._jobs.append(job)

    def pop(self, current_time_s: float, layout: FactoryLayout) -> Job | None:
        if not self._jobs:
            return None
        self._jobs.sort(key=lambda j: _job_score(j, self._rule, current_time_s, layout))
        return self._jobs.pop(0)

    def peek(self, current_time_s: float, layout: FactoryLayout) -> Job | None:
        if not self._jobs:
            return None
        self._jobs.sort(key=lambda j: _job_score(j, self._rule, current_time_s, layout))
        return self._jobs[0]

    def remove(self, job_id: str) -> bool:
        before = len(self._jobs)
        self._jobs = [j for j in self._jobs if j.job_id != job_id]
        return len(self._jobs) < before

    def change_rule(self, new_rule: SchedulingRule) -> None:
        self._rule = new_rule

    def __len__(self) -> int:
        return len(self._jobs)

    def as_list(self, current_time_s: float, layout: FactoryLayout) -> list[Job]:
        self._jobs.sort(key=lambda j: _job_score(j, self._rule, current_time_s, layout))
        return list(self._jobs)


# ─────────────────────────────────────────────────────────────────────────────
# Sequence-dependent setup minimizer (nearest-neighbor TSP heuristic)
# ─────────────────────────────────────────────────────────────────────────────

class SetupOptimizer:
    """
    Given a list of jobs and a machine with sequence-dependent setup matrix,
    reorders jobs to minimize total setup time (nearest-neighbor heuristic).
    """

    def optimize(self, jobs: list[Job], machine: Machine) -> list[Job]:
        if len(jobs) <= 1:
            return jobs

        matrix = machine.setup_matrix
        current_family = machine.current_family or ""
        unvisited = list(jobs)
        ordered: list[Job] = []

        while unvisited:
            # Find next job with minimum setup from current family
            best_job  = None
            best_time = float("inf")
            for j in unvisited:
                st = matrix.get(current_family, j.family)
                if st < best_time:
                    best_time = st
                    best_job  = j
            if best_job:
                ordered.append(best_job)
                current_family = best_job.family
                unvisited.remove(best_job)

        return ordered

    def total_setup_time(self, jobs: list[Job], machine: Machine) -> float:
        total = 0.0
        prev_family = machine.current_family or ""
        for j in jobs:
            total += machine.setup_matrix.get(prev_family, j.family)
            prev_family = j.family
        return total


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────

class Dispatcher:
    """
    Central dispatcher: assigns jobs from pending pool to machine queues.
    Respects routing (job goes to machine specified in its next routing step).
    """

    def __init__(self, layout: FactoryLayout, rule: SchedulingRule = SchedulingRule.FIFO) -> None:
        self._layout  = layout
        self._rule    = rule
        self._queues: dict[str, JobQueue] = {
            mid: JobQueue(mid, rule) for mid in layout.machines
        }
        self._setup_opt = SetupOptimizer()

    def dispatch(self, jobs: list[Job], current_time_s: float) -> dict[str, list[Job]]:
        """
        Assign each job to the correct machine queue based on its next routing step.
        Returns {machine_id → newly assigned jobs}.
        """
        assigned: dict[str, list[Job]] = defaultdict(list)

        for job in jobs:
            step = job.next_step
            if not step:
                continue
            mid = step.machine_id
            if mid in self._queues:
                self._queues[mid].push(job)
                assigned[mid].append(job)

        return dict(assigned)

    def next_job_for(self, machine_id: str, current_time_s: float) -> Job | None:
        q = self._queues.get(machine_id)
        if not q:
            return None
        return q.pop(current_time_s, self._layout)

    def queue_length(self, machine_id: str) -> int:
        q = self._queues.get(machine_id)
        return len(q) if q else 0

    def all_queue_lengths(self) -> dict[str, int]:
        return {mid: len(q) for mid, q in self._queues.items()}

    def optimize_setup_sequence(self, machine_id: str, current_time_s: float) -> None:
        """Reorder queue using nearest-neighbor setup minimization."""
        q = self._queues.get(machine_id)
        machine = self._layout.machines.get(machine_id)
        if not q or not machine:
            return
        jobs = q.as_list(current_time_s, self._layout)
        optimized = self._setup_opt.optimize(jobs, machine)
        q._jobs = optimized

    def change_rule(self, new_rule: SchedulingRule) -> None:
        self._rule = new_rule
        for q in self._queues.values():
            q.change_rule(new_rule)


# ─────────────────────────────────────────────────────────────────────────────
# Schedule Plan (static schedule)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScheduledOperation:
    job_id:     str
    order_id:   str
    machine_id: str
    step_seq:   int
    start_time: float   # sim-seconds
    end_time:   float
    setup_time: float = 0.0
    family:     str   = ""


@dataclass
class SchedulePlan:
    operations:      list[ScheduledOperation] = field(default_factory=list)
    makespan_h:      float = 0.0
    total_setup_s:   float = 0.0
    tardiness_s:     float = 0.0
    machine_util:    dict[str, float] = field(default_factory=dict)
    gantt:           list[dict[str, Any]] = field(default_factory=list)


class StaticScheduler:
    """
    Generates a static (offline) schedule for a set of orders.
    Uses a simple list-scheduling algorithm (greedy, rule-based).
    """

    def schedule(
        self,
        layout:  FactoryLayout,
        rule:    SchedulingRule = SchedulingRule.EDD,
        horizon_s: float = 28800,
    ) -> SchedulePlan:
        from datetime import timezone

        # Build job list
        jobs: list[Job] = []
        jseq = 0
        for oid, order in layout.orders.items():
            prod = layout.products.get(order.product_id)
            if not prod:
                continue
            for i in range(order.quantity):
                jseq += 1
                jobs.append(Job(
                    job_id    = f"sched_{oid}_{jseq:04d}",
                    order_id  = oid,
                    product_id= order.product_id,
                    routing   = list(prod.routing),
                    arrived_at= 0.0,
                    family    = prod.family,
                ))

        # Sort jobs by rule
        def sort_key(j: Job) -> Any:
            order = layout.orders.get(j.order_id)
            if rule == SchedulingRule.EDD and order:
                return order.due_date.timestamp()
            elif rule == SchedulingRule.PRIORITY and order:
                return order.priority
            return 0

        jobs.sort(key=sort_key)

        # Machine availability tracker: machine_id → earliest available time
        machine_avail: dict[str, float] = {mid: 0.0 for mid in layout.machines}
        # Job completion tracker: job_id → step completion time
        job_step_done: dict[str, float] = {}

        operations: list[ScheduledOperation] = []
        total_setup = 0.0

        for job in jobs:
            prev_done = 0.0
            for step in job.routing:
                mid     = step.machine_id
                machine = layout.machines.get(mid)
                if not machine:
                    continue

                start = max(machine_avail.get(mid, 0.0), prev_done)

                # Setup time
                setup = machine.setup_matrix.get(machine.current_family or "", job.family, step.setup_time_s)
                total_setup += setup

                end = start + setup + step.cycle_time_s

                operations.append(ScheduledOperation(
                    job_id     = job.job_id,
                    order_id   = job.order_id,
                    machine_id = mid,
                    step_seq   = step.sequence,
                    start_time = start,
                    end_time   = end,
                    setup_time = setup,
                    family     = job.family,
                ))

                machine_avail[mid]        = end
                machine.current_family    = job.family
                prev_done                 = end
            job_step_done[job.job_id] = prev_done

        makespan  = max((op.end_time for op in operations), default=0.0)
        tardiness = 0.0
        for job in jobs:
            order = layout.orders.get(job.order_id)
            if order and job.job_id in job_step_done:
                due_s = order.due_date.timestamp() - datetime.now(timezone.utc).timestamp()
                late  = max(0.0, job_step_done[job.job_id] - due_s)
                tardiness += late

        # Machine utilization
        machine_util: dict[str, float] = {}
        for mid in layout.machines:
            ops = [op for op in operations if op.machine_id == mid]
            busy = sum(op.end_time - op.start_time - op.setup_time for op in ops)
            machine_util[mid] = round(busy / max(makespan, 1.0) * 100, 2)

        gantt = [
            {
                "job_id":     op.job_id,
                "order_id":   op.order_id,
                "machine_id": op.machine_id,
                "step":       op.step_seq,
                "start_h":    round(op.start_time / 3600, 3),
                "end_h":      round(op.end_time / 3600, 3),
                "setup_h":    round(op.setup_time / 3600, 3),
                "family":     op.family,
            }
            for op in operations[:2000]
        ]

        return SchedulePlan(
            operations   = operations,
            makespan_h   = round(makespan / 3600, 2),
            total_setup_s = round(total_setup, 1),
            tardiness_s  = round(tardiness, 1),
            machine_util = machine_util,
            gantt        = gantt,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Real-time rescheduler
# ─────────────────────────────────────────────────────────────────────────────

class RealTimeRescheduler:
    """
    Reacts to events (machine breakdown, urgent order) by:
    1. Rerouting jobs from broken machine to alternatives
    2. Boosting priority of late orders
    3. Splitting orders across parallel machines
    """

    def __init__(self, layout: FactoryLayout, dispatcher: Dispatcher) -> None:
        self._layout     = layout
        self._dispatcher = dispatcher

    def on_breakdown(self, machine_id: str, current_time_s: float) -> list[str]:
        """
        Find alternative machines for the same process type.
        Reroute waiting jobs to alternatives.
        Returns list of rerouted job_ids.
        """
        broken = self._layout.machines.get(machine_id)
        if not broken:
            return []

        # Find alternatives: same machine_type in same or adjacent cell
        alts = [
            mid for mid, m in self._layout.machines.items()
            if m.machine_type == broken.machine_type and mid != machine_id
            and m.status not in {MachineStatus.BREAKDOWN, MachineStatus.OFFLINE}
        ]
        if not alts:
            log.warning("No alternative for broken machine %s (%s)", machine_id, broken.machine_type)
            return []

        # Move queued jobs to best alternative (shortest queue)
        q = self._dispatcher._queues.get(machine_id)
        if not q:
            return []

        rerouted: list[str] = []
        jobs_to_move = list(q._jobs)
        q._jobs = []

        for job in jobs_to_move:
            best_alt = min(alts, key=lambda m: self._dispatcher.queue_length(m))
            self._dispatcher._queues[best_alt].push(job)
            # Update routing step to point to new machine
            step = job.next_step
            if step:
                step.machine_id = best_alt
            rerouted.append(job.job_id)
            log.info("Rerouted job %s from %s to %s", job.job_id, machine_id, best_alt)

        return rerouted

    def boost_late_orders(self, current_time_s: float) -> list[str]:
        """Set priority = 1 for any order past its due date."""
        boosted: list[str] = []
        from datetime import timezone
        now_ts = datetime.now(timezone.utc).timestamp()
        for oid, order in self._layout.orders.items():
            if order.due_date.timestamp() < now_ts and order.status == "in_progress":
                order.priority = 1
                boosted.append(oid)
        return boosted

    def split_order(self, order_id: str, machine_ids: list[str]) -> bool:
        """Distribute remaining jobs of an order across multiple machines (load balancing)."""
        order = self._layout.orders.get(order_id)
        if not order or not machine_ids:
            return False
        # Find all queued jobs for this order across all queues
        all_jobs: list[Job] = []
        for q in self._dispatcher._queues.values():
            all_jobs.extend([j for j in q._jobs if j.order_id == order_id])

        # Round-robin assign
        for i, job in enumerate(all_jobs):
            target = machine_ids[i % len(machine_ids)]
            for q in self._dispatcher._queues.values():
                q.remove(job.job_id)
            self._dispatcher._queues[target].push(job)
            if job.next_step:
                job.next_step.machine_id = target

        return True


# ─────────────────────────────────────────────────────────────────────────────
# Capacity Requirements Planning (CRP)
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CapacityLoad:
    machine_id:     str
    required_h:     float = 0.0   # hours needed to process all orders
    available_h:    float = 0.0   # shift hours available
    load_pct:       float = 0.0   # required / available × 100
    overloaded:     bool  = False


class CapacityPlanner:
    """
    CRP: calculates capacity load per machine for a given set of orders.
    Identifies overloaded machines before simulation starts.
    """

    def plan(self, layout: FactoryLayout, horizon_hours: float = 8.0) -> list[CapacityLoad]:
        required: dict[str, float] = defaultdict(float)

        for oid, order in layout.orders.items():
            prod = layout.products.get(order.product_id)
            if not prod:
                continue
            qty = order.quantity - order.completed_qty
            for step in prod.routing:
                required[step.machine_id] += (step.cycle_time_s + step.setup_time_s) * qty / 3600.0

        loads: list[CapacityLoad] = []
        for mid, req_h in required.items():
            machine = layout.machines.get(mid)
            avail_h = horizon_hours * (machine.parallel_slots if machine else 1)
            load_pct = req_h / max(avail_h, 0.001) * 100

            loads.append(CapacityLoad(
                machine_id  = mid,
                required_h  = round(req_h, 2),
                available_h = round(avail_h, 2),
                load_pct    = round(load_pct, 1),
                overloaded  = load_pct > 100,
            ))

        loads.sort(key=lambda l: l.load_pct, reverse=True)
        return loads
