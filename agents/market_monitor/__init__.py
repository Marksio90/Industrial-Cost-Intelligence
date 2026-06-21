"""
ICI Market Monitor Agent

Architecture overview — see individual modules:
  models.py            § Data models: CommodityCode, PriceSeries, MarketShock, Alert, Forecast
  data_sources.py      § Section 1:  Data source connectors + circuit breaker registry
  indicators.py        § Section 2:  Technical indicators (SMA, EMA, RSI, MACD, Bollinger, ATR)
  commodity_tracker.py § Section 3:  Per-commodity tracking + composite indices + seasonal factors
  fx_engine.py         § Section 4:  FX impact calculator + hedge advisor
  inflation_engine.py  § Section 5:  PPI/CPI deflators + real price decomposition
  forecasting.py       § Section 6:  Ensemble forecaster (ARIMA, Prophet, XGBoost, Naive)
  shock_detection.py   § Section 7:  Z-score, CUSUM, volatility regime, correlation breakdown
  alert_engine.py      § Section 8:  Rule evaluator + deduplicator + multi-channel router
  dashboard.py         § Section 9:  Panel data builders (grid, technical, forecast, alerts)
  api.py               § Section 10: REST API + WebSocket real-time stream
  events.py            § Section 11: Domain events + Redis stream publisher

Commodities tracked:
  STEEL     (HRC, CRC, rebar)
  ALUMINIUM (P1020, scrap)
  COPPER    (Grade A, scrap)
  WOOD      (Lumber, plywood, OSB)
  CARTONS   (OCC, containerboard)
  ENERGY    (Brent, TTF gas, electricity DE/PL)
  TRANSPORT (Baltic Dry, container spot, road EU)

Data sources:
  LME, Platts (S&P Global), ICE, EEX, CME (Quandl),
  Fastmarkets/RISI, Drewry, ECB, Eurostat, FRED
"""

from .api import router as market_monitor_router
from .models import (
    CommodityCode,
    PricePoint,
    PriceSeries,
    MarketIndicators,
    PriceForecast,
    MarketShock,
    Alert,
    AlertRule,
    AlertSeverity,
    ShockType,
    TrendDirection,
    ForecastModel,
)
from .commodity_tracker import CommodityTracker, COMMODITY_GROUPS
from .data_sources import DataSourceRegistry, build_registry
from .fx_engine import FXImpactCalculator, FXRateProvider
from .inflation_engine import InflationImpactCalculator, DeflatorRegistry
from .shock_detection import ShockDetectionEngine
from .alert_engine import AlertManager, AlertRouter, ChannelConfig
from .forecasting import EnsembleForecaster
from .events import MarketEventPublisher, MarketEventType
from .indicators import IndicatorEngine

__all__ = [
    "market_monitor_router",
    # Models
    "CommodityCode",
    "PricePoint",
    "PriceSeries",
    "MarketIndicators",
    "PriceForecast",
    "MarketShock",
    "Alert",
    "AlertRule",
    "AlertSeverity",
    "ShockType",
    "TrendDirection",
    "ForecastModel",
    # Core services
    "CommodityTracker",
    "COMMODITY_GROUPS",
    "DataSourceRegistry",
    "build_registry",
    "FXImpactCalculator",
    "FXRateProvider",
    "InflationImpactCalculator",
    "DeflatorRegistry",
    "ShockDetectionEngine",
    "AlertManager",
    "AlertRouter",
    "ChannelConfig",
    "EnsembleForecaster",
    "MarketEventPublisher",
    "MarketEventType",
    "IndicatorEngine",
]
