"""
Section 9 — Dashboard Design

Data layer for the market monitoring dashboard.

Dashboard panels:
  1. Market Overview Header
       — Energy Index, Metals Index, Transport Index (sparkline)
       — Active alerts count, last refresh timestamp
       — EUR/USD FX rate with 1-day change

  2. Commodity Price Grid (per-group accordion)
       — Current price, 1d/1w/1m/YTD change, trend arrow
       — Colour coding: green (down >2%), red (up >2%), grey (flat)
       — Last 30-day sparkline data

  3. Technical Indicators Panel (per-commodity)
       — RSI gauge, MACD histogram, Bollinger Band chart data
       — Trend strength meter
       — Volatility regime (low/normal/elevated/extreme)

  4. Price Forecast Chart
       — 90-day ensemble forecast with 80% confidence band
       — Historical vs forecast overlay
       — Model accuracy metrics

  5. FX Impact Panel
       — EUR/USD, EUR/PLN live rates
       — Portfolio FX exposure heatmap
       — Hedge scenario table

  6. Inflation & PPI Panel
       — PPI Germany YoY (bar)
       — HICP Euro area (line)
       — Real vs nominal price comparison

  7. Alerts Feed
       — Active alerts (live updating)
       — Alert history (last 7 days)
       — Acknowledge / resolve buttons

  8. Shock Timeline
       — Chronological shock log
       — Affected commodities, contagion paths

  9. Correlation Heatmap
       — 20×20 commodity correlation matrix
       — Colour: dark red (corr=-1) → white → dark blue (corr=+1)

  10. Source Health Monitor
        — Per-source status (green/amber/red), last success, avg latency

All panel data is serialised as plain dicts (JSON-serialisable).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .models import (
    Alert,
    AlertSeverity,
    CommodityCode,
    MarketIndicators,
    MarketShock,
    PriceForecast,
    PriceSeries,
    TrendDirection,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Colour + formatting helpers
# ─────────────────────────────────────────────────────────────────────────────

def _trend_arrow(direction: TrendDirection | None) -> str:
    return {
        TrendDirection.STRONG_UP:   "↑↑",
        TrendDirection.UP:          "↑",
        TrendDirection.FLAT:        "→",
        TrendDirection.DOWN:        "↓",
        TrendDirection.STRONG_DOWN: "↓↓",
    }.get(direction, "—") if direction else "—"


def _price_colour(chg_pct: float | None) -> str:
    """CSS colour class for price change."""
    if chg_pct is None:
        return "neutral"
    if chg_pct > 2.0:
        return "red"
    if chg_pct < -2.0:
        return "green"
    return "neutral"


def _vol_regime(vol_20: float | None) -> str:
    if vol_20 is None:
        return "unknown"
    if vol_20 < 15:
        return "low"
    if vol_20 < 30:
        return "normal"
    if vol_20 < 50:
        return "elevated"
    return "extreme"


def _rsi_zone(rsi: float | None) -> str:
    if rsi is None:
        return "unknown"
    if rsi > 70:
        return "overbought"
    if rsi < 30:
        return "oversold"
    return "neutral"


# ─────────────────────────────────────────────────────────────────────────────
# Panel data builders
# ─────────────────────────────────────────────────────────────────────────────

def build_overview_header(
    indices:       dict[str, Any],   # from CommodityTracker.get_all_indices()
    active_alerts: int,
    last_refresh:  datetime | None,
    fx_eur_usd:    float | None,
    fx_chg_1d:     float | None,
) -> dict[str, Any]:
    return {
        "panel":         "overview_header",
        "generated_at":  datetime.now(timezone.utc).isoformat(),
        "last_refresh":  last_refresh.isoformat() if last_refresh else None,
        "active_alerts": active_alerts,
        "fx": {
            "eur_usd":      fx_eur_usd,
            "eur_usd_chg1d": fx_chg_1d,
            "colour":       _price_colour(fx_chg_1d),
        },
        "indices": {
            name: {
                "value": idx.get("value"),
                "label": idx.get("name"),
            }
            for name, idx in indices.items()
        },
    }


def build_commodity_grid_row(
    commodity:  CommodityCode,
    series:     PriceSeries,
    indicators: MarketIndicators | None,
    sparkline_days: int = 30,
) -> dict[str, Any]:
    """Single row in the commodity price grid."""
    vals = series.values
    sparkline = vals[-sparkline_days:] if len(vals) >= sparkline_days else vals

    chg_1d  = indicators.chg_1d_pct  if indicators else None
    chg_1w  = indicators.chg_1w_pct  if indicators else None
    chg_1m  = indicators.chg_1m_pct  if indicators else None
    chg_ytd = indicators.chg_ytd_pct if indicators else None
    trend   = indicators.trend        if indicators else None

    return {
        "commodity":  commodity.value,
        "label":      commodity.value.replace("_", " "),
        "price":      round(series.latest or 0, 4),
        "currency":   series.currency,
        "unit":       series.unit,
        "chg_1d":     round(chg_1d or 0, 2),
        "chg_1w":     round(chg_1w or 0, 2),
        "chg_1m":     round(chg_1m or 0, 2),
        "chg_ytd":    round(chg_ytd or 0, 2),
        "colour":     _price_colour(chg_1d),
        "trend":      trend.value if trend else "flat",
        "trend_arrow": _trend_arrow(trend),
        "sparkline":  [round(v, 4) for v in sparkline],
        "updated_at": series.latest_ts.isoformat() if series.latest_ts else None,
    }


def build_commodity_grid(
    series_map:  dict[CommodityCode, PriceSeries],
    inds_map:    dict[CommodityCode, MarketIndicators],
    group_order: list[str] | None = None,
) -> dict[str, Any]:
    from .commodity_tracker import COMMODITY_GROUPS

    order = group_order or list(COMMODITY_GROUPS.keys())
    groups = {}
    for group in order:
        codes = COMMODITY_GROUPS.get(group, [])
        rows  = []
        for code in codes:
            s = series_map.get(code)
            if s:
                rows.append(build_commodity_grid_row(code, s, inds_map.get(code)))
        groups[group] = {"group": group, "rows": rows}

    return {"panel": "commodity_grid", "groups": groups}


def build_technical_panel(
    commodity:  CommodityCode,
    series:     PriceSeries,
    indicators: MarketIndicators,
) -> dict[str, Any]:
    vals = series.values[-60:] if len(series.values) >= 60 else series.values

    # Bollinger Band data points for chart
    from .indicators import _bollinger, _sma
    bb_up, bb_mid, bb_lo = _bollinger(vals, 20)
    sma20 = _sma(vals, 20)

    return {
        "panel":     "technical",
        "commodity": commodity.value,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "price":     series.latest,
        "rsi_14": {
            "value":  round(indicators.rsi_14 or 50, 1),
            "zone":   _rsi_zone(indicators.rsi_14),
        },
        "macd": {
            "macd":      round(indicators.macd or 0, 4),
            "signal":    round(indicators.macd_signal or 0, 4),
            "histogram": round(indicators.macd_histogram or 0, 4),
            "bias":      "bullish" if (indicators.macd or 0) > (indicators.macd_signal or 0) else "bearish",
        },
        "bollinger": {
            "upper":  [round(v, 4) if v else None for v in bb_up],
            "mid":    [round(v, 4) if v else None for v in bb_mid],
            "lower":  [round(v, 4) if v else None for v in bb_lo],
            "price_series": [round(v, 4) for v in vals],
            "position": (
                "above_upper" if (series.latest and indicators.bollinger_upper
                                  and series.latest > indicators.bollinger_upper)
                else "below_lower" if (series.latest and indicators.bollinger_lower
                                        and series.latest < indicators.bollinger_lower)
                else "within"
            ),
        },
        "volatility": {
            "vol_20":   round(indicators.hist_vol_20 or 0, 1),
            "vol_60":   round(indicators.hist_vol_60 or 0, 1),
            "regime":   _vol_regime(indicators.hist_vol_20),
            "atr_14":   round(indicators.atr_14 or 0, 4),
        },
        "trend": {
            "direction": indicators.trend.value if indicators.trend else "flat",
            "strength":  round(indicators.trend_strength or 0, 3),
            "arrow":     _trend_arrow(indicators.trend),
            "ma_20":     round(indicators.ma_20 or 0, 4),
            "ma_50":     round(indicators.ma_50 or 0, 4),
            "ma_200":    round(indicators.ma_200 or 0, 4),
        },
    }


def build_forecast_panel(
    commodity: CommodityCode,
    series:    PriceSeries,
    forecast:  PriceForecast,
) -> dict[str, Any]:
    """Forecast chart panel with historical overlay."""
    hist_window = 90
    hist_vals   = series.values[-hist_window:]
    hist_ts     = series.timestamps[-hist_window:]

    return {
        "panel":      "forecast",
        "commodity":  commodity.value,
        "model":      forecast.model.value,
        "generated_at": forecast.generated_at.isoformat(),
        "currency":   forecast.currency,
        "unit":       forecast.unit,
        "historical": {
            "timestamps": [ts.isoformat() for ts in hist_ts],
            "values":     [round(v, 4) for v in hist_vals],
        },
        "forecast": {
            "timestamps": [ts.isoformat() for ts, _, _, _ in forecast.horizon_series],
            "point":      [round(p, 4) for _, p, _, _ in forecast.horizon_series],
            "ci80_low":   [round(lo, 4) for _, _, lo, _ in forecast.horizon_series],
            "ci80_high":  [round(hi, 4) for _, _, _, hi in forecast.horizon_series],
        },
        "summary": {
            "forecast_1w":  forecast.forecast_1w,
            "forecast_1m":  forecast.forecast_1m,
            "forecast_3m":  forecast.forecast_3m,
            "ci80_low_1m":  forecast.ci80_low_1m,
            "ci80_high_1m": forecast.ci80_high_1m,
        },
        "accuracy": {
            "rmse": forecast.rmse,
            "mae":  forecast.mae,
            "mape": forecast.mape,
            "r2":   forecast.r2,
        },
    }


def build_alerts_feed(
    alerts:    list[Alert],
    max_items: int = 50,
) -> dict[str, Any]:
    def _alert_dict(a: Alert) -> dict[str, Any]:
        return {
            "alert_id":    a.alert_id,
            "commodity":   a.commodity.value if a.commodity else None,
            "severity":    a.severity.value,
            "status":      a.status.value,
            "title":       a.title,
            "body":        a.body,
            "triggered_at": a.triggered_at.isoformat(),
            "acknowledged_by": a.acknowledged_by,
        }

    active   = [a for a in alerts if a.status.value == "active"]
    resolved = [a for a in alerts if a.status.value != "active"]

    return {
        "panel":    "alerts_feed",
        "active":   [_alert_dict(a) for a in active[:max_items]],
        "resolved": [_alert_dict(a) for a in resolved[:20]],
        "summary": {
            "total_active":   len(active),
            "critical":       sum(1 for a in active if a.severity == AlertSeverity.CRITICAL),
            "warning":        sum(1 for a in active if a.severity == AlertSeverity.WARNING),
        },
    }


def build_shock_timeline(
    shocks:    list[MarketShock],
    max_items: int = 100,
) -> dict[str, Any]:
    def _shock_dict(s: MarketShock) -> dict[str, Any]:
        return {
            "shock_id":    s.shock_id,
            "commodity":   s.commodity.value if s.commodity else None,
            "shock_type":  s.shock_type.value,
            "magnitude":   round(s.magnitude, 3),
            "direction":   s.direction,
            "confidence":  round(s.confidence, 3),
            "description": s.description,
            "detected_at": s.detected_at.isoformat(),
            "affected":    [c.value for c in s.affected_commodities],
        }

    return {
        "panel":  "shock_timeline",
        "shocks": [_shock_dict(s) for s in shocks[:max_items]],
        "count":  len(shocks),
    }


def build_correlation_heatmap(
    correlations: dict[tuple, float],
    commodities:  list[CommodityCode],
) -> dict[str, Any]:
    """
    Builds correlation matrix for heatmap rendering.
    Returns {labels, matrix} where matrix[i][j] = correlation(i,j).
    """
    n      = len(commodities)
    idx_map = {c: i for i, c in enumerate(commodities)}
    matrix  = [[None] * n for _ in range(n)]

    # Diagonal = 1
    for i in range(n):
        matrix[i][i] = 1.0

    for (ca, cb), corr in correlations.items():
        ia = idx_map.get(ca)
        ib = idx_map.get(cb)
        if ia is not None and ib is not None:
            matrix[ia][ib] = corr
            matrix[ib][ia] = corr

    return {
        "panel":  "correlation_heatmap",
        "labels": [c.value for c in commodities],
        "matrix": matrix,
        "scale":  {"min": -1.0, "max": 1.0, "neutral": 0.0},
    }


def build_source_health_panel(
    health_reports: list[Any],   # list[SourceHealth]
) -> dict[str, Any]:
    def _status(healthy: bool, circuit_open: bool) -> str:
        if circuit_open:
            return "degraded"
        return "ok" if healthy else "warning"

    return {
        "panel": "source_health",
        "sources": [
            {
                "source":       h.source.value,
                "status":       _status(h.healthy, h.circuit_open),
                "last_success": h.last_success_ts.isoformat() if h.last_success_ts else None,
                "failures":     h.consecutive_failures,
                "latency_ms":   round(h.avg_latency_ms, 1),
            }
            for h in health_reports
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dashboard assembler
# ─────────────────────────────────────────────────────────────────────────────

class DashboardAssembler:
    """
    Assembles the full dashboard payload from all subsystems.
    Called by API endpoint GET /market/dashboard.
    """

    def build_full_dashboard(
        self,
        tracker:       Any,   # CommodityTracker
        alert_manager: Any,   # AlertManager
        shocks:        list[MarketShock],
        forecasts:     dict[CommodityCode, PriceForecast],
        correlations:  dict[tuple, float],
        fx_eur_usd:    float | None = None,
        fx_chg_1d:     float | None = None,
        health_reports: list[Any] | None = None,
    ) -> dict[str, Any]:
        series_map = {c: tracker.get_series(c) for c in CommodityCode if tracker.get_series(c)}
        inds_map   = tracker.get_all_indicators()
        indices    = {name: {"name": idx.name, "value": idx.value}
                      for name, idx in tracker.get_all_indices().items()}
        alerts     = alert_manager.get_active_alerts() if hasattr(alert_manager, "get_active_alerts") else []

        dashboard = {
            "generated_at":  datetime.now(timezone.utc).isoformat(),
            "overview":      build_overview_header(
                indices       = indices,
                active_alerts = len(alerts),
                last_refresh  = tracker._last_refresh,
                fx_eur_usd    = fx_eur_usd,
                fx_chg_1d     = fx_chg_1d,
            ),
            "commodity_grid": build_commodity_grid(series_map, inds_map),
            "alerts_feed":    build_alerts_feed(alerts),
            "shock_timeline": build_shock_timeline(shocks),
            "correlation_heatmap": build_correlation_heatmap(
                correlations,
                list(series_map.keys())[:20],  # cap at 20 for readability
            ),
        }

        if health_reports:
            dashboard["source_health"] = build_source_health_panel(health_reports)

        # Technical panels for top-traded commodities
        priority = [
            CommodityCode.STEEL_HRC, CommodityCode.COPPER_GRADE_A,
            CommodityCode.BRENT, CommodityCode.ALUMINIUM_P1020,
        ]
        tech_panels = {}
        for code in priority:
            s = series_map.get(code)
            i = inds_map.get(code)
            if s and i:
                tech_panels[code.value] = build_technical_panel(code, s, i)
        dashboard["technical_panels"] = tech_panels

        # Forecast panels
        forecast_panels = {}
        for code, fc in forecasts.items():
            s = series_map.get(code)
            if s:
                forecast_panels[code.value] = build_forecast_panel(code, s, fc)
        dashboard["forecast_panels"] = forecast_panels

        return dashboard
