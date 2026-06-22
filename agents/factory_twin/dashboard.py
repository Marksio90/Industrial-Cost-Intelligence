"""
Section 6 — Dashboard

Builders widgetów dla factory digital twin:
  - build_factory_map()        → mapa fabryki (węzły + krawędzie + status kolorami)
  - build_oee_tiles()          → kafelki OEE per maszyna (Availability × Performance × Quality)
  - build_gantt_chart()        → wykres Gantt (job × maszyna × czas)
  - build_throughput_chart()   → przepustowość w czasie (timeseries)
  - build_wip_chart()          → WIP po buforach (słupkowy)
  - build_energy_chart()       → energia per maszyna + taryfa godzinowa
  - build_breakdown_timeline() → oś czasu awarii (event log)
  - build_queue_heatmap()      → heatmapa długości kolejek (maszyna × czas)
  - build_utilization_bar()    → wykres słupkowy utilizacji (maszyna → processing / setup / breakdown)
  - build_kpi_summary()        → tabela KPI fabryki
  - build_shift_gantt()        → Gantt zmianowy pracowników
  - build_bottleneck_radar()   → radar identyfikacji wąskich gardeł
  - DashboardAssembler         → kompletna odpowiedź
"""
from __future__ import annotations

from typing import Any

from .models import FactoryKPI, MachineKPI, SimulationResult
from .factory_graph import FactoryGraph


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _r(v: float, n: int = 2) -> float:
    return round(v, n)


def _oee_color(oee: float) -> str:
    if oee >= 85:
        return "#4CAF50"   # green — world class
    elif oee >= 65:
        return "#FFC107"   # amber — acceptable
    elif oee >= 45:
        return "#FF9800"   # orange — poor
    return "#F44336"       # red — critical


def _util_color(util: float) -> str:
    if util >= 90:
        return "#F44336"   # overloaded
    elif util >= 70:
        return "#4CAF50"   # healthy
    elif util >= 40:
        return "#FFC107"   # underutilized
    return "#9E9E9E"       # idle


# ─────────────────────────────────────────────────────────────────────────────
# Factory Map (graph visualization)
# ─────────────────────────────────────────────────────────────────────────────

