"""
Section 4 — FX Impact Engine

Computes the foreign-exchange impact on commodity costs for a given reporting currency.

Use cases:
  1. A European buyer (EUR) buys LME copper priced in USD:
     - USD/EUR move affects ACTUAL EUR cost even if USD price is flat
  2. A Polish plant buys in EUR but invoices customers in PLN:
     - EUR/PLN move affects margins
  3. Risk hedging: forward rate vs spot sensitivity analysis

Engine features:
  FXRateProvider     — pulls live + historical ECB rates, fallback to last known
  FXImpactCalculator — computes impact, breakeven analysis, hedge effectiveness
  FXHedgeAdvisor     — models forward cover, recommends hedge ratio
  ExposureMatrix     — maps each commodity to its invoice currency + reporting CCY
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog

from .data_sources import ECBDataSource
from .models import CommodityCode, FXImpact, FXRate

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# FX rate cache + provider
# ─────────────────────────────────────────────────────────────────────────────

# Commodity → its natural invoice currency
COMMODITY_CURRENCY: dict[CommodityCode, str] = {
    CommodityCode.STEEL_HRC:        "EUR",
    CommodityCode.STEEL_CRC:        "EUR",
    CommodityCode.STEEL_REBAR:      "EUR",
    CommodityCode.ALUMINIUM_P1020:  "USD",
    CommodityCode.ALUMINIUM_SCRAP:  "EUR",
    CommodityCode.COPPER_GRADE_A:   "USD",
    CommodityCode.COPPER_SCRAP:     "EUR",
    CommodityCode.LUMBER_SOFTWOOD:  "USD",
    CommodityCode.PLYWOOD:          "EUR",
    CommodityCode.OSB:              "EUR",
    CommodityCode.OCC:              "EUR",
    CommodityCode.CONTAINERBOARD:   "EUR",
    CommodityCode.BRENT:            "USD",
    CommodityCode.TTF_GAS:          "EUR",
    CommodityCode.ELECTRICITY_DE:   "EUR",
    CommodityCode.ELECTRICITY_PL:   "EUR",
    CommodityCode.BALTIC_DRY:       "USD",
    CommodityCode.CONTAINER_SPOT:   "USD",
    CommodityCode.ROAD_FREIGHT_EU:  "EUR",
}


class FXRateProvider:
    """
    Maintains FX rate time series for all currency pairs relevant to the portfolio.
    Pairs tracked: EUR/USD, EUR/PLN, EUR/GBP, EUR/CNY, USD/PLN
    """

    PAIRS = [
        ("EUR", "USD"),
        ("EUR", "PLN"),
        ("EUR", "GBP"),
        ("EUR", "CNY"),
    ]

    def __init__(self, ecb_source: ECBDataSource | None = None) -> None:
        self._ecb    = ecb_source or ECBDataSource()
        # Cache: (base, quote) → list of (ts, rate)
        self._series: dict[tuple[str, str], list[tuple[datetime, float]]] = {}

    async def refresh(self, days: int = 365) -> None:
        for base, quote in self.PAIRS:
            try:
                rates = await self._ecb.fetch_fx(base=base, quote=quote, days=days)
                self._series[(base, quote)] = rates
                logger.info("fx_rates_refreshed", pair=f"{base}/{quote}", points=len(rates))
            except Exception as exc:
                logger.warning("fx_refresh_error", pair=f"{base}/{quote}", error=str(exc))

    def get_rate(
        self,
        base:  str,
        quote: str,
        ts:    datetime | None = None,
    ) -> float | None:
        """Get rate at timestamp (or latest if ts=None)."""
        key    = (base.upper(), quote.upper())
        series = self._series.get(key, [])
        if not series:
            return None
        if ts is None:
            return series[-1][1]
        # Closest point at or before ts
        candidates = [(t, r) for t, r in series if t <= ts]
        return candidates[-1][1] if candidates else series[0][1]

    def get_rate_n_days_ago(self, base: str, quote: str, days: int) -> float | None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        return self.get_rate(base, quote, cutoff)

    def get_rate_series(self, base: str, quote: str) -> list[tuple[datetime, float]]:
        return self._series.get((base.upper(), quote.upper()), [])

    def cross_rate(self, ccy1: str, ccy2: str) -> float | None:
        """Cross-rate via EUR if direct pair not available."""
        if ccy1 == ccy2:
            return 1.0
        direct = self.get_rate(ccy1, ccy2)
        if direct:
            return direct
        # Via EUR
        r1 = self.get_rate("EUR", ccy1)
        r2 = self.get_rate("EUR", ccy2)
        if r1 and r2 and r1 != 0:
            return r2 / r1
        return None


# ─────────────────────────────────────────────────────────────────────────────
# FX Impact Calculator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FXBreakeven:
    """Price point where FX move exactly offsets commodity price move."""
    commodity:       CommodityCode
    reporting_ccy:   str
    commodity_ccy:   str
    breakeven_rate:  float     # exchange rate at which EUR cost = target
    current_rate:    float
    pct_move_needed: float


@dataclass
class HedgeScenario:
    hedge_ratio:      float     # 0.0–1.0
    forward_rate:     float
    effective_rate:   float     # blended spot + forward
    cost_in_rpt_ccy: float
    vs_unhedged:      float     # cost delta vs fully unhedged position
    hedge_cost_bps:   float     # forward premium cost in basis points


class FXImpactCalculator:
    """
    Calculates EUR-equivalent cost impact of FX moves on commodity positions.
    """

    def __init__(self, provider: FXRateProvider, reporting_ccy: str = "EUR") -> None:
        self._provider      = provider
        self._reporting_ccy = reporting_ccy

    def compute_impact(
        self,
        commodity:      CommodityCode,
        price_native:   float,          # price in commodity's invoice currency
        quantity:       float = 1.0,
    ) -> FXImpact | None:
        commodity_ccy = COMMODITY_CURRENCY.get(commodity, "EUR")

        if commodity_ccy == self._reporting_ccy:
            # No FX exposure
            return FXImpact(
                commodity       = commodity,
                reporting_ccy   = self._reporting_ccy,
                commodity_ccy   = commodity_ccy,
                spot_rate       = 1.0,
                rate_30d_ago    = 1.0,
                rate_1y_ago     = 1.0,
                price_native    = price_native,
                price_local     = price_native,
                fx_impact_30d_pct = 0.0,
                fx_impact_1y_pct  = 0.0,
            )

        spot        = self._provider.cross_rate(self._reporting_ccy, commodity_ccy)
        rate_30d    = self._provider.get_rate_n_days_ago(self._reporting_ccy, commodity_ccy, 30)
        rate_1y     = self._provider.get_rate_n_days_ago(self._reporting_ccy, commodity_ccy, 365)

        if spot is None:
            return None

        price_local = price_native / spot  # convert to reporting CCY

        fx_30d = ((spot - rate_30d) / rate_30d * 100) if rate_30d else None
        fx_1y  = ((spot - rate_1y)  / rate_1y  * 100) if rate_1y  else None

        return FXImpact(
            commodity         = commodity,
            reporting_ccy     = self._reporting_ccy,
            commodity_ccy     = commodity_ccy,
            spot_rate         = spot,
            rate_30d_ago      = rate_30d or spot,
            rate_1y_ago       = rate_1y  or spot,
            price_native      = price_native,
            price_local       = round(price_local, 4),
            fx_impact_30d_pct = round(fx_30d or 0.0, 3),
            fx_impact_1y_pct  = round(fx_1y  or 0.0, 3),
        )

    def compute_portfolio_fx_impact(
        self,
        positions: list[tuple[CommodityCode, float, float]],  # (commodity, price_native, qty)
    ) -> dict[str, Any]:
        """
        Aggregate FX impact across multiple commodity positions.
        Returns total value in reporting CCY at spot vs at rates from 30d/1y ago.
        """
        total_spot   = 0.0
        total_30d    = 0.0
        total_1y     = 0.0
        impacts      = []

        for commodity, price, qty in positions:
            impact = self.compute_impact(commodity, price, qty)
            if impact is None:
                continue
            ccy = COMMODITY_CURRENCY.get(commodity, "EUR")

            def _to_reporting(native: float, rate: float) -> float:
                return native / rate if rate else native

            spot_rate   = impact.spot_rate
            rate_30d    = impact.rate_30d_ago
            rate_1y     = impact.rate_1y_ago
            val_spot    = _to_reporting(price * qty, spot_rate)
            val_30d     = _to_reporting(price * qty, rate_30d)
            val_1y      = _to_reporting(price * qty, rate_1y)

            total_spot += val_spot
            total_30d  += val_30d
            total_1y   += val_1y

            impacts.append({
                "commodity":       commodity.value,
                "currency":        ccy,
                "price_native":    price,
                "price_local":     impact.price_local,
                "fx_impact_30d":   impact.fx_impact_30d_pct,
                "fx_impact_1y":    impact.fx_impact_1y_pct,
                "value_reporting": round(val_spot, 2),
            })

        return {
            "reporting_ccy":       self._reporting_ccy,
            "total_value_spot":    round(total_spot, 2),
            "total_value_30d_ago": round(total_30d, 2),
            "total_value_1y_ago":  round(total_1y, 2),
            "fx_delta_30d":        round(total_spot - total_30d, 2),
            "fx_delta_1y":         round(total_spot - total_1y, 2),
            "fx_delta_30d_pct":    round((total_spot - total_30d) / total_30d * 100 if total_30d else 0, 3),
            "positions":           impacts,
        }

    def breakeven_analysis(
        self,
        commodity:      CommodityCode,
        current_price:  float,
        target_cost:    float,          # desired cost in reporting CCY
    ) -> FXBreakeven | None:
        """
        At what FX rate would the commodity cost exactly `target_cost` in reporting CCY?
        """
        commodity_ccy = COMMODITY_CURRENCY.get(commodity, self._reporting_ccy)
        spot          = self._provider.cross_rate(self._reporting_ccy, commodity_ccy)
        if spot is None:
            return None
        breakeven_rate = current_price / target_cost if target_cost else spot
        pct_move       = (breakeven_rate - spot) / spot * 100
        return FXBreakeven(
            commodity       = commodity,
            reporting_ccy   = self._reporting_ccy,
            commodity_ccy   = commodity_ccy,
            breakeven_rate  = round(breakeven_rate, 6),
            current_rate    = round(spot, 6),
            pct_move_needed = round(pct_move, 3),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Hedge Advisor
# ─────────────────────────────────────────────────────────────────────────────

_INTEREST_RATE_DIFFERENTIALS: dict[tuple[str, str], float] = {
    # Approximate annual rate differentials (USD rate - EUR rate) → forward premium
    ("EUR", "USD"): -0.015,    # USD higher by ~1.5% → USD forward at premium
    ("EUR", "PLN"): 0.030,     # PLN rates higher by ~3%
    ("EUR", "GBP"): -0.005,
    ("EUR", "CNY"): -0.020,
}


def forward_rate(
    spot:        float,
    base:        str,
    quote:       str,
    months:      int,
    ir_diff:     float | None = None,  # override interest rate differential
) -> float:
    """
    Simplified Interest Rate Parity forward rate calculation.
    F = S × (1 + r_quote × t) / (1 + r_base × t)
    """
    if ir_diff is None:
        ir_diff = _INTEREST_RATE_DIFFERENTIALS.get((base.upper(), quote.upper()), 0.0)
    t = months / 12.0
    return spot * (1 + ir_diff * t)


class FXHedgeAdvisor:
    """
    Models hedging strategies for commodity FX exposures.
    Recommends hedge ratio and calculates forward cost.
    """

    def __init__(self, provider: FXRateProvider, reporting_ccy: str = "EUR") -> None:
        self._provider      = provider
        self._reporting_ccy = reporting_ccy

    def model_hedge_scenarios(
        self,
        commodity:     CommodityCode,
        price_native:  float,
        quantity:      float,
        horizon_months: int = 3,
    ) -> list[HedgeScenario]:
        """
        Returns hedge scenarios at 0%, 25%, 50%, 75%, 100% coverage.
        """
        commodity_ccy = COMMODITY_CURRENCY.get(commodity, self._reporting_ccy)
        if commodity_ccy == self._reporting_ccy:
            return []

        spot = self._provider.cross_rate(self._reporting_ccy, commodity_ccy)
        if spot is None:
            return []

        fwd  = forward_rate(spot, self._reporting_ccy, commodity_ccy, horizon_months)
        total_native = price_native * quantity

        scenarios = []
        for ratio in (0.0, 0.25, 0.50, 0.75, 1.0):
            hedged_portion   = total_native * ratio
            unhedged_portion = total_native * (1 - ratio)
            effective_rate   = (hedged_portion / fwd + unhedged_portion / spot) / total_native if total_native else 1 / spot
            cost_rpt         = total_native * effective_rate
            unhedged_cost    = total_native / spot
            hedge_cost_bps   = abs(fwd - spot) / spot * 10000 * ratio

            scenarios.append(HedgeScenario(
                hedge_ratio      = ratio,
                forward_rate     = round(fwd, 6),
                effective_rate   = round(effective_rate, 6),
                cost_in_rpt_ccy  = round(cost_rpt, 2),
                vs_unhedged      = round(cost_rpt - unhedged_cost, 2),
                hedge_cost_bps   = round(hedge_cost_bps, 1),
            ))

        return scenarios

    def recommend_hedge_ratio(
        self,
        commodity:     CommodityCode,
        horizon_months: int = 3,
        volatility:    float = 0.10,    # annualised FX vol
        risk_aversion: float = 0.5,     # 0 = fully speculative, 1 = fully risk-averse
    ) -> float:
        """
        Minimum variance hedge ratio (MV hedge):
          h* = ρ × σ_spot / σ_fwd  (simplified: uses vol and risk aversion parameter)
        Returns recommended hedge ratio 0.0–1.0.
        """
        t    = horizon_months / 12.0
        fwd_vol = volatility * math.sqrt(t)
        optimal = min(1.0, risk_aversion * (1 - math.exp(-fwd_vol * 2)))
        return round(optimal, 2)
