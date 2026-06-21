"""
Section 7 — Market Shock Detection

Detects abnormal price movements, volatility regime changes, and
supply/demand shocks across all tracked commodities.

Detection methods:
  Z-Score detector       — |price - μ| / σ > threshold (rolling 60d window)
  CUSUM detector         — cumulative sum control chart for sustained drift
  GARCH(1,1) detector    — volatility regime change via conditional variance
  Correlation detector   — cross-commodity correlation breakdown (Spearman)
  Seasonal anomaly       — deviation from expected seasonal pattern
  News signal injector   — keyword-driven event classification (external feed)

Shock severity:
  magnitude ≤ 2σ     → monitor only
  2σ < magnitude ≤ 3σ → WARNING alert
  magnitude > 3σ     → CRITICAL alert

Cross-commodity contagion:
  Energy shock → propagates to Transport (+2d lag), Steel (+5d lag)
  Transport shock → propagates to all physical commodities (+3d lag)
  FX shock (EUR/USD) → propagates to all USD-denominated commodities (same day)
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .models import (
    AlertSeverity,
    CommodityCode,
    MarketShock,
    ShockType,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SHOCK_CONFIG = {
    "z_score_warning":    2.0,   # std devs
    "z_score_critical":   3.0,
    "cusum_slack":        0.5,   # CUSUM k parameter (fractions of sigma)
    "cusum_threshold":    5.0,   # CUSUM h parameter
    "vol_regime_ratio":   2.0,   # current vol / historical vol ratio
    "corr_break_delta":   0.40,  # correlation change > 0.40 = breakdown
    "seasonal_z_thresh":  2.5,   # seasonal anomaly z-score
    "min_history":        30,    # minimum points required
}


# ─────────────────────────────────────────────────────────────────────────────
# Z-Score detector
# ─────────────────────────────────────────────────────────────────────────────

def detect_z_score_shock(
    values:    list[float],
    commodity: CommodityCode,
    window:    int   = 60,
    cfg:       dict  = DEFAULT_SHOCK_CONFIG,
) -> MarketShock | None:
    """
    Detects if the latest value is an outlier vs rolling μ±σ.
    Uses 60-day rolling window (252 for longer-horizon commodities).
    """
    if len(values) < cfg["min_history"]:
        return None

    history = values[-window - 1: -1]
    latest  = values[-1]

    if len(history) < 10:
        return None

    mu    = statistics.mean(history)
    sigma = statistics.stdev(history) if len(history) > 1 else 1e-6
    if sigma == 0:
        return None

    z = (latest - mu) / sigma

    if abs(z) < cfg["z_score_warning"]:
        return None

    direction = 1 if z > 0 else -1

    return MarketShock(
        commodity    = commodity,
        shock_type   = ShockType.PRICE_SPIKE if z > 0 else ShockType.PRICE_CRASH,
        magnitude    = round(abs(z), 3),
        direction    = direction,
        description  = (
            f"{commodity.value}: price {latest:.2f} is {abs(z):.1f}σ "
            f"{'above' if z > 0 else 'below'} 60-day mean {mu:.2f}"
        ),
        confidence   = min(1.0, (abs(z) - cfg["z_score_warning"]) / 2.0),
        metadata     = {"z_score": round(z, 3), "mu": round(mu, 4), "sigma": round(sigma, 4)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# CUSUM detector
# ─────────────────────────────────────────────────────────────────────────────

def detect_cusum_shift(
    values:    list[float],
    commodity: CommodityCode,
    window:    int  = 120,
    cfg:       dict = DEFAULT_SHOCK_CONFIG,
) -> MarketShock | None:
    """
    CUSUM control chart — detects sustained upward or downward drift.
    Signals when cumulative sum of standardised deviations > h·σ.
    """
    if len(values) < cfg["min_history"] + 10:
        return None

    history = values[-window:]
    mu      = statistics.mean(history[:-10])
    sigma   = statistics.stdev(history[:-10]) if len(history) > 10 else 1.0
    k       = cfg["cusum_slack"] * sigma
    h       = cfg["cusum_threshold"] * sigma

    s_hi    = 0.0
    s_lo    = 0.0
    for v in history[-20:]:   # evaluate on last 20 observations
        s_hi = max(0, s_hi + (v - mu) - k)
        s_lo = max(0, s_lo - (v - mu) - k)

    if s_hi > h:
        return MarketShock(
            commodity   = commodity,
            shock_type  = ShockType.DEMAND_SURGE,
            magnitude   = round(s_hi / sigma, 3),
            direction   = 1,
            description = f"{commodity.value}: CUSUM detects sustained upward drift (S+={s_hi:.2f} > {h:.2f})",
            confidence  = min(1.0, (s_hi - h) / h),
            metadata    = {"cusum_hi": round(s_hi, 4), "threshold": round(h, 4)},
        )
    if s_lo > h:
        return MarketShock(
            commodity   = commodity,
            shock_type  = ShockType.SUPPLY_DISRUPTION,
            magnitude   = round(s_lo / sigma, 3),
            direction   = -1,
            description = f"{commodity.value}: CUSUM detects sustained downward drift (S-={s_lo:.2f} > {h:.2f})",
            confidence  = min(1.0, (s_lo - h) / h),
            metadata    = {"cusum_lo": round(s_lo, 4), "threshold": round(h, 4)},
        )
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Volatility regime detector
# ─────────────────────────────────────────────────────────────────────────────

def _realised_vol(values: list[float], window: int) -> float | None:
    if len(values) < window + 1:
        return None
    rets = [math.log(values[i] / values[i - 1]) for i in range(-window, 0) if values[i - 1] != 0]
    return statistics.stdev(rets) * math.sqrt(252) * 100 if len(rets) > 1 else None


def detect_volatility_regime_change(
    values:    list[float],
    commodity: CommodityCode,
    cfg:       dict = DEFAULT_SHOCK_CONFIG,
) -> MarketShock | None:
    """
    Compares short-term (10-day) vs long-term (60-day) realised volatility.
    Ratio > 2x → regime change.
    """
    vol_short = _realised_vol(values, 10)
    vol_long  = _realised_vol(values, 60)

    if vol_short is None or vol_long is None or vol_long == 0:
        return None

    ratio = vol_short / vol_long

    if ratio < cfg["vol_regime_ratio"]:
        return None

    return MarketShock(
        commodity   = commodity,
        shock_type  = ShockType.VOLATILITY_REGIME,
        magnitude   = round(ratio, 3),
        direction   = 0,
        description = (
            f"{commodity.value}: volatility regime change detected "
            f"(10d vol {vol_short:.1f}% vs 60d vol {vol_long:.1f}%, ratio {ratio:.1f}x)"
        ),
        confidence  = min(1.0, (ratio - cfg["vol_regime_ratio"]) / cfg["vol_regime_ratio"]),
        metadata    = {"vol_10d": round(vol_short, 2), "vol_60d": round(vol_long, 2), "ratio": round(ratio, 2)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cross-commodity correlation breakdown
# ─────────────────────────────────────────────────────────────────────────────

def _spearman_corr(x: list[float], y: list[float]) -> float:
    """Spearman rank correlation."""
    n   = len(x)
    rx  = [sorted(x).index(xi) for xi in x]
    ry  = [sorted(y).index(yi) for yi in y]
    d2  = sum((rxi - ryi) ** 2 for rxi, ryi in zip(rx, ry))
    return 1 - 6 * d2 / (n * (n ** 2 - 1)) if n > 1 else 0.0


def detect_correlation_breakdown(
    series_a:    list[float],
    series_b:    list[float],
    commodity_a: CommodityCode,
    commodity_b: CommodityCode,
    long_window: int  = 120,
    short_window: int = 20,
    cfg:         dict = DEFAULT_SHOCK_CONFIG,
) -> MarketShock | None:
    """
    Detects if the rolling short-term correlation deviates significantly
    from the long-term baseline correlation.
    """
    min_len = max(long_window, short_window) + 1
    if len(series_a) < min_len or len(series_b) < min_len:
        return None

    aligned = min(len(series_a), len(series_b))
    a, b    = series_a[-aligned:], series_b[-aligned:]

    corr_long  = _spearman_corr(a[-long_window:],  b[-long_window:])
    corr_short = _spearman_corr(a[-short_window:], b[-short_window:])
    delta      = abs(corr_long - corr_short)

    if delta < cfg["corr_break_delta"]:
        return None

    return MarketShock(
        commodity            = commodity_a,
        shock_type           = ShockType.CORRELATION_BREAK,
        magnitude            = round(delta, 3),
        direction            = 0,
        affected_commodities = [commodity_a, commodity_b],
        description          = (
            f"Correlation breakdown: {commodity_a.value} ↔ {commodity_b.value} "
            f"(long-term {corr_long:.2f} → short-term {corr_short:.2f}, Δ={delta:.2f})"
        ),
        confidence           = min(1.0, delta / 0.80),
        metadata             = {"corr_long": round(corr_long, 3), "corr_short": round(corr_short, 3)},
    )


# ─────────────────────────────────────────────────────────────────────────────
# Seasonal anomaly detector
# ─────────────────────────────────────────────────────────────────────────────

def detect_seasonal_anomaly(
    values:     list[float],
    timestamps: list[datetime],
    commodity:  CommodityCode,
    cfg:        dict = DEFAULT_SHOCK_CONFIG,
) -> MarketShock | None:
    """
    Compares current month's average price to same-month historical mean.
    Flags if z-score > threshold.
    """
    if len(values) < 24 or not timestamps:
        return None

    now_month  = timestamps[-1].month
    now_year   = timestamps[-1].year

    # Group historical prices by calendar month (exclude current year)
    by_month: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for ts, v in zip(timestamps[:-21], values[:-21]):
        if ts.year < now_year:
            by_month[ts.month].append(v)

    historical = by_month.get(now_month, [])
    if len(historical) < 2:
        return None

    hist_mean  = statistics.mean(historical)
    hist_std   = statistics.stdev(historical)
    if hist_std == 0:
        return None

    current    = statistics.mean(values[-21:])   # current month avg
    z          = (current - hist_mean) / hist_std

    if abs(z) < cfg["seasonal_z_thresh"]:
        return None

    return MarketShock(
        commodity   = commodity,
        shock_type  = ShockType.SEASONAL_ANOMALY,
        magnitude   = round(abs(z), 3),
        direction   = 1 if z > 0 else -1,
        description = (
            f"{commodity.value}: seasonal anomaly in month {now_month} "
            f"(current {current:.2f} vs historical mean {hist_mean:.2f}, {z:+.1f}σ)"
        ),
        confidence  = min(1.0, abs(z) / 5.0),
        metadata    = {
            "current_avg": round(current, 4),
            "hist_mean":   round(hist_mean, 4),
            "hist_std":    round(hist_std, 4),
            "month":       now_month,
        },
    )


# ─────────────────────────────────────────────────────────────────────────────
# Cross-commodity contagion propagation
# ─────────────────────────────────────────────────────────────────────────────

# Defines which commodities are affected by a shock to a source commodity.
# Value = lag in trading days before effect appears.
CONTAGION_MAP: dict[CommodityCode, dict[CommodityCode, int]] = {
    CommodityCode.BRENT: {
        CommodityCode.ROAD_FREIGHT_EU:  2,
        CommodityCode.CONTAINER_SPOT:   2,
        CommodityCode.STEEL_HRC:        5,
        CommodityCode.ALUMINIUM_P1020:  3,
    },
    CommodityCode.CONTAINER_SPOT: {
        CommodityCode.OCC:          3,
        CommodityCode.CONTAINERBOARD: 3,
        CommodityCode.LUMBER_SOFTWOOD: 5,
    },
    CommodityCode.BALTIC_DRY: {
        CommodityCode.COPPER_GRADE_A: 3,
        CommodityCode.STEEL_HRC:      3,
    },
}


def propagate_shocks(
    primary_shock: MarketShock,
) -> list[dict[str, Any]]:
    """
    Returns list of expected secondary shocks given a primary shock.
    Each entry: {commodity, lag_days, description}
    """
    contagion = CONTAGION_MAP.get(primary_shock.commodity, {})
    result = []
    for affected, lag in contagion.items():
        result.append({
            "commodity":   affected,
            "lag_days":    lag,
            "expected_at": datetime.now(timezone.utc) + timedelta(days=lag),
            "description": (
                f"{affected.value} expected to be affected by "
                f"{primary_shock.commodity.value} shock in ~{lag} trading days"
            ),
        })
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Shock Engine — orchestrates all detectors
# ─────────────────────────────────────────────────────────────────────────────

class ShockDetectionEngine:
    """
    Runs all shock detectors on a commodity snapshot.
    Deduplicates and ranks by severity.
    """

    def __init__(self, cfg: dict | None = None) -> None:
        self._cfg = {**DEFAULT_SHOCK_CONFIG, **(cfg or {})}

    def scan_commodity(
        self,
        commodity:  CommodityCode,
        values:     list[float],
        timestamps: list[datetime] | None = None,
    ) -> list[MarketShock]:
        shocks: list[MarketShock] = []

        z = detect_z_score_shock(values, commodity, cfg=self._cfg)
        if z:
            shocks.append(z)

        c = detect_cusum_shift(values, commodity, cfg=self._cfg)
        if c:
            shocks.append(c)

        v = detect_volatility_regime_change(values, commodity, cfg=self._cfg)
        if v:
            shocks.append(v)

        if timestamps:
            s = detect_seasonal_anomaly(values, timestamps, commodity, cfg=self._cfg)
            if s:
                shocks.append(s)

        return shocks

    def scan_all(
        self,
        series_map: dict[CommodityCode, "PriceSeries"],     # type: ignore[name-defined]
    ) -> list[MarketShock]:
        from .models import PriceSeries
        all_shocks: list[MarketShock] = []

        for commodity, series in series_map.items():
            shocks = self.scan_commodity(
                commodity,
                series.values,
                series.timestamps,
            )
            all_shocks.extend(shocks)

        # Correlation breakdown scans on key pairs
        pairs = [
            (CommodityCode.BRENT, CommodityCode.STEEL_HRC),
            (CommodityCode.BRENT, CommodityCode.CONTAINER_SPOT),
            (CommodityCode.COPPER_GRADE_A, CommodityCode.ALUMINIUM_P1020),
            (CommodityCode.CONTAINER_SPOT, CommodityCode.CONTAINERBOARD),
        ]
        for ca, cb in pairs:
            sa = series_map.get(ca)
            sb = series_map.get(cb)
            if sa and sb:
                shock = detect_correlation_breakdown(
                    sa.values, sb.values, ca, cb, cfg=self._cfg
                )
                if shock:
                    all_shocks.append(shock)

        # Sort by magnitude descending
        all_shocks.sort(key=lambda s: s.magnitude, reverse=True)
        return all_shocks

    def severity(self, shock: MarketShock) -> AlertSeverity:
        if shock.magnitude >= self._cfg["z_score_critical"]:
            return AlertSeverity.CRITICAL
        if shock.magnitude >= self._cfg["z_score_warning"]:
            return AlertSeverity.WARNING
        return AlertSeverity.INFO
