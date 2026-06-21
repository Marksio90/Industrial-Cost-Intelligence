"""
Section 3 — Commodity Tracking

Central tracker that maintains up-to-date price series for all 7 commodity groups.
Runs a periodic refresh loop and exposes in-memory snapshots.

Commodity groups and their sub-indices:
  STEEL     — HRC, CRC, rebar (European mill prices + LME scrap)
  ALUMINIUM — P1020 ingot, scrap (LME settlement)
  COPPER    — Grade A cathode, scrap (LME 3M settlement)
  WOOD      — Random Length Lumber (CME), plywood, OSB (Fastmarkets)
  CARTONS   — OCC recovered paper, linerboard/medium (Fastmarkets/RISI)
  ENERGY    — Brent crude (ICE), TTF natural gas (ICE), electricity spot (EEX)
  TRANSPORT — Baltic Dry Index, container WCI (Drewry), road spot (Transporeon)

Composite indices:
  EnergyIndex     = normalised weighted basket of BRENT(30%) + TTF(40%) + ELEC(30%)
  TransportIndex  = weighted BDI(20%) + CONTAINER(50%) + ROAD(30%)
  MetalsIndex     = weighted STEEL_HRC(40%) + ALUMINIUM(30%) + COPPER(30%)
"""
from __future__ import annotations

