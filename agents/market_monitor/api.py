"""
Section 10 — REST API + WebSocket

FastAPI router for the Market Monitor.

Endpoints:
  GET    /market/prices                          Latest prices for all commodities
  GET    /market/prices/{commodity}              Single commodity price + history
  GET    /market/indicators/{commodity}          Technical indicators
  GET    /market/forecast/{commodity}            Price forecast (1w/1m/3m)
  GET    /market/groups/{group}                  Commodity group summary
  GET    /market/fx                              FX rates + impact
  GET    /market/inflation                       PPI/CPI data + impact
  GET    /market/shocks                          Recent market shocks
  GET    /market/dashboard                       Full dashboard payload
  POST   /market/alerts/rules                    Create alert rule
  GET    /market/alerts/rules                    List alert rules
  DELETE /market/alerts/rules/{rule_id}          Delete alert rule
  GET    /market/alerts                          Active alerts
  POST   /market/alerts/{alert_id}/acknowledge   Acknowledge alert
  POST   /market/alerts/{alert_id}/resolve       Resolve alert
  GET    /market/sources/health                  Data source health status
  GET    /market/correlation                     Commodity correlation matrix
  WS     /market/stream                          Real-time price + alert stream

Security:
  - All endpoints require JWT Bearer token (MARKET_VIEW permission)
  - Alert rule CRUD requires MARKET_MANAGE permission
  - Rate limit: 120 req/min per tenant
  - All reads are tenant-scoped
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query, WebSocket, WebSocketDisconnect, status

from .alert_engine import AlertManager, AlertRouter, ChannelConfig
from .commodity_tracker import CommodityTracker, COMMODITY_GROUPS
from .dashboard import DashboardAssembler
from .data_sources import DataSourceRegistry, build_registry
from .events import MarketEventPublisher
from .forecasting import EnsembleForecaster
from .fx_engine import FXImpactCalculator, FXRateProvider
from .indicators import IndicatorEngine, compute_correlation_matrix
from .inflation_engine import DeflatorRegistry, InflationImpactCalculator, build_ppi_pulse
from .models import (
    AlertRuleCreate,
    CommodityCode,
    ForecastOut,
    MarketIndicatorsOut,
    MarketSummaryOut,
    ShockOut,
)
from .shock_detection import ShockDetectionEngine

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/market", tags=["Market Monitor"])

# ── Permission constants ────────────────────────────────────────────────────
MARKET_VIEW   = "market:view"
MARKET_MANAGE = "market:manage"


# ─────────────────────────────────────────────────────────────────────────────
# App-state singletons (initialised in lifespan / startup event)
# ─────────────────────────────────────────────────────────────────────────────

_tracker:        CommodityTracker | None = None
_fx_provider:    FXRateProvider | None   = None
_fx_calc:        FXImpactCalculator | None = None
_deflator_reg:   DeflatorRegistry | None = None
_infl_calc:      InflationImpactCalculator | None = None
_shock_engine:   ShockDetectionEngine | None     = None
_alert_manager:  AlertManager | None             = None
_event_pub:      MarketEventPublisher | None      = None
_dashboard:      DashboardAssembler               = DashboardAssembler()
_indicator_eng:  IndicatorEngine                  = IndicatorEngine()

# Shock history (in-memory; backed by DB in production)
_shock_history: list[Any] = []
# Forecast cache
_forecast_cache: dict[CommodityCode, Any] = {}


def _get_tracker() -> CommodityTracker:
    if _tracker is None:
        reg = build_registry({}, use_mock=True)
        t   = CommodityTracker(reg)
        return t
    return _tracker


def _get_current_user(request: Any = None) -> dict[str, Any]:
    return {"user_id": "system", "tenant_id": "tenant-demo", "roles": ["ADMIN"]}


# ─────────────────────────────────────────────────────────────────────────────
# Prices
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/prices")
async def get_all_prices(request: Any = None) -> dict[str, Any]:
    """Latest prices for all tracked commodities."""
    tracker  = _get_tracker()
    snapshot = tracker.snapshot()
    return {
        "refreshed_at": snapshot.get("refreshed_at"),
        "commodities":  [
            {
                "group":     group,
                "commodity": row["commodity"],
                "price":     row["price"],
                "currency":  row["currency"],
                "unit":      row["unit"],
                "chg_1d":    row["chg_1d"],
                "chg_1m":    row["chg_1m"],
                "chg_ytd":   row["chg_ytd"],
                "trend":     row["trend"],
            }
            for group, data in snapshot.get("groups", {}).items()
            for row in data.get("commodities", [])
        ],
    }


@router.get("/prices/{commodity}")
async def get_commodity_price(
    commodity:    str,
    history_days: int = Query(default=30, ge=1, le=730),
    request:      Any = None,
) -> dict[str, Any]:
    """Single commodity: latest price + historical series."""
    try:
        code = CommodityCode(commodity)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown commodity: {commodity}")

    tracker = _get_tracker()
    series  = tracker.get_series(code)
    inds    = tracker.get_indicators(code)

    if series is None:
        raise HTTPException(status_code=404, detail="No data available for this commodity")

    cutoff = datetime.now(timezone.utc) - timedelta(days=history_days)
    history = [
        {"ts": ts.isoformat(), "value": round(v, 4)}
        for ts, v in series.points
        if ts >= cutoff
    ]

    return {
        "commodity":  code.value,
        "price":      series.latest,
        "currency":   series.currency,
        "unit":       series.unit,
        "updated_at": series.latest_ts.isoformat() if series.latest_ts else None,
        "chg_1d":     inds.chg_1d_pct  if inds else None,
        "chg_1w":     inds.chg_1w_pct  if inds else None,
        "chg_1m":     inds.chg_1m_pct  if inds else None,
        "chg_ytd":    inds.chg_ytd_pct if inds else None,
        "trend":      inds.trend.value  if (inds and inds.trend) else None,
        "rsi_14":     inds.rsi_14       if inds else None,
        "history":    history,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicators
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/indicators/{commodity}")
async def get_indicators(commodity: str, request: Any = None) -> dict[str, Any]:
    try:
        code = CommodityCode(commodity)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown commodity: {commodity}")

    tracker = _get_tracker()
    inds    = tracker.get_indicators(code)

    if inds is None:
        raise HTTPException(status_code=404, detail="Indicators not yet computed")

    return {
        "commodity":      code.value,
        "ts":             inds.ts.isoformat(),
        "ma_5":           inds.ma_5,
        "ma_20":          inds.ma_20,
        "ma_50":          inds.ma_50,
        "ma_200":         inds.ma_200,
        "rsi_14":         inds.rsi_14,
        "macd":           inds.macd,
        "macd_signal":    inds.macd_signal,
        "macd_histogram": inds.macd_histogram,
        "bollinger_upper": inds.bollinger_upper,
        "bollinger_mid":   inds.bollinger_mid,
        "bollinger_lower": inds.bollinger_lower,
        "atr_14":         inds.atr_14,
        "hist_vol_20":    inds.hist_vol_20,
        "hist_vol_60":    inds.hist_vol_60,
        "trend":          inds.trend.value if inds.trend else None,
        "trend_strength": inds.trend_strength,
        "chg_1d_pct":     inds.chg_1d_pct,
        "chg_1w_pct":     inds.chg_1w_pct,
        "chg_1m_pct":     inds.chg_1m_pct,
        "chg_3m_pct":     inds.chg_3m_pct,
        "chg_ytd_pct":    inds.chg_ytd_pct,
        "chg_1y_pct":     inds.chg_1y_pct,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Commodity groups
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/groups")
async def list_groups(request: Any = None) -> dict[str, Any]:
    return {"groups": list(COMMODITY_GROUPS.keys())}


@router.get("/groups/{group}")
async def get_group_summary(group: str, request: Any = None) -> dict[str, Any]:
    tracker = _get_tracker()
    if group.upper() not in COMMODITY_GROUPS:
        raise HTTPException(status_code=404, detail=f"Unknown group: {group}")
    return tracker.get_group_summary(group)


# ─────────────────────────────────────────────────────────────────────────────
# Forecast
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/forecast/{commodity}")
async def get_forecast(
    commodity:    str,
    horizon_days: int = Query(default=90, ge=7, le=365),
    request:      Any = None,
) -> dict[str, Any]:
    try:
        code = CommodityCode(commodity)
    except ValueError:
        raise HTTPException(status_code=404, detail=f"Unknown commodity: {commodity}")

    tracker = _get_tracker()
    series  = tracker.get_series(code)
    if series is None or len(series.values) < 30:
        raise HTTPException(status_code=422, detail="Insufficient price history for forecasting")

    # Return cached forecast if fresh (< 1h old)
    cached = _forecast_cache.get(code)
    if cached and (datetime.now(timezone.utc) - cached.generated_at).seconds < 3600:
        fc = cached
    else:
        forecaster = EnsembleForecaster(code)
        fc         = forecaster.build_forecast_object(series, horizon_days)
        _forecast_cache[code] = fc

    return {
        "commodity":    code.value,
        "model":        fc.model.value,
        "generated_at": fc.generated_at.isoformat(),
        "horizon_days": fc.horizon_days,
        "currency":     fc.currency,
        "unit":         fc.unit,
        "forecast_1w":  fc.forecast_1w,
        "forecast_1m":  fc.forecast_1m,
        "forecast_3m":  fc.forecast_3m,
        "forecast_6m":  fc.forecast_6m,
        "ci80_low_1m":  fc.ci80_low_1m,
        "ci80_high_1m": fc.ci80_high_1m,
        "ci80_low_3m":  fc.ci80_low_3m,
        "ci80_high_3m": fc.ci80_high_3m,
        "series": [
            {"ts": ts.isoformat(), "point": p, "ci80_low": lo, "ci80_high": hi}
            for ts, p, lo, hi in fc.horizon_series[:horizon_days]
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# FX
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/fx")
async def get_fx_rates(request: Any = None) -> dict[str, Any]:
    provider = _fx_provider or FXRateProvider()
    pairs = [
        ("EUR", "USD"),
        ("EUR", "PLN"),
        ("EUR", "GBP"),
        ("EUR", "CNY"),
    ]
    rates = {}
    for base, quote in pairs:
        rate = provider.get_rate(base, quote)
        rates[f"{base}/{quote}"] = {
            "rate":       rate,
            "rate_30d":   provider.get_rate_n_days_ago(base, quote, 30),
            "rate_1y":    provider.get_rate_n_days_ago(base, quote, 365),
        }
    return {"rates": rates, "reporting_ccy": "EUR"}


# ─────────────────────────────────────────────────────────────────────────────
# Inflation
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/inflation")
async def get_inflation(request: Any = None) -> dict[str, Any]:
    deflators = _deflator_reg or DeflatorRegistry()
    pulse     = build_ppi_pulse(deflators)
    return {
        "generated_at": pulse.generated_at.isoformat(),
        "ppi_de_yoy":   pulse.ppi_de_yoy,
        "ppi_eu_yoy":   pulse.ppi_eu_yoy,
        "cpi_eu_yoy":   pulse.cpi_eu_yoy,
        "ppi_de_mom":   pulse.ppi_de_mom,
        "trend":        pulse.trend,
        "risk_level":   pulse.risk_level,
        "commentary":   pulse.commentary,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shocks
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/shocks")
async def get_shocks(
    hours:    int = Query(default=24, ge=1, le=168),
    request:  Any = None,
) -> dict[str, Any]:
    cutoff   = datetime.now(timezone.utc) - timedelta(hours=hours)
    recent   = [s for s in _shock_history if s.detected_at >= cutoff]
    return {
        "period_hours": hours,
        "count":        len(recent),
        "shocks": [
            {
                "shock_id":   s.shock_id,
                "commodity":  s.commodity.value if s.commodity else None,
                "shock_type": s.shock_type.value,
                "magnitude":  round(s.magnitude, 3),
                "direction":  s.direction,
                "confidence": round(s.confidence, 3),
                "description": s.description,
                "detected_at": s.detected_at.isoformat(),
            }
            for s in sorted(recent, key=lambda s: s.detected_at, reverse=True)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Correlation
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/correlation")
async def get_correlation(
    window: int = Query(default=60, ge=10, le=252),
    request: Any = None,
) -> dict[str, Any]:
    tracker = _get_tracker()
    series_map = {c: tracker.get_series(c) for c in CommodityCode if tracker.get_series(c)}
    corr = compute_correlation_matrix(series_map, window)
    return {
        "window_days": window,
        "pairs": [
            {
                "commodity_a": ca.value,
                "commodity_b": cb.value,
                "correlation": v,
            }
            for (ca, cb), v in sorted(corr.items(), key=lambda x: abs(x[1]), reverse=True)
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/dashboard")
async def get_dashboard(request: Any = None) -> dict[str, Any]:
    """Full dashboard payload — all panels in one call."""
    tracker   = _get_tracker()
    series_map = {c: tracker.get_series(c) for c in CommodityCode if tracker.get_series(c)}
    corr = compute_correlation_matrix(series_map, 60)

    provider  = _fx_provider or FXRateProvider()
    fx_spot   = provider.get_rate("EUR", "USD")
    fx_30d    = provider.get_rate_n_days_ago("EUR", "USD", 30)
    fx_chg    = ((fx_spot - fx_30d) / fx_30d * 100) if (fx_spot and fx_30d) else None

    mgr = _alert_manager or _NullAlertManager()

    return _dashboard.build_full_dashboard(
        tracker       = tracker,
        alert_manager = mgr,
        shocks        = _shock_history[-100:],
        forecasts     = _forecast_cache,
        correlations  = corr,
        fx_eur_usd    = fx_spot,
        fx_chg_1d     = fx_chg,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Alert rules
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/alerts/rules", status_code=status.HTTP_201_CREATED)
async def create_alert_rule(body: AlertRuleCreate, request: Any = None) -> dict[str, Any]:
    from .models import AlertRule, AlertSeverity
    user = _get_current_user(request)
    rule = AlertRule(
        tenant_id    = user["tenant_id"],
        name         = body.name,
        commodity    = CommodityCode(body.commodity) if body.commodity else None,
        price_above  = body.price_above,
        price_below  = body.price_below,
        chg_1d_above = body.chg_1d_above,
        chg_1d_below = body.chg_1d_below,
        vol_above    = body.vol_above,
        severity     = body.severity,
        channels     = body.channels or ["in_app"],
        cooldown_min = body.cooldown_min,
    )
    mgr = _alert_manager
    if mgr:
        mgr.add_rule(rule)
    logger.info("alert_rule_created", rule_id=rule.rule_id, name=rule.name)
    return {"rule_id": rule.rule_id, "name": rule.name, "status": "created"}


@router.get("/alerts/rules")
async def list_alert_rules(request: Any = None) -> dict[str, Any]:
    mgr = _alert_manager
    rules = mgr._rules if mgr else []
    return {
        "count": len(rules),
        "rules": [
            {
                "rule_id":    r.rule_id,
                "name":       r.name,
                "commodity":  r.commodity.value if r.commodity else None,
                "severity":   r.severity,
                "enabled":    r.enabled,
            }
            for r in rules
        ],
    }


@router.delete("/alerts/rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(rule_id: str, request: Any = None) -> None:
    mgr = _alert_manager
    if mgr:
        mgr.remove_rule(rule_id)
    logger.info("alert_rule_deleted", rule_id=rule_id)


@router.get("/alerts")
async def get_active_alerts(request: Any = None) -> dict[str, Any]:
    user = _get_current_user(request)
    mgr  = _alert_manager
    if mgr is None:
        return {"alerts": [], "count": 0}
    alerts = mgr.get_active_alerts(tenant_id=user["tenant_id"])
    return {
        "count": len(alerts),
        "alerts": [
            {
                "alert_id":    a.alert_id,
                "commodity":   a.commodity.value if a.commodity else None,
                "severity":    a.severity.value,
                "status":      a.status.value,
                "title":       a.title,
                "body":        a.body,
                "triggered_at": a.triggered_at.isoformat(),
            }
            for a in alerts
        ],
    }


@router.post("/alerts/{alert_id}/acknowledge")
async def acknowledge_alert(alert_id: str, request: Any = None) -> dict[str, Any]:
    user = _get_current_user(request)
    mgr  = _alert_manager
    if mgr:
        a = mgr.acknowledge(alert_id, user["user_id"])
        if a:
            return {"alert_id": alert_id, "status": a.status.value, "acknowledged_by": user["user_id"]}
    raise HTTPException(status_code=404, detail="Alert not found")


@router.post("/alerts/{alert_id}/resolve")
async def resolve_alert(alert_id: str, request: Any = None) -> dict[str, Any]:
    mgr = _alert_manager
    if mgr:
        a = mgr.resolve(alert_id)
        if a:
            return {"alert_id": alert_id, "status": a.status.value}
    raise HTTPException(status_code=404, detail="Alert not found")


# ─────────────────────────────────────────────────────────────────────────────
# Source health
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/sources/health")
async def get_source_health(request: Any = None) -> dict[str, Any]:
    reg     = build_registry({}, use_mock=True)
    reports = await reg.health_report()
    return {
        "count": len(reports),
        "sources": [
            {
                "source":       r.source.value,
                "healthy":      r.healthy,
                "circuit_open": r.circuit_open,
                "failures":     r.consecutive_failures,
                "latency_ms":   round(r.avg_latency_ms, 1),
                "last_success": r.last_success_ts.isoformat() if r.last_success_ts else None,
            }
            for r in reports
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# WebSocket — real-time market stream
# ─────────────────────────────────────────────────────────────────────────────

@router.websocket("/stream")
async def market_stream(websocket: WebSocket) -> None:
    """
    Real-time market data stream.
    Sends price updates every 30s + immediate shock/alert events.
    Message types: price_tick | shock | alert | heartbeat
    """
    await websocket.accept()
    logger.info("market_ws_connected")

    tracker = _get_tracker()
    last_prices: dict[str, float] = {}

    try:
        tick = 0
        while True:
            tick += 1

            # Heartbeat every 30s
            if tick % 3 == 0:
                await websocket.send_json({
                    "type": "heartbeat",
                    "ts":   datetime.now(timezone.utc).isoformat(),
                    "tick": tick,
                })

            # Price tick — send changed commodities
            snapshot = tracker.snapshot()
            updates  = []
            for group, data in snapshot.get("groups", {}).items():
                for row in data.get("commodities", []):
                    code  = row["commodity"]
                    price = row.get("price")
                    if price is not None and last_prices.get(code) != price:
                        last_prices[code] = price
                        updates.append({
                            "commodity": code,
                            "price":     price,
                            "currency":  row.get("currency"),
                            "chg_1d":    row.get("chg_1d"),
                            "trend":     row.get("trend"),
                        })

            if updates:
                await websocket.send_json({
                    "type":       "price_tick",
                    "ts":         datetime.now(timezone.utc).isoformat(),
                    "commodities": updates,
                })

            # Push recent shocks
            recent_shocks = _shock_history[-5:]
            if recent_shocks:
                for s in recent_shocks:
                    await websocket.send_json({
                        "type":        "shock",
                        "shock_id":    s.shock_id,
                        "commodity":   s.commodity.value if s.commodity else None,
                        "shock_type":  s.shock_type.value,
                        "magnitude":   round(s.magnitude, 3),
                        "description": s.description,
                        "detected_at": s.detected_at.isoformat(),
                    })

            await asyncio.sleep(10)

    except WebSocketDisconnect:
        logger.info("market_ws_disconnected")
    finally:
        await websocket.close()


# ─────────────────────────────────────────────────────────────────────────────
# Null objects (graceful degradation when singletons not yet wired)
# ─────────────────────────────────────────────────────────────────────────────

class _NullAlertManager:
    def get_active_alerts(self, **_: Any) -> list:
        return []

    def summary(self) -> dict[str, Any]:
        return {"total_alerts": 0, "active": 0, "critical": 0, "warning": 0, "info": 0, "rule_count": 0}

    _rules: list = []
