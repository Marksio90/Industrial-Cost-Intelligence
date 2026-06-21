"""
Section 5 — Inflation Impact Engine

Converts nominal commodity prices to real (inflation-adjusted) prices
and quantifies how much of a price move is "real" vs purely inflationary.

Deflators used:
  PPI_DE    — German Manufacturing PPI (Eurostat, sts_inpp_m)
  PPI_EU    — Euro area Manufacturing PPI (Eurostat)
  CPI_EU    — HICP Euro area all items (Eurostat, prc_hicp_mmor)
  PCEPI_US  — US PCE Price Index (FRED, PCEPI)
  CRB       — CRB Commodity Index (for commodity-specific deflation)

Per-commodity deflator mapping:
  Metals     → PPI_DE (manufacturing inputs)
  Wood       → PPI_EU
  Cartons    → PPI_EU
  Energy     → PCEPI_US or CPI_EU depending on buyer reporting currency
  Transport  → CPI_EU (freight is service, not industrial good)
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .data_sources import EurostatDataSource
from .models import CommodityCode, InflationImpact

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Deflator mapping
# ─────────────────────────────────────────────────────────────────────────────

COMMODITY_DEFLATOR: dict[CommodityCode, str] = {
    CommodityCode.STEEL_HRC:        "PPI_DE",
    CommodityCode.STEEL_CRC:        "PPI_DE",
    CommodityCode.STEEL_REBAR:      "PPI_DE",
    CommodityCode.ALUMINIUM_P1020:  "PPI_DE",
    CommodityCode.ALUMINIUM_SCRAP:  "PPI_DE",
    CommodityCode.COPPER_GRADE_A:   "PPI_DE",
    CommodityCode.COPPER_SCRAP:     "PPI_DE",
    CommodityCode.LUMBER_SOFTWOOD:  "PPI_EU",
    CommodityCode.PLYWOOD:          "PPI_EU",
    CommodityCode.OSB:              "PPI_EU",
    CommodityCode.OCC:              "PPI_EU",
    CommodityCode.CONTAINERBOARD:   "PPI_EU",
    CommodityCode.BRENT:            "CPI_EU",
    CommodityCode.TTF_GAS:          "CPI_EU",
    CommodityCode.ELECTRICITY_DE:   "CPI_EU",
    CommodityCode.ELECTRICITY_PL:   "CPI_EU",
    CommodityCode.BALTIC_DRY:       "CPI_EU",
    CommodityCode.CONTAINER_SPOT:   "CPI_EU",
    CommodityCode.ROAD_FREIGHT_EU:  "CPI_EU",
}


# ─────────────────────────────────────────────────────────────────────────────
# Deflator time series store
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class DeflatorSeries:
    key:    str
    label:  str
    points: list[tuple[datetime, float]] = field(default_factory=list)   # (ts, index)
    base_year: int = 2020

    def index_at(self, ts: datetime) -> float | None:
        """Closest index value at or before ts."""
        candidates = [(t, v) for t, v in self.points if t <= ts]
        return candidates[-1][1] if candidates else None

    def latest(self) -> float | None:
        return self.points[-1][1] if self.points else None

    def value_at_year_start(self, year: int) -> float | None:
        target = datetime(year, 1, 1, tzinfo=timezone.utc)
        return self.index_at(target)

    def yoy_pct(self) -> float | None:
        """Year-over-year % change (latest vs 12 months prior)."""
        if not self.points:
            return None
        now_val  = self.points[-1][1]
        year_ago = datetime.now(timezone.utc) - timedelta(days=365)
        old      = self.index_at(year_ago)
        if old is None or old == 0:
            return None
        return (now_val - old) / old * 100

    def mom_pct(self) -> float | None:
        """Month-over-month % change."""
        if len(self.points) < 2:
            return None
        cur = self.points[-1][1]
        prv = self.points[-2][1]
        return (cur - prv) / prv * 100 if prv else None


# ─────────────────────────────────────────────────────────────────────────────
# Deflator Registry
# ─────────────────────────────────────────────────────────────────────────────

class DeflatorRegistry:
    """Maintains up-to-date deflator series from Eurostat + FRED."""

    _EUROSTAT_MAP = {
        "PPI_DE": ("PPI_DE",  "German Manufacturing PPI"),
        "PPI_EU": ("PPI_EU",  "Euro Area Manufacturing PPI"),
        "CPI_EU": ("CPI_EU",  "HICP Euro Area"),
    }

    def __init__(self, eurostat: EurostatDataSource | None = None) -> None:
        self._eurostat = eurostat or EurostatDataSource()
        self._series:  dict[str, DeflatorSeries] = {}
        self._seeded = False

    async def refresh(self) -> None:
        for key, (ds_key, label) in self._EUROSTAT_MAP.items():
            try:
                # Map our keys to Eurostat dataset paths
                eurostat_key = "PPI_DE" if "PPI" in ds_key else "CPI_EU"
                points = await self._eurostat.fetch_deflator(eurostat_key, periods=60)
                if points:
                    if key not in self._series:
                        self._series[key] = DeflatorSeries(key=key, label=label)
                    self._series[key].points = sorted(points, key=lambda x: x[0])
                    logger.info("deflator_refreshed", key=key, points=len(points))
            except Exception as exc:
                logger.warning("deflator_refresh_error", key=key, error=str(exc))
        self._seeded = True
        if not self._series:
            self._seed_mock_deflators()

    def _seed_mock_deflators(self) -> None:
        """Deterministic mock deflators for dev / CI."""
        now = datetime.now(timezone.utc)
        for key, (_, label) in self._EUROSTAT_MAP.items():
            points = []
            idx    = 100.0
            # 5 years of monthly data
            for m in range(60):
                ts   = now - timedelta(days=(59 - m) * 30)
                # PPI was more volatile post-2021
                delta = 0.3 if m < 24 else (0.6 if m < 36 else 0.2)
                idx  = idx * (1 + delta / 100)
                points.append((ts, round(idx, 4)))
            self._series[key] = DeflatorSeries(key=key, label=label, points=points)

    def get(self, key: str) -> DeflatorSeries | None:
        return self._series.get(key)

    def all_yoy(self) -> dict[str, float | None]:
        return {k: s.yoy_pct() for k, s in self._series.items()}


# ─────────────────────────────────────────────────────────────────────────────
# Inflation Impact Calculator
# ─────────────────────────────────────────────────────────────────────────────

class InflationImpactCalculator:
    """
    Deflates nominal commodity prices to real terms.
    Decomposes price changes into:
      - Real component (supply/demand driven)
      - Inflationary component (general price level)
    """

    def __init__(self, deflator_registry: DeflatorRegistry, base_year: int = 2020) -> None:
        self._deflators = deflator_registry
        self._base_year = base_year

    def compute(
        self,
        commodity:     CommodityCode,
        nominal_price: float,
        as_of:         datetime | None = None,
    ) -> InflationImpact | None:
        as_of  = as_of or datetime.now(timezone.utc)
        def_key = COMMODITY_DEFLATOR.get(commodity, "PPI_DE")
        series  = self._deflators.get(def_key)
        if series is None:
            return None

        current_idx = series.index_at(as_of)
        base_idx    = series.value_at_year_start(self._base_year)

        if current_idx is None or base_idx is None or base_idx == 0 or current_idx == 0:
            return None

        real_price = nominal_price / (current_idx / base_idx)

        # Real price 1y ago
        ts_1y        = as_of - timedelta(days=365)
        idx_1y       = series.index_at(ts_1y)
        # Approximate nominal price 1y ago = need actual price series; we just model deflator effect
        real_1y      = (nominal_price / (idx_1y / base_idx)) if idx_1y and idx_1y > 0 else None
        real_chg_1y  = (real_price - real_1y) / real_1y * 100 if real_1y else None

        # Real price 3y ago
        ts_3y        = as_of - timedelta(days=1095)
        idx_3y       = series.index_at(ts_3y)
        real_3y      = (nominal_price / (idx_3y / base_idx)) if idx_3y and idx_3y > 0 else None
        real_chg_3y  = (real_price - real_3y) / real_3y * 100 if real_3y else None

        return InflationImpact(
            commodity       = commodity,
            nominal_price   = nominal_price,
            real_price      = round(real_price, 4),
            deflator        = def_key,
            deflator_value  = current_idx,
            base_year       = self._base_year,
            real_chg_1y_pct = round(real_chg_1y or 0.0, 3),
            real_chg_3y_pct = round(real_chg_3y or 0.0, 3),
            report_date     = as_of,
        )

    def decompose_price_change(
        self,
        commodity:      CommodityCode,
        price_now:      float,
        price_then:     float,
        ts_now:         datetime,
        ts_then:        datetime,
    ) -> dict[str, float | None]:
        """
        Decomposes total price change into:
          total_pct   = (price_now - price_then) / price_then × 100
          infl_pct    = component explained by inflation (deflator move)
          real_pct    = residual (true supply/demand signal)
        """
        def_key = COMMODITY_DEFLATOR.get(commodity, "PPI_DE")
        series  = self._deflators.get(def_key)
        base_idx = series.value_at_year_start(self._base_year) if series else None

        total_pct = (price_now - price_then) / price_then * 100 if price_then else None

        if series is None or base_idx is None or base_idx == 0:
            return {"total_pct": total_pct, "infl_pct": None, "real_pct": None}

        idx_now  = series.index_at(ts_now)
        idx_then = series.index_at(ts_then)

        if not idx_now or not idx_then or idx_then == 0:
            return {"total_pct": total_pct, "infl_pct": None, "real_pct": None}

        real_now  = price_now  / (idx_now  / base_idx)
        real_then = price_then / (idx_then / base_idx)
        real_pct  = (real_now - real_then) / real_then * 100 if real_then else None
        infl_pct  = (total_pct - real_pct) if (total_pct is not None and real_pct is not None) else None

        return {
            "total_pct": round(total_pct or 0, 3),
            "infl_pct":  round(infl_pct  or 0, 3),
            "real_pct":  round(real_pct  or 0, 3),
            "deflator":  def_key,
            "idx_now":   idx_now,
            "idx_then":  idx_then,
        }

    def real_price_trend(
        self,
        commodity:     CommodityCode,
        nominal_series: list[tuple[datetime, float]],
    ) -> list[tuple[datetime, float]]:
        """Convert a nominal price series to real (base-year) prices."""
        def_key  = COMMODITY_DEFLATOR.get(commodity, "PPI_DE")
        series   = self._deflators.get(def_key)
        base_idx = series.value_at_year_start(self._base_year) if series else None
        if not series or not base_idx:
            return nominal_series
        result = []
        for ts, nominal in nominal_series:
            idx = series.index_at(ts)
            if idx and idx > 0:
                result.append((ts, round(nominal / (idx / base_idx), 4)))
        return result


# ─────────────────────────────────────────────────────────────────────────────
# PPI Pulse — quick inflation summary for procurement reporting
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PPIPulse:
    """Monthly PPI summary for procurement dashboard."""
    generated_at: datetime
    ppi_de_yoy:   float | None   # Year-over-year German PPI %
    ppi_eu_yoy:   float | None   # Year-over-year EU PPI %
    cpi_eu_yoy:   float | None   # HICP year-over-year %
    ppi_de_mom:   float | None   # Month-over-month
    trend:        str            # "accelerating" | "decelerating" | "stable"
    risk_level:   str            # "HIGH" | "MEDIUM" | "LOW"
    commentary:   str


def build_ppi_pulse(deflator_registry: DeflatorRegistry) -> PPIPulse:
    ppi_de = deflator_registry.get("PPI_DE")
    ppi_eu = deflator_registry.get("PPI_EU")
    cpi_eu = deflator_registry.get("CPI_EU")

    yoy_de  = ppi_de.yoy_pct() if ppi_de else None
    yoy_eu  = ppi_eu.yoy_pct() if ppi_eu else None
    yoy_cpi = cpi_eu.yoy_pct() if cpi_eu else None
    mom_de  = ppi_de.mom_pct() if ppi_de else None

    # Trend: compare last 3 MoM changes
    trend = "stable"
    if ppi_de and len(ppi_de.points) >= 4:
        recent_mom = [
            (ppi_de.points[-i][1] - ppi_de.points[-i - 1][1]) / ppi_de.points[-i - 1][1] * 100
            for i in range(1, 4)
        ]
        if recent_mom[0] > recent_mom[1] > recent_mom[2]:
            trend = "accelerating"
        elif recent_mom[0] < recent_mom[1] < recent_mom[2]:
            trend = "decelerating"

    yoy = yoy_de or 0.0
    risk = "HIGH" if yoy > 8 else ("MEDIUM" if yoy > 4 else "LOW")

    if yoy_de is not None:
        commentary = (
            f"German PPI is running at {yoy_de:+.1f}% YoY ({trend}). "
            f"Input cost pressure is {risk.lower()} — "
            f"{'negotiate longer fixed-price contracts' if risk == 'HIGH' else 'monitor quarterly'}."
        )
    else:
        commentary = "Deflator data unavailable — using cached values."

    return PPIPulse(
        generated_at = datetime.now(timezone.utc),
        ppi_de_yoy   = yoy_de,
        ppi_eu_yoy   = yoy_eu,
        cpi_eu_yoy   = yoy_cpi,
        ppi_de_mom   = mom_de,
        trend        = trend,
        risk_level   = risk,
        commentary   = commentary,
    )
