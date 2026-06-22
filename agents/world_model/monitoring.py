"""Prometheus metrics for the World Model."""
from __future__ import annotations
import time
from dataclasses import dataclass

try:
    from prometheus_client import Counter, Gauge, Histogram
    _FORECAST_RUNS = Counter("world_model_forecast_runs_total", "Forecast runs", ["product_id", "status"])
    _SIM_RUNS = Counter("world_model_simulation_runs_total", "MC simulation runs", ["scenario"])
    _SCENARIO_RUNS = Counter("world_model_scenario_runs_total", "Scenario planning runs", ["status"])
    _OPT_RUNS = Counter("world_model_optimization_runs_total", "Optimization runs", ["status"])
    _FORECAST_DURATION = Histogram("world_model_forecast_duration_ms", "Forecast latency", ["type"])
    _COST_DELTA_GAUGE = Gauge("world_model_cost_delta_pct", "Latest 12m cost delta %", ["product_id", "scenario"])
    _VAR_GAUGE = Gauge("world_model_var_95_12m", "VaR 95% at 12m", ["product_id"])
    _SPREAD_GAUGE = Gauge("world_model_scenario_spread_pct", "Scenario spread %", ["product_id"])
    _HEDGE_BENEFIT_GAUGE = Gauge("world_model_hedge_benefit_eur", "Hedge net benefit EUR", ["product_id"])
except ImportError:
    class _Stub:
        def labels(self, **kw): return self
        def inc(self, *a): pass
        def observe(self, *a): pass
        def set(self, *a): pass
    _FORECAST_RUNS = _Stub()
    _SIM_RUNS = _Stub()
    _SCENARIO_RUNS = _Stub()
    _OPT_RUNS = _Stub()
    _FORECAST_DURATION = _Stub()
    _COST_DELTA_GAUGE = _Stub()
    _VAR_GAUGE = _Stub()
    _SPREAD_GAUGE = _Stub()
    _HEDGE_BENEFIT_GAUGE = _Stub()


@dataclass
class WorldModelHealthSnapshot:
    total_forecast_runs: int = 0
    total_sim_runs: int = 0
    total_opt_runs: int = 0
    avg_forecast_ms: float = 0.0
    last_product_id: str = ""
    last_run_ts: float = 0.0


_state = WorldModelHealthSnapshot()


def record_forecast(product_id: str, status: str, duration_ms: float) -> None:
    _FORECAST_RUNS.labels(product_id=product_id, status=status).inc()
    _FORECAST_DURATION.labels(type="forecast").observe(duration_ms)
    _state.total_forecast_runs += 1
    _state.last_product_id = product_id
    _state.last_run_ts = time.time()
    n = _state.total_forecast_runs
    _state.avg_forecast_ms = (_state.avg_forecast_ms * (n - 1) + duration_ms) / n


def record_simulation(scenario: str, duration_ms: float) -> None:
    _SIM_RUNS.labels(scenario=scenario).inc()
    _FORECAST_DURATION.labels(type="simulation").observe(duration_ms)
    _state.total_sim_runs += 1


def record_scenario_run(status: str, duration_ms: float) -> None:
    _SCENARIO_RUNS.labels(status=status).inc()
    _FORECAST_DURATION.labels(type="scenario_planning").observe(duration_ms)


def record_optimization(product_id: str, status: str, net_benefit_eur: float, duration_ms: float) -> None:
    _OPT_RUNS.labels(status=status).inc()
    _HEDGE_BENEFIT_GAUGE.labels(product_id=product_id).set(net_benefit_eur)
    _FORECAST_DURATION.labels(type="optimization").observe(duration_ms)
    _state.total_opt_runs += 1


def record_cost_delta(product_id: str, scenario: str, delta_pct: float) -> None:
    _COST_DELTA_GAUGE.labels(product_id=product_id, scenario=scenario).set(delta_pct)


def record_var(product_id: str, var_95: float) -> None:
    _VAR_GAUGE.labels(product_id=product_id).set(var_95)


def record_spread(product_id: str, spread_pct: float) -> None:
    _SPREAD_GAUGE.labels(product_id=product_id).set(spread_pct)


def get_health() -> WorldModelHealthSnapshot:
    return _state
