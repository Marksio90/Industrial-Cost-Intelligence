"""
Section 2 — Market Indicators

Technical + fundamental indicator computation on price series.

Indicators computed:
  Moving averages: SMA(5,20,50,200), EMA(12,26)
  Momentum:        RSI(14), MACD(12,26,9), Rate of Change
  Volatility:      Bollinger Bands(20,2σ), ATR(14), Historical Vol (20d, 60d annualised)
  Trend:           ADX-style trend strength, direction classification
  Performance:     % change over 1d / 1w / 1m / 3m / YTD / 1y

All functions operate on plain list[float] to avoid NumPy dependency.
If NumPy is available it is used for speed; otherwise pure-Python fallback.
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timedelta, timezone
from typing import Sequence

import structlog

from .models import (
    CommodityCode,
    MarketIndicators,
    PriceSeries,
    TrendDirection,
)

logger = structlog.get_logger(__name__)

try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False


# ─────────────────────────────────────────────────────────────────────────────
# Low-level math helpers (pure Python)
# ─────────────────────────────────────────────────────────────────────────────

def _sma(values: Sequence[float], period: int) -> list[float | None]:
    result: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            result.append(None)
        else:
            result.append(sum(values[i - period + 1: i + 1]) / period)
    return result


def _ema(values: Sequence[float], period: int) -> list[float | None]:
    k = 2 / (period + 1)
    result: list[float | None] = [None] * (period - 1)
    if len(values) < period:
        return [None] * len(values)
    seed = sum(values[:period]) / period
    result.append(seed)
    for v in values[period:]:
        result.append(result[-1] * (1 - k) + v * k)  # type: ignore[operator]
    return result


def _rsi(values: Sequence[float], period: int = 14) -> list[float | None]:
    """Wilder RSI."""
    if len(values) < period + 1:
        return [None] * len(values)
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    gains   = [max(c, 0.0) for c in changes]
    losses  = [max(-c, 0.0) for c in changes]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_vals: list[float | None] = [None] * (period + 1)
    rs  = avg_gain / avg_loss if avg_loss else float("inf")
    rsi_vals.append(100 - 100 / (1 + rs))

    for i in range(period, len(changes)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rs = avg_gain / avg_loss if avg_loss else float("inf")
        rsi_vals.append(100 - 100 / (1 + rs))

    return rsi_vals


def _atr(
    highs:  Sequence[float],
    lows:   Sequence[float],
    closes: Sequence[float],
    period: int = 14,
) -> list[float | None]:
    """Average True Range — requires high/low/close; approximated with single series."""
    # When only close is available: ATR ≈ SMA of |close[i] - close[i-1]|
    trs: list[float] = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
    result: list[float | None] = [None]
    smas = _sma(trs, period)
    result.extend(smas)
    return result


def _bollinger(
    values: Sequence[float],
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (upper, mid, lower) bands."""
    upper: list[float | None] = []
    mid:   list[float | None] = []
    lower: list[float | None] = []
    for i in range(len(values)):
        if i < period - 1:
            upper.append(None); mid.append(None); lower.append(None)
        else:
            window = list(values[i - period + 1: i + 1])
            m = sum(window) / period
            sd = statistics.stdev(window) if len(window) > 1 else 0.0
            upper.append(m + std_mult * sd)
            mid.append(m)
            lower.append(m - std_mult * sd)
    return upper, mid, lower


def _hist_vol(values: Sequence[float], period: int = 20, annualise: int = 252) -> list[float | None]:
    """Annualised historical volatility of log returns."""
    if len(values) < 2:
        return [None] * len(values)
    log_rets = [math.log(values[i] / values[i - 1]) for i in range(1, len(values))]
    result: list[float | None] = [None]  # shift for first return
    for i in range(len(log_rets)):
        if i < period - 1:
            result.append(None)
        else:
            window = log_rets[i - period + 1: i + 1]
            vol = statistics.stdev(window) * math.sqrt(annualise) * 100
            result.append(vol)
    return result