def build_factory_map(graph: FactoryGraph) -> dict[str, Any]:
    """
    Returns node/edge data for D3 / Cytoscape / React Flow rendering.
    Colors encode utilization: green < 70% < amber < 90% < red.
    """
    nodes = []
    for n in graph.nodes():
        color = _util_color(n.utilization * 100) if n.node_type == "machine" else n.color
        nodes.append({
            "id":          n.node_id,
            "label":       n.label,
            "type":        n.node_type,
            "cell":        n.cell_id,
            "x":           _r(n.x),
            "y":           _r(n.y),
            "utilization": _r(n.utilization * 100),
            "queue":       n.queue_length,
            "status":      n.status,
            "color":       color,
            "shape":       n.shape,
        })

    edges = []
    for e in graph.edges():
        edges.append({
            "from":       e.from_id,
            "to":         e.to_id,
            "type":       e.edge_type,
            "distance_m": _r(e.distance_m),
            "flow_rate":  _r(e.flow_rate),
            "utilization": _r(e.utilization * 100),
        })

    stats = graph.stats()
    return {
        "type":      "factory_map",
        "nodes":     nodes,
        "edges":     edges,
        "bottleneck": stats.get("bottleneck"),
        "spf":        stats.get("spf", []),
        "cell_util":  {k: _r(v * 100) for k, v in stats.get("cell_util", {}).items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# OEE Tiles
# ─────────────────────────────────────────────────────────────────────────────

def build_oee_tiles(machine_kpis: dict[str, MachineKPI]) -> list[dict[str, Any]]:
    """One tile per machine with OEE breakdown and color coding."""
    tiles = []
    for mid, kpi in sorted(machine_kpis.items(), key=lambda x: x[1].oee, reverse=True):
        tiles.append({
            "machine_id":     mid,
            "oee":            _r(kpi.oee),
            "availability":   _r(kpi.availability_pct),
            "performance":    _r(kpi.performance_pct),
            "quality":        _r(kpi.quality_pct),
            "utilization":    _r(kpi.utilization_pct),
            "throughput_h":   _r(kpi.throughput_h),
            "breakdowns":     kpi.breakdown_count,
            "downtime_h":     _r(kpi.total_downtime_h),
            "energy_kwh":     _r(kpi.energy_kwh),
            "defects":        kpi.defect_count,
            "color":          _oee_color(kpi.oee),
            "starved_pct":    _r(kpi.starved_pct),
            "blocked_pct":    _r(kpi.blocked_pct),
        })
    return tiles


# ─────────────────────────────────────────────────────────────────────────────
# Gantt Chart
# ─────────────────────────────────────────────────────────────────────────────

def build_gantt_chart(gantt_data: list[dict[str, Any]], max_rows: int = 500) -> dict[str, Any]:
    """
    Gantt chart data: list of bars {machine, job, start_h, end_h, scrapped}.
    Grouped by machine_id for row assignment.
    """
    machines = sorted({r["machine_id"] for r in gantt_data})
    machine_idx = {m: i for i, m in enumerate(machines)}

    bars = []
    for row in gantt_data[:max_rows]:
        bars.append({
            "row":     machine_idx.get(row["machine_id"], 0),
            "machine": row["machine_id"],
            "job_id":  row["job_id"],
            "start_h": _r(row["start_h"]),
            "end_h":   _r(row["end_h"]),
            "scrapped": row.get("scrapped", False),
            "color":   "#F44336" if row.get("scrapped") else "#2196F3",
        })

    return {
        "type":     "gantt",
        "machines": machines,
        "bars":     bars,
        "time_unit": "hours",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Throughput Timeseries
# ─────────────────────────────────────────────────────────────────────────────

def build_throughput_chart(kpi_timeseries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type":       "line",
        "x_label":    "Czas [h]",
        "y_label":    "Przepustowość [szt]",
        "series": {
            "throughput": {
                "x": [r["t_h"]      for r in kpi_timeseries],
                "y": [r["throughput"] for r in kpi_timeseries],
            },
            "wip": {
                "x": [r["t_h"] for r in kpi_timeseries],
                "y": [r["wip"]  for r in kpi_timeseries],
            },
        },
    }


def build_oee_timeseries(kpi_timeseries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "type":    "line",
        "x_label": "Czas [h]",
        "y_label": "OEE [%]",
        "series": {
            "avg_oee": {
                "x": [r["t_h"]     for r in kpi_timeseries],
                "y": [_r(r["avg_oee"] * 100) for r in kpi_timeseries],
            },
        },
        "reference_lines": [
            {"y": 85, "label": "World Class", "color": "#4CAF50"},
            {"y": 65, "label": "Acceptable",  "color": "#FFC107"},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# WIP by Buffer
# ─────────────────────────────────────────────────────────────────────────────

def build_wip_chart(wip_by_buffer: dict[str, int]) -> dict[str, Any]:
    sorted_items = sorted(wip_by_buffer.items(), key=lambda x: x[1], reverse=True)
    return {
        "type":     "bar",
        "x_label":  "Bufor",
        "y_label":  "WIP [szt]",
        "labels":   [k for k, _ in sorted_items],
        "values":   [v for _, v in sorted_items],
        "colors":   ["#F44336" if v > 80 else "#FFC107" if v > 40 else "#4CAF50"
                     for _, v in sorted_items],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Energy Chart
# ─────────────────────────────────────────────────────────────────────────────

def build_energy_chart(
    machine_kpis: dict[str, MachineKPI],
    kpi_timeseries: list[dict[str, Any]],
) -> dict[str, Any]:
    # Per-machine energy bar
    machines  = sorted(machine_kpis, key=lambda m: machine_kpis[m].energy_kwh, reverse=True)
    kwh_vals  = [_r(machine_kpis[m].energy_kwh) for m in machines]

    # Energy timeseries
    energy_ts = {
        "x": [r["t_h"]        for r in kpi_timeseries],
        "y": [_r(r["energy_kwh"]) for r in kpi_timeseries],
    }

    return {
        "type": "energy",
        "bar": {
            "labels": machines,
            "values": kwh_vals,
            "y_label": "Energia [kWh]",
        },
        "timeseries": energy_ts,
        "total_kwh": _r(sum(kwh_vals)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Breakdown Timeline
# ─────────────────────────────────────────────────────────────────────────────

def build_breakdown_timeline(event_log: list[dict[str, Any]]) -> dict[str, Any]:
    """Event log filtered to machine events, formatted for timeline view."""
    machine_events = [
        ev for ev in event_log
        if ev.get("type") in {
            "machine_breakdown", "machine_repair",
            "maintenance_start", "maintenance_done",
            "quality_alarm", "spc_violation",
        }
    ]

    return {
        "type":   "timeline",
        "events": [
            {
                "t_h":      ev["t_h"],
                "type":     ev["type"],
                "machine":  ev["entity_id"],
                "color":    "#F44336" if "breakdown" in ev["type"] or "alarm" in ev["type"]
                            else "#FF9800" if "maintenance" in ev["type"]
                            else "#2196F3",
            }
            for ev in machine_events[:1000]
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Utilization Stacked Bar
# ─────────────────────────────────────────────────────────────────────────────

def build_utilization_bar(machine_kpis: dict[str, MachineKPI]) -> dict[str, Any]:
    """Stacked bar: processing / setup / breakdown / starved / blocked / idle per machine."""
    machines = sorted(machine_kpis.keys())
    total    = 100.0

    def remainder(kpi: MachineKPI) -> float:
        used = kpi.utilization_pct + kpi.starved_pct + kpi.blocked_pct + (100 - kpi.availability_pct)
        return max(0.0, total - used)

    return {
        "type":     "bar_stacked",
        "machines": machines,
        "series": {
            "Przetwarzanie": [_r(machine_kpis[m].utilization_pct)           for m in machines],
            "Awarie":        [_r(100 - machine_kpis[m].availability_pct)    for m in machines],
            "Oczekiwanie":   [_r(machine_kpis[m].starved_pct)               for m in machines],
            "Blokowanie":    [_r(machine_kpis[m].blocked_pct)               for m in machines],
            "Bezczynność":   [_r(remainder(machine_kpis[m]))                for m in machines],
        },
        "colors": ["#4CAF50", "#F44336", "#FFC107", "#FF9800", "#9E9E9E"],
    }


# ─────────────────────────────────────────────────────────────────────────────
# KPI Summary
# ─────────────────────────────────────────────────────────────────────────────

def build_kpi_summary(kpi: FactoryKPI) -> dict[str, Any]:
    return {
        "type":  "kpi_summary",
        "tiles": [
            {"id": "throughput",   "label": "Przepustowość",   "value": kpi.throughput_total,             "unit": "szt",   "color": "#1976D2"},
            {"id": "throughput_h", "label": "Przepustowość/h", "value": _r(kpi.throughput_h),             "unit": "szt/h", "color": "#1565C0"},
            {"id": "oee",          "label": "OEE średnia",     "value": _r(kpi.avg_oee),                  "unit": "%",     "color": _oee_color(kpi.avg_oee)},
            {"id": "wip",          "label": "Średni WIP",      "value": _r(kpi.avg_wip),                  "unit": "szt",   "color": "#F57F17"},
            {"id": "lead_time",    "label": "Lead time avg",   "value": _r(kpi.avg_lead_time_h),          "unit": "h",     "color": "#6A1B9A"},
            {"id": "otd",          "label": "On-Time Delivery","value": _r(kpi.on_time_delivery),         "unit": "%",     "color": "#2E7D32" if kpi.on_time_delivery >= 95 else "#F44336"},
            {"id": "energy",       "label": "Energia",         "value": _r(kpi.total_energy_kwh),         "unit": "kWh",   "color": "#E65100"},
            {"id": "energy_cost",  "label": "Koszt energii",   "value": _r(kpi.energy_cost_eur),          "unit": "EUR",   "color": "#BF360C"},
            {"id": "scrap",        "label": "Wskaźnik braków", "value": _r(kpi.scrap_rate),               "unit": "%",     "color": "#B71C1C" if kpi.scrap_rate > 2 else "#388E3C"},
            {"id": "bottleneck",   "label": "Wąskie gardło",   "value": kpi.bottleneck_id or "brak",      "unit": "",      "color": "#F44336"},
            {"id": "late_orders",  "label": "Spóźnione zlecenia","value": kpi.late_orders,               "unit": "szt",   "color": "#C62828" if kpi.late_orders > 0 else "#2E7D32"},
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bottleneck Radar
# ─────────────────────────────────────────────────────────────────────────────

def build_bottleneck_radar(
    machine_kpis: dict[str, MachineKPI],
    top_n: int = 6,
) -> dict[str, Any]:
    """Spider chart: top-N machines by utilization — 5 axes per machine."""
    top = sorted(machine_kpis.values(), key=lambda k: k.utilization_pct, reverse=True)[:top_n]
    return {
        "type":     "radar",
        "machines": [k.machine_id for k in top],
        "axes":     ["Utilizacja", "OEE", "Jakość", "Dostępność", "Wydajność"],
        "series": [
            {
                "machine": k.machine_id,
                "values":  [
                    _r(k.utilization_pct),
                    _r(k.oee),
                    _r(k.quality_pct),
                    _r(k.availability_pct),
                    _r(k.performance_pct),
                ],
            }
            for k in top
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Queue Heatmap
# ─────────────────────────────────────────────────────────────────────────────

def build_queue_heatmap(
    event_log: list[dict[str, Any]],
    machines:  list[str],
    time_buckets: int = 20,
    horizon_h: float = 8.0,
) -> dict[str, Any]:
    """
    Heatmap: machine × time bucket → avg queue length.
    Built from event log.
    """
    bucket_size = horizon_h / time_buckets
    # Initialize
    heat: dict[str, list[int]] = {m: [0] * time_buckets for m in machines}

    for ev in event_log:
        if ev.get("type") == "job_start":
            mid = ev.get("entity_id", "")
            if mid in heat:
                t_h    = ev.get("t_h", 0.0)
                bucket = min(int(t_h / bucket_size), time_buckets - 1)
                heat[mid][bucket] += 1

    time_labels = [_r(i * bucket_size, 1) for i in range(time_buckets)]

    return {
        "type":         "heatmap",
        "x_labels":     time_labels,
        "y_labels":     machines,
        "matrix":       [heat[m] for m in machines],
        "color_scale":  "RdYlGn_r",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard Assembler
# ─────────────────────────────────────────────────────────────────────────────

class DashboardAssembler:
    """
    Assembles a complete factory twin dashboard payload from a SimulationResult.
    """

    def build(
        self,
        result: SimulationResult,
        graph:  FactoryGraph | None = None,
    ) -> dict[str, Any]:
        kpi = result.factory_kpi

        dashboard: dict[str, Any] = {
            "kpi_summary":        build_kpi_summary(kpi),
            "oee_tiles":          build_oee_tiles(kpi.machine_kpis),
            "utilization_bar":    build_utilization_bar(kpi.machine_kpis),
            "throughput_chart":   build_throughput_chart(result.kpi_timeseries),
            "oee_timeseries":     build_oee_timeseries(result.kpi_timeseries),
            "wip_chart":          build_wip_chart(kpi.wip_by_buffer),
            "energy_chart":       build_energy_chart(kpi.machine_kpis, result.kpi_timeseries),
            "gantt_chart":        build_gantt_chart(result.gantt_data),
            "breakdown_timeline": build_breakdown_timeline(result.event_log),
            "bottleneck_radar":   build_bottleneck_radar(kpi.machine_kpis),
            "queue_heatmap":      build_queue_heatmap(
                result.event_log,
                list(kpi.machine_kpis.keys()),
                horizon_h=result.config.horizon_hours,
            ),
        }

        if graph:
            dashboard["factory_map"] = build_factory_map(graph)

        return dashboard
