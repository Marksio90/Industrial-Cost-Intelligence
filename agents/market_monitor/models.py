"""
Market Monitor — Data Models

ORM + Pydantic schemas for all market monitoring entities.

Commodities tracked:
  STEEL       — HRC, CRC, rebar, wire rod (LME / Platts)
  ALUMINIUM   — Primary ingot, scrap, alloys (LME)
  COPPER      — Grade A cathode, scrap (LME / COMEX)
  WOOD        — Softwood lumber, plywood, OSB (CME Random Length Lumber)
  CARTONS     — Corrugated containerboard, OCC (RISI / Fastmarkets)
  ENERGY      — Brent crude, TTF gas, electricity spot (ICE / EEX)
  TRANSPORT   — Baltic Dry Index, road freight spot, container rates (Drewry / Xeneta)

Price series are stored as time series rows (commodity_id + ts + value).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Enums
# ─────────────────────────────────────────────────────────────────────────────

class CommodityCode(str, Enum):
    # Metals
    STEEL_HRC        = "STEEL_HRC"         # Hot-rolled coil, EUR/t
    STEEL_CRC        = "STEEL_CRC"         # Cold-rolled coil, EUR/t
    STEEL_REBAR      = "STEEL_REBAR"       # Reinforcing bar, EUR/t
    ALUMINIUM_P1020  = "ALUMINIUM_P1020"   # Primary ingot P1020, USD/t
    ALUMINIUM_SCRAP  = "ALUMINIUM_SCRAP"   # Zorba scrap, EUR/t
    COPPER_GRADE_A   = "COPPER_GRADE_A"    # LME Grade A cathode, USD/t
    COPPER_SCRAP     = "COPPER_SCRAP"      # No.1 copper scrap, EUR/t
    # Forest products
    LUMBER_SOFTWOOD  = "LUMBER_SOFTWOOD"   # Random Length Lumber, USD/MBF
    PLYWOOD          = "PLYWOOD"           # Structural plywood, EUR/m³
    OSB              = "OSB"               # Oriented strand board, EUR/m³
    # Packaging
    OCC              = "OCC"               # Old corrugated containers, EUR/t
    CONTAINERBOARD   = "CONTAINERBOARD"    # Linerboard/medium, EUR/t
    # Energy
    BRENT            = "BRENT"             # Brent crude, USD/bbl
    TTF_GAS          = "TTF_GAS"           # TTF natural gas, EUR/MWh
    ELECTRICITY_DE   = "ELECTRICITY_DE"    # German baseload, EUR/MWh
    ELECTRICITY_PL   = "ELECTRICITY_PL"   # Polish baseload, EUR/MWh
    # Transport
    BALTIC_DRY       = "BALTIC_DRY"        # BDI index (no unit)
    CONTAINER_SPOT   = "CONTAINER_SPOT"    # SCFI composite, USD/TEU
    ROAD_FREIGHT_EU  = "ROAD_FREIGHT_EU"   # Transporeon spot, EUR/km


class DataSourceType(str, Enum):
    LME          = "LME"           # London Metal Exchange
    CME          = "CME"           # Chicago Mercantile Exchange
    ICE          = "ICE"           # Intercontinental Exchange
    EEX          = "EEX"           # European Energy Exchange
    PLATTS       = "PLATTS"        # S&P Global Platts
    FASTMARKETS  = "FASTMARKETS"   # Fastmarkets (RISI)
    DREWRY       = "DREWRY"        # Drewry container rates
    XENETA       = "XENETA"        # Xeneta ocean freight
    TRANSPOREON  = "TRANSPOREON"   # Road freight spot
    WORLD_BANK   = "WORLD_BANK"    # World Bank commodity database
    ECB          = "ECB"           # ECB FX reference rates
    EUROSTAT     = "EUROSTAT"      # Eurostat PPI/CPI
    FRED         = "FRED"          # Federal Reserve Economic Data
    CUSTOM_API   = "CUSTOM_API"    # Tenant-defined external API
    MANUAL       = "MANUAL"        # Manual entry by analyst


class PriceFrequency(str, Enum):
    TICK    = "tick"     # Real-time tick
    INTRADAY = "intraday"  # Sub-daily (1h, 4h)
    DAILY   = "daily"
    WEEKLY  = "weekly"
    MONTHLY = "monthly"


class TrendDirection(str, Enum):
    STRONG_UP   = "strong_up"
    UP          = "up"
    FLAT        = "flat"
    DOWN        = "down"
    STRONG_DOWN = "strong_down"


class ShockType(str, Enum):
    PRICE_SPIKE       = "price_spike"       # Sudden large price move
    PRICE_CRASH       = "price_crash"       # Sudden large price drop
    VOLATILITY_REGIME = "volatility_regime" # Vol regime change (GARCH)
    SUPPLY_DISRUPTION = "supply_disruption" # Supply-side news signal
    DEMAND_SURGE      = "demand_surge"      # Demand-side news signal
    CORRELATION_BREAK = "correlation_break" # Cross-commodity correlation breakdown
    FX_SHOCK          = "fx_shock"          # Extreme FX move
    GEOPOLITICAL      = "geopolitical"      # News-based geo event
    SEASONAL_ANOMALY  = "seasonal_anomaly"  # Deviation from seasonal pattern


class AlertSeverity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"


class AlertStatus(str, Enum):
    ACTIVE      = "active"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED    = "resolved"
    SNOOZED     = "snoozed"


class ForecastModel(str, Enum):
    ARIMA        = "ARIMA"
    SARIMA       = "SARIMA"
    PROPHET      = "prophet"
    LSTM         = "LSTM"
    XGBOOST      = "xgboost"
    ENSEMBLE     = "ensemble"   # Weighted average of all above
    NAIVE        = "naive"      # Last-value fallback


# ─────────────────────────────────────────────────────────────────────────────
# Core value objects
# ─────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PricePoint:
    commodity: CommodityCode
    value:     float
    currency:  str          # ISO 4217
    unit:      str          # t, bbl, MWh, MBF, TEU, km, index
    ts:        datetime
    source:    DataSourceType
    frequency: PriceFrequency = PriceFrequency.DAILY


@dataclass(frozen=True)
class FXRate:
    base:  str      # EUR
    quote: str      # USD
    rate:  float
    ts:    datetime
    source: DataSourceType = DataSourceType.ECB


@dataclass
class PriceSeries:
    """In-memory time series for a single commodity."""
    commodity:  CommodityCode
    currency:   str
    unit:       str
    frequency:  PriceFrequency
    points:     list[tuple[datetime, float]] = field(default_factory=list)   # (ts, value)

    @property
    def values(self) -> list[float]:
        return [v for _, v in self.points]

    @property
    def timestamps(self) -> list[datetime]:
        return [ts for ts, _ in self.points]

    @property
    def latest(self) -> float | None:
        return self.points[-1][1] if self.points else None

    @property
    def latest_ts(self) -> datetime | None:
        return self.points[-1][0] if self.points else None

    def pct_change(self, periods: int = 1) -> float | None:
        if len(self.points) < periods + 1:
            return None
        old = self.points[-(periods + 1)][1]
        new = self.points[-1][1]
        return (new - old) / old * 100 if old else None

    def pct_change_ytd(self) -> float | None:
        now = datetime.now(timezone.utc)
        year_start = datetime(now.year, 1, 1, tzinfo=timezone.utc)
        baseline = next(
            (v for ts, v in self.points if ts >= year_start),
            None,
        )
        return (self.latest - baseline) / baseline * 100 if baseline and self.latest else None


# ─────────────────────────────────────────────────────────────────────────────
# Market indicators
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketIndicators:
    """Computed technical + fundamental indicators for a commodity."""
    commodity:      CommodityCode
    ts:             datetime
    # Moving averages
    ma_5:           float | None = None
    ma_20:          float | None = None
    ma_50:          float | None = None
    ma_200:         float | None = None
    ema_12:         float | None = None
    ema_26:         float | None = None
    # Momentum
    rsi_14:         float | None = None     # 0–100; >70 overbought, <30 oversold
    macd:           float | None = None
    macd_signal:    float | None = None
    macd_histogram: float | None = None
    # Volatility
    bollinger_upper:  float | None = None
    bollinger_mid:    float | None = None
    bollinger_lower:  float | None = None
    atr_14:           float | None = None   # Average True Range
    hist_vol_20:      float | None = None   # 20-day historical volatility (annualised)
    hist_vol_60:      float | None = None
    # Trend
    trend:            TrendDirection | None = None
    trend_strength:   float | None = None   # 0–1
    # Performance
    chg_1d_pct:     float | None = None
    chg_1w_pct:     float | None = None
    chg_1m_pct:     float | None = None
    chg_3m_pct:     float | None = None
    chg_ytd_pct:    float | None = None
    chg_1y_pct:     float | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Forecast
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PriceForecast:
    commodity:    CommodityCode
    model:        ForecastModel
    generated_at: datetime
    horizon_days: int
    currency:     str
    unit:         str
    # Point forecasts
    forecast_1w:  float | None = None
    forecast_1m:  float | None = None
    forecast_3m:  float | None = None
    forecast_6m:  float | None = None
    # Confidence intervals (80%)
    ci80_low_1m:  float | None = None
    ci80_high_1m: float | None = None
    ci80_low_3m:  float | None = None
    ci80_high_3m: float | None = None
    # Model quality
    rmse:         float | None = None
    mae:          float | None = None
    mape:         float | None = None
    r2:           float | None = None
    # Full horizon series
    horizon_series: list[tuple[datetime, float, float, float]] = field(default_factory=list)
    # (ts, point, ci80_low, ci80_high)


# ─────────────────────────────────────────────────────────────────────────────
# Shock
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class MarketShock:
    shock_id:         str = field(default_factory=lambda: str(uuid4()))
    commodity:        CommodityCode | None = None   # None = cross-market
    shock_type:       ShockType = ShockType.PRICE_SPIKE
    detected_at:      datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    magnitude:        float = 0.0      # std deviations from rolling mean
    direction:        int  = 1          # +1 = up, -1 = down, 0 = neutral
    affected_commodities: list[CommodityCode] = field(default_factory=list)
    description:      str  = ""
    confidence:       float = 0.0      # 0–1
    news_signals:     list[str] = field(default_factory=list)
    metadata:         dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# FX + Inflation impact
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FXImpact:
    """FX-adjusted price effect on a commodity denominated in foreign currency."""
    commodity:      CommodityCode
    reporting_ccy:  str         # e.g. EUR
    commodity_ccy:  str         # e.g. USD
    spot_rate:      float       # CCY/reporting
    rate_30d_ago:   float
    rate_1y_ago:    float
    price_native:   float       # price in commodity_ccy
    price_local:    float       # price in reporting_ccy at spot
    fx_impact_30d_pct: float    # how much FX moved price in reporting_ccy over 30d
    fx_impact_1y_pct:  float
    hedged_rate:       float | None = None   # forward/hedge rate if applicable


@dataclass
class InflationImpact:
    """Inflation-adjusted (real) price for a commodity."""
    commodity:       CommodityCode
    nominal_price:   float
    real_price:      float       # deflated by PPI or CPI
    deflator:        str         # "PPI_DE" | "CPI_EU" | "PCEPI_US"
    deflator_value:  float       # index value used
    base_year:       int         # e.g. 2020
    real_chg_1y_pct: float
    real_chg_3y_pct: float
    report_date:     datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─────────────────────────────────────────────────────────────────────────────
# Alert
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class AlertRule:
    rule_id:       str = field(default_factory=lambda: str(uuid4()))
    tenant_id:     str = ""
    name:          str = ""
    commodity:     CommodityCode | None = None   # None = applies to all
    # Trigger conditions (any match fires the alert)
    price_above:   float | None = None
    price_below:   float | None = None
    chg_1d_above:  float | None = None    # % change
    chg_1d_below:  float | None = None
    chg_1w_above:  float | None = None
    vol_above:     float | None = None    # annualised vol %
    shock_types:   list[ShockType] = field(default_factory=list)
    severity:      AlertSeverity = AlertSeverity.WARNING
    channels:      list[str] = field(default_factory=list)   # email, slack, webhook, sms
    enabled:       bool = True
    cooldown_min:  int  = 60    # minutes before re-alerting same rule


@dataclass
class Alert:
    alert_id:    str = field(default_factory=lambda: str(uuid4()))
    rule_id:     str = ""
    tenant_id:   str = ""
    commodity:   CommodityCode | None = None
    severity:    AlertSeverity = AlertSeverity.WARNING
    status:      AlertStatus = AlertStatus.ACTIVE
    title:       str = ""
    body:        str = ""
    triggered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    resolved_at:     datetime | None = None
    metadata:        dict[str, Any] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic API schemas
# ─────────────────────────────────────────────────────────────────────────────

class CommodityPriceOut(BaseModel):
    commodity:   str
    value:       float
    currency:    str
    unit:        str
    ts:          datetime
    source:      str
    chg_1d_pct:  float | None = None
    chg_1w_pct:  float | None = None
    chg_1m_pct:  float | None = None
    chg_ytd_pct: float | None = None


class MarketIndicatorsOut(BaseModel):
    commodity:      str
    ts:             datetime
    ma_20:          float | None
    ma_50:          float | None
    rsi_14:         float | None
    macd:           float | None
    macd_signal:    float | None
    bollinger_upper: float | None
    bollinger_lower: float | None
    hist_vol_20:    float | None
    trend:          str | None
    trend_strength: float | None
    chg_1d_pct:     float | None
    chg_1m_pct:     float | None
    chg_ytd_pct:    float | None


class ForecastOut(BaseModel):
    commodity:    str
    model:        str
    generated_at: datetime
    horizon_days: int
    currency:     str
    forecast_1w:  float | None
    forecast_1m:  float | None
    forecast_3m:  float | None
    ci80_low_1m:  float | None
    ci80_high_1m: float | None
    rmse:         float | None
    mape:         float | None


class ShockOut(BaseModel):
    shock_id:    str
    commodity:   str | None
    shock_type:  str
    detected_at: datetime
    magnitude:   float
    direction:   int
    description: str
    confidence:  float


class AlertRuleCreate(BaseModel):
    name:          str
    commodity:     str | None = None
    price_above:   float | None = None
    price_below:   float | None = None
    chg_1d_above:  float | None = None
    chg_1d_below:  float | None = None
    vol_above:     float | None = None
    severity:      str = "WARNING"
    channels:      list[str] = Field(default_factory=list)
    cooldown_min:  int = 60


class AlertOut(BaseModel):
    alert_id:    str
    commodity:   str | None
    severity:    str
    status:      str
    title:       str
    body:        str
    triggered_at: datetime


class MarketSummaryOut(BaseModel):
    """Top-level dashboard summary."""
    generated_at:  datetime
    commodities:   list[CommodityPriceOut]
    active_alerts: int
    shocks_24h:    int
    fx_eur_usd:    float | None
    energy_index:  float | None    # Normalised energy basket
    transport_index: float | None  # Normalised transport basket