def _macd(
    values: Sequence[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Returns (macd_line, signal_line, histogram)."""
    ema_fast   = _ema(values, fast)
    ema_slow   = _ema(values, slow)
    macd_line  = [
        (f - s) if (f is not None and s is not None) else None
        for f, s in zip(ema_fast, ema_slow)
    ]
    valid = [(i, v) for i, v in enumerate(macd_line) if v is not None]
    signal_line: list[float | None] = [None] * len(macd_line)
    if len(valid) >= signal:
        vals  = [v for _, v in valid]
        idxs  = [i for i, _ in valid]
        sigs  = _ema(vals, signal)
        for pos, sig in zip(idxs, sigs):
            signal_line[pos] = sig
    histogram = [
        (m - s) if (m is not None and s is not None) else None
        for m, s in zip(macd_line, signal_line)
    ]
    return macd_line, signal_line, histogram


# ─────────────────────────────────────────────────────────────────────────────
# Trend classification
# ─────────────────────────────────────────────────────────────────────────────

def _classify_trend(values: Sequence[float], window: int = 20) -> tuple[TrendDirection, float]:
    """
    Simple trend: compare EMA-20 slope and price-vs-MA position.
    Returns (direction, strength 0–1).
    """
    if len(values) < window + 1:
        return TrendDirection.FLAT, 0.0

    ema = _ema(values, window)
    last_ema   = next((v for v in reversed(ema) if v is not None), None)
    prev_ema   = next((v for v in reversed(ema[:-5]) if v is not None), None)
    last_price = values[-1]

    if last_ema is None or prev_ema is None:
        return TrendDirection.FLAT, 0.0

    slope_pct = (last_ema - prev_ema) / prev_ema * 100
    price_vs_ma = (last_price - last_ema) / last_ema * 100

    # Strength: 0–1 based on magnitude
    strength = min(1.0, abs(slope_pct) / 5.0)

    if slope_pct > 2.0 and price_vs_ma > 0:
        direction = TrendDirection.STRONG_UP
    elif slope_pct > 0.5:
        direction = TrendDirection.UP
    elif slope_pct < -2.0 and price_vs_ma < 0:
        direction = TrendDirection.STRONG_DOWN
    elif slope_pct < -0.5:
        direction = TrendDirection.DOWN
    else:
        direction = TrendDirection.FLAT
        strength  = 0.0

    return direction, round(strength, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Performance helpers
# ─────────────────────────────────────────────────────────────────────────────

def _pct_change_n(values: Sequence[float], n: int) -> float | None:
    if len(values) < n + 1:
        return None
    old = values[-(n + 1)]
    new = values[-1]
    return (new - old) / old * 100 if old else None


def _pct_change_ytd(
    series: PriceSeries,
) -> float | None:
    now        = datetime.now(timezone.utc)
    year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
    baseline   = next((v for ts, v in series.points if ts >= year_start), None)
    if baseline is None or series.latest is None:
        return None
    return (series.latest - baseline) / baseline * 100


def _pct_change_periods_ago(series: PriceSeries, days: int) -> float | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    old_val = next(
        (v for ts, v in reversed(series.points) if ts <= cutoff),
        None,
    )
    if old_val is None or series.latest is None:
        return None
    return (series.latest - old_val) / old_val * 100


# ─────────────────────────────────────────────────────────────────────────────
# Main indicator engine
# ─────────────────────────────────────────────────────────────────────────────

class IndicatorEngine:
    """
    Computes all technical + performance indicators for a PriceSeries.
    """

    def compute(self, series: PriceSeries) -> MarketIndicators:
        values = series.values
        ts     = series.latest_ts or datetime.now(timezone.utc)

        if not values:
            return MarketIndicators(commodity=series.commodity, ts=ts)

        # — Moving averages ——————————————————————————————————————————————
        sma5   = _sma(values, 5)
        sma20  = _sma(values, 20)
        sma50  = _sma(values, 50)
        sma200 = _sma(values, 200)
        ema12  = _ema(values, 12)
        ema26  = _ema(values, 26)

        # — Momentum ————————————————————————————————————————————————————
        rsi14  = _rsi(values, 14)
        macd_line, macd_sig, macd_hist = _macd(values)

        # — Volatility ——————————————————————————————————————————————————
        bb_up, bb_mid, bb_low = _bollinger(values, 20)
        atr14    = _atr(values, values, values, 14)
        hvol20   = _hist_vol(values, 20)
        hvol60   = _hist_vol(values, 60)

        # — Trend ————————————————————————————————————————————————————————
        trend, strength = _classify_trend(values)

        # — Performance ——————————————————————————————————————————————————
        # Approximate trading days: 1d=1, 1w=5, 1m=21, 3m=63, 1y=252
        chg_1d  = _pct_change_n(values, 1)
        chg_1w  = _pct_change_n(values, 5)
        chg_1m  = _pct_change_n(values, 21)
        chg_3m  = _pct_change_n(values, 63)
        chg_ytd = _pct_change_ytd(series)
        chg_1y  = _pct_change_n(values, 252)

        def _last(lst: list) -> float | None:
            return next((v for v in reversed(lst) if v is not None), None)

        return MarketIndicators(
            commodity       = series.commodity,
            ts              = ts,
            ma_5            = _last(sma5),
            ma_20           = _last(sma20),
            ma_50           = _last(sma50),
            ma_200          = _last(sma200),
            ema_12          = _last(ema12),
            ema_26          = _last(ema26),
            rsi_14          = _last(rsi14),
            macd            = _last(macd_line),
            macd_signal     = _last(macd_sig),
            macd_histogram  = _last(macd_hist),
            bollinger_upper = _last(bb_up),
            bollinger_mid   = _last(bb_mid),
            bollinger_lower = _last(bb_low),
            atr_14          = _last(atr14),
            hist_vol_20     = _last(hvol20),
            hist_vol_60     = _last(hvol60),
            trend           = trend,
            trend_strength  = strength,
            chg_1d_pct      = chg_1d,
            chg_1w_pct      = chg_1w,
            chg_1m_pct      = chg_1m,
            chg_3m_pct      = chg_3m,
            chg_ytd_pct     = chg_ytd,
            chg_1y_pct      = chg_1y,
        )

    def compute_all(self, series_map: dict[CommodityCode, PriceSeries]) -> dict[CommodityCode, MarketIndicators]:
        return {code: self.compute(s) for code, s in series_map.items()}

    def overbought_oversold(self, indicators: MarketIndicators) -> str | None:
        """Returns 'overbought' | 'oversold' | None based on RSI."""
        if indicators.rsi_14 is None:
            return None
        if indicators.rsi_14 > 70:
            return "overbought"
        if indicators.rsi_14 < 30:
            return "oversold"
        return None

    def bollinger_position(self, series: PriceSeries, inds: MarketIndicators) -> str | None:
        """Returns 'above_upper' | 'below_lower' | 'within' | None."""
        price = series.latest
        if price is None:
            return None
        if inds.bollinger_upper and price > inds.bollinger_upper:
            return "above_upper"
        if inds.bollinger_lower and price < inds.bollinger_lower:
            return "below_lower"
        return "within"

    def macd_signal_cross(self, series: PriceSeries, prev_values: list[float]) -> str | None:
        """
        Detects MACD signal line crossover on the last 2 data points.
        Returns 'bullish_cross' | 'bearish_cross' | None.
        """
        if len(prev_values) < 2:
            return None
        ml_now,  ms_now,  _ = _macd(series.values)
        ml_prev, ms_prev, _ = _macd(prev_values)

        def _last(lst: list) -> float | None:
            return next((v for v in reversed(lst) if v is not None), None)

        m_now, s_now  = _last(ml_now), _last(ms_now)
        m_prev, s_prev = _last(ml_prev), _last(ms_prev)

        if None in (m_now, s_now, m_prev, s_prev):
            return None
        if m_prev < s_prev and m_now > s_now:        # type: ignore[operator]
            return "bullish_cross"
        if m_prev > s_prev and m_now < s_now:        # type: ignore[operator]
            return "bearish_cross"
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Cross-commodity correlation matrix
# ─────────────────────────────────────────────────────────────────────────────

def compute_correlation_matrix(
    series_map: dict[CommodityCode, PriceSeries],
    window:     int = 60,
) -> dict[tuple[CommodityCode, CommodityCode], float]:
    """
    Pearson correlation of daily log-returns for all commodity pairs.
    Only uses the most recent `window` observations (aligned by position).
    """

    def _log_returns(series: PriceSeries) -> list[float]:
        vals = series.values[-window - 1:]
        if len(vals) < 2:
            return []
        return [math.log(vals[i] / vals[i - 1]) for i in range(1, len(vals))]

    codes   = list(series_map.keys())
    returns = {c: _log_returns(series_map[c]) for c in codes}
    result: dict[tuple[CommodityCode, CommodityCode], float] = {}

    for i, c1 in enumerate(codes):
        for j, c2 in enumerate(codes):
            if j <= i:
                continue
            r1, r2 = returns[c1], returns[c2]
            n = min(len(r1), len(r2))
            if n < 10:
                continue
            r1, r2 = r1[-n:], r2[-n:]
            try:
                corr = _pearson(r1, r2)
                result[(c1, c2)] = round(corr, 4)
            except Exception:
                pass

    return result


def _pearson(x: list[float], y: list[float]) -> float:
    n   = len(x)
    mx  = sum(x) / n
    my  = sum(y) / n
    num = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    den = math.sqrt(
        sum((xi - mx) ** 2 for xi in x) * sum((yi - my) ** 2 for yi in y)
    )
    return num / den if den else 0.0