import asyncio
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .data_sources import DataSourceRegistry
from .indicators import IndicatorEngine
from .models import (
    CommodityCode,
    MarketIndicators,
    PriceFrequency,
    PriceSeries,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Commodity groups
# ─────────────────────────────────────────────────────────────────────────────

COMMODITY_GROUPS: dict[str, list[CommodityCode]] = {
    "STEEL":     [CommodityCode.STEEL_HRC, CommodityCode.STEEL_CRC, CommodityCode.STEEL_REBAR],
    "ALUMINIUM": [CommodityCode.ALUMINIUM_P1020, CommodityCode.ALUMINIUM_SCRAP],
    "COPPER":    [CommodityCode.COPPER_GRADE_A, CommodityCode.COPPER_SCRAP],
    "WOOD":      [CommodityCode.LUMBER_SOFTWOOD, CommodityCode.PLYWOOD, CommodityCode.OSB],
    "CARTONS":   [CommodityCode.OCC, CommodityCode.CONTAINERBOARD],
    "ENERGY":    [CommodityCode.BRENT, CommodityCode.TTF_GAS,
                  CommodityCode.ELECTRICITY_DE, CommodityCode.ELECTRICITY_PL],
    "TRANSPORT": [CommodityCode.BALTIC_DRY, CommodityCode.CONTAINER_SPOT,
                  CommodityCode.ROAD_FREIGHT_EU],
}

# Weights for composite indices (must sum to 1.0 per index)
_ENERGY_INDEX_WEIGHTS: dict[CommodityCode, float] = {
    CommodityCode.BRENT:          0.30,
    CommodityCode.TTF_GAS:        0.40,
    CommodityCode.ELECTRICITY_DE: 0.30,
}

_TRANSPORT_INDEX_WEIGHTS: dict[CommodityCode, float] = {
    CommodityCode.BALTIC_DRY:      0.20,
    CommodityCode.CONTAINER_SPOT:  0.50,
    CommodityCode.ROAD_FREIGHT_EU: 0.30,
}

_METALS_INDEX_WEIGHTS: dict[CommodityCode, float] = {
    CommodityCode.STEEL_HRC:       0.40,
    CommodityCode.ALUMINIUM_P1020: 0.30,
    CommodityCode.COPPER_GRADE_A:  0.30,
}


# ─────────────────────────────────────────────────────────────────────────────
# Composite index calculation
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CompositeIndex:
    name:        str
    value:       float          # normalised 100 = base period level
    chg_1d_pct:  float | None = None
    chg_1m_pct:  float | None = None
    chg_ytd_pct: float | None = None
    components:  dict[str, float] = field(default_factory=dict)   # commodity → weight
    computed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _normalised_index(
    series_map: dict[CommodityCode, PriceSeries],
    weights:    dict[CommodityCode, float],
    base_window: int = 252,    # trading days to use as "base" for normalisation
) -> float | None:
    """
    Compute weighted normalised index:
      index = Σ weight_i × (current_price_i / base_price_i) × 100
    where base_price_i = mean over last `base_window` trading days.
    """
    total = 0.0
    total_weight = 0.0
    for commodity, weight in weights.items():
        series = series_map.get(commodity)
        if series is None or not series.values:
            continue
        vals = series.values
        current = vals[-1]
        base    = statistics.mean(vals[-base_window:]) if len(vals) >= base_window else statistics.mean(vals)
        if base == 0:
            continue
        total        += weight * (current / base * 100)
        total_weight += weight
    if total_weight == 0:
        return None
    return round(total / total_weight, 2)


# ─────────────────────────────────────────────────────────────────────────────
# Commodity Tracker
# ─────────────────────────────────────────────────────────────────────────────

class CommodityTracker:
    """
    Maintains in-memory PriceSeries for all commodities.
    Refreshes on a configurable interval via _run_refresh_loop().
    """

    def __init__(
        self,
        registry:         DataSourceRegistry,
        history_days:     int = 730,        # 2 years of history
        refresh_interval: int = 3600,       # seconds between data pulls
    ) -> None:
        self._registry         = registry
        self._history_days     = history_days
        self._refresh_interval = refresh_interval
        self._series:     dict[CommodityCode, PriceSeries] = {}
        self._indicators: dict[CommodityCode, MarketIndicators] = {}
        self._indices:    dict[str, CompositeIndex] = {}
        self._indicator_engine = IndicatorEngine()
        self._last_refresh:  datetime | None = None
        self._refresh_lock = asyncio.Lock()

    # ── Initialisation ─────────────────────────────────────────────────────

    async def initialise(self) -> None:
        """Load full history for all commodities on startup."""
        logger.info("commodity_tracker_initialising", commodities=len(list(CommodityCode)))
        await self._refresh_all(full_history=True)

    # ── Refresh loop ───────────────────────────────────────────────────────

    async def run_refresh_loop(self) -> None:
        """Background task — call from app startup."""
        while True:
            try:
                await self._refresh_all(full_history=False)
            except Exception as exc:
                logger.error("refresh_loop_error", error=str(exc))
            await asyncio.sleep(self._refresh_interval)

    async def _refresh_all(self, full_history: bool = False) -> None:
        async with self._refresh_lock:
            tasks = [
                self._refresh_commodity(c, full_history)
                for c in CommodityCode
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
            self._indicators = self._indicator_engine.compute_all(self._series)
            self._recompute_indices()
            self._last_refresh = datetime.now(timezone.utc)
            logger.info("commodity_tracker_refreshed", commodities=len(self._series))

    async def _refresh_commodity(self, commodity: CommodityCode, full_history: bool) -> None:
        try:
            if full_history or commodity not in self._series:
                points = await self._registry.get_history(commodity, self._history_days)
            else:
                points = await self._registry.get_history(commodity, 5)

            if not points:
                return

            # Determine currency + unit from first point
            ccy  = points[0].currency
            unit = points[0].unit
            freq = points[0].frequency

            if commodity not in self._series:
                self._series[commodity] = PriceSeries(
                    commodity = commodity,
                    currency  = ccy,
                    unit      = unit,
                    frequency = freq,
                )

            existing_ts = {ts for ts, _ in self._series[commodity].points}
            new_points  = [(p.ts, p.value) for p in points if p.ts not in existing_ts]
            self._series[commodity].points.extend(new_points)
            self._series[commodity].points.sort(key=lambda x: x[0])

            # Trim to history window
            cutoff = datetime.now(timezone.utc) - timedelta(days=self._history_days + 30)
            self._series[commodity].points = [
                (ts, v) for ts, v in self._series[commodity].points
                if ts >= cutoff
            ]

        except Exception as exc:
            logger.warning("commodity_refresh_error", commodity=commodity.value, error=str(exc))

    def _recompute_indices(self) -> None:
        # Energy Index
        ei = _normalised_index(self._series, _ENERGY_INDEX_WEIGHTS)
        if ei is not None:
            self._indices["energy"] = CompositeIndex(
                name="Energy Index", value=ei,
                components={c.value: w for c, w in _ENERGY_INDEX_WEIGHTS.items()},
            )
        # Transport Index
        ti = _normalised_index(self._series, _TRANSPORT_INDEX_WEIGHTS)
        if ti is not None:
            self._indices["transport"] = CompositeIndex(
                name="Transport Index", value=ti,
                components={c.value: w for c, w in _TRANSPORT_INDEX_WEIGHTS.items()},
            )
        # Metals Index
        mi = _normalised_index(self._series, _METALS_INDEX_WEIGHTS)
        if mi is not None:
            self._indices["metals"] = CompositeIndex(
                name="Metals Index", value=mi,
                components={c.value: w for c, w in _METALS_INDEX_WEIGHTS.items()},
            )

    # ── Public accessors ───────────────────────────────────────────────────

    def get_series(self, commodity: CommodityCode) -> PriceSeries | None:
        return self._series.get(commodity)

    def get_latest_price(self, commodity: CommodityCode) -> float | None:
        s = self._series.get(commodity)
        return s.latest if s else None

    def get_indicators(self, commodity: CommodityCode) -> MarketIndicators | None:
        return self._indicators.get(commodity)

    def get_all_indicators(self) -> dict[CommodityCode, MarketIndicators]:
        return dict(self._indicators)

    def get_index(self, name: str) -> CompositeIndex | None:
        return self._indices.get(name)

    def get_all_indices(self) -> dict[str, CompositeIndex]:
        return dict(self._indices)

    def get_group_summary(self, group: str) -> dict[str, Any]:
        """Returns latest prices + % changes for all commodities in a group."""
        codes   = COMMODITY_GROUPS.get(group.upper(), [])
        summary = []
        for code in codes:
            series = self._series.get(code)
            inds   = self._indicators.get(code)
            summary.append({
                "commodity": code.value,
                "price":     series.latest if series else None,
                "currency":  series.currency if series else None,
                "unit":      series.unit if series else None,
                "chg_1d":    inds.chg_1d_pct if inds else None,
                "chg_1w":    inds.chg_1w_pct if inds else None,
                "chg_1m":    inds.chg_1m_pct if inds else None,
                "chg_ytd":   inds.chg_ytd_pct if inds else None,
                "trend":     inds.trend.value if (inds and inds.trend) else None,
                "rsi_14":    inds.rsi_14 if inds else None,
                "vol_20":    inds.hist_vol_20 if inds else None,
            })
        return {"group": group, "commodities": summary}

    def snapshot(self) -> dict[str, Any]:
        """Full market snapshot for dashboard / API response."""
        return {
            "refreshed_at": self._last_refresh.isoformat() if self._last_refresh else None,
            "groups": {
                group: self.get_group_summary(group)
                for group in COMMODITY_GROUPS
            },
            "indices": {
                name: {
                    "name":  idx.name,
                    "value": idx.value,
                }
                for name, idx in self._indices.items()
            },
        }

    def status(self) -> dict[str, Any]:
        return {
            "tracked_commodities": len(self._series),
            "last_refresh":        self._last_refresh.isoformat() if self._last_refresh else None,
            "indices_computed":    list(self._indices.keys()),
            "data_points_total":   sum(len(s.points) for s in self._series.values()),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Seasonal adjustment helpers
# ─────────────────────────────────────────────────────────────────────────────

# Historical average monthly deviations from annual mean (% over/under)
SEASONAL_PATTERNS: dict[CommodityCode, dict[int, float]] = {
    CommodityCode.LUMBER_SOFTWOOD: {
        1: -8, 2: -5, 3: 2, 4: 8, 5: 12, 6: 10,
        7:  6, 8:  4, 9: 0, 10: -3, 11: -8, 12: -10,
    },
    CommodityCode.STEEL_HRC: {
        1: -3, 2: -1, 3:  3, 4:  5, 5:  4, 6:  2,
        7: -1, 8: -2, 9: -1, 10:  1, 11: -2, 12: -5,
    },
    CommodityCode.ELECTRICITY_DE: {
        1: 20, 2: 15, 3:  5, 4: -5, 5: -10, 6: -8,
        7: -5, 8:  0, 9:  3, 10:  8, 11: 12, 12: 18,
    },
    CommodityCode.BRENT: {
        1: -2, 2: -1, 3:  1, 4:  3, 5:  4, 6:  2,
        7:  3, 8:  4, 9:  2, 10:  0, 11: -3, 12: -4,
    },
}


def seasonal_adjustment_factor(commodity: CommodityCode, month: int) -> float:
    """Returns seasonal factor as a multiplier (1.0 = no adjustment)."""
    patterns = SEASONAL_PATTERNS.get(commodity, {})
    pct      = patterns.get(month, 0.0)
    return 1.0 + pct / 100.0
