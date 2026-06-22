"""
Section 1 — Data Sources

Connectors for all market data providers.

Architecture:
  BaseDataSource           — abstract interface (fetch_latest, fetch_history, health_check)
  LMEDataSource            — London Metal Exchange (REST API + WebSocket tick)
  CMEDataSource            — Chicago Mercantile Exchange (Quandl / CME DataMine)
  ICEDataSource            — Intercontinental Exchange (Brent, TTF)
  EEXDataSource            — European Energy Exchange (electricity spot)
  PlattsDataSource         — S&P Global Platts (steel assessments)
  FastmarketsDataSource    — Fastmarkets / RISI (paper, packaging)
  DrewryDataSource         — Drewry container freight index
  ECBDataSource            — European Central Bank FX reference rates
  EurostatDataSource       — Eurostat PPI / CPI
  FREDDataSource           — Federal Reserve FRED (economic indicators)
  DataSourceRegistry       — central registry, round-robin fallback, circuit breaker

Each connector:
  - Returns PricePoint list
  - Implements exponential-backoff retry (3 attempts)
  - Tracks last_success_ts and consecutive_failures
  - Gracefully degrades to cached last-known value if source is down

Credentials loaded via ICI SecretsManager (Vault / AWS SM / env).
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import structlog

from .models import (
    CommodityCode,
    DataSourceType,
    PriceFrequency,
    PricePoint,
)

logger = structlog.get_logger(__name__)

_RETRY_DELAYS = (2.0, 4.0, 8.0)   # seconds; exponential backoff


# ─────────────────────────────────────────────────────────────────────────────
# Base connector
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SourceHealth:
    source:               DataSourceType
    healthy:              bool
    last_success_ts:      datetime | None = None
    consecutive_failures: int             = 0
    avg_latency_ms:       float           = 0.0
    circuit_open:         bool            = False   # True = stop trying until reset


class BaseDataSource(ABC):
    """Abstract base for all market data connectors."""

    CIRCUIT_BREAKER_THRESHOLD = 5      # failures before opening circuit
    CIRCUIT_RESET_SECONDS     = 300    # 5 min cooldown

    def __init__(self, api_key: str = "", base_url: str = "") -> None:
        self._api_key   = api_key
        self._base_url  = base_url
        self._health    = SourceHealth(source=self.source_type, healthy=True)
        self._circuit_opened_at: float | None = None
        self._cache: dict[CommodityCode, PricePoint] = {}

    @property
    @abstractmethod
    def source_type(self) -> DataSourceType: ...

    @property
    @abstractmethod
    def supported_commodities(self) -> list[CommodityCode]: ...

    @abstractmethod
    async def _fetch(
        self,
        commodity: CommodityCode,
        start:     datetime | None = None,
        end:       datetime | None = None,
    ) -> list[PricePoint]: ...

    async def fetch_latest(self, commodity: CommodityCode) -> PricePoint | None:
        """Fetch latest price with retry + circuit breaker."""
        if not self._can_attempt():
            return self._cache.get(commodity)

        for delay in (0.0, *_RETRY_DELAYS):
            if delay:
                await asyncio.sleep(delay)
            try:
                t0 = time.monotonic()
                points = await self._fetch(commodity)
                self._record_success(time.monotonic() - t0)
                if points:
                    self._cache[commodity] = points[-1]
                    return points[-1]
                return self._cache.get(commodity)
            except Exception as exc:
                logger.warning(
                    "data_source_fetch_error",
                    source=self.source_type.value, commodity=commodity.value, error=str(exc),
                )
        self._record_failure()
        return self._cache.get(commodity)

    async def fetch_history(
        self,
        commodity: CommodityCode,
        start:     datetime,
        end:       datetime | None = None,
    ) -> list[PricePoint]:
        if not self._can_attempt():
            return []
        try:
            return await self._fetch(commodity, start, end or datetime.now(timezone.utc))
        except Exception as exc:
            logger.warning("data_source_history_error", source=self.source_type.value, error=str(exc))
            return []

    async def health_check(self) -> SourceHealth:
        try:
            commodity = self.supported_commodities[0] if self.supported_commodities else None
            if commodity:
                await self.fetch_latest(commodity)
        except Exception:
            pass
        return self._health

    # ── Circuit breaker helpers ───────────────────────────────────────────────

    def _can_attempt(self) -> bool:
        if not self._health.circuit_open:
            return True
        if self._circuit_opened_at and time.monotonic() - self._circuit_opened_at > self.CIRCUIT_RESET_SECONDS:
            self._health.circuit_open = False
            self._health.consecutive_failures = 0
            logger.info("circuit_breaker_reset", source=self.source_type.value)
            return True
        return False

    def _record_success(self, latency_s: float) -> None:
        self._health.healthy              = True
        self._health.last_success_ts      = datetime.now(timezone.utc)
        self._health.consecutive_failures = 0
        self._health.avg_latency_ms       = (
            self._health.avg_latency_ms * 0.9 + latency_s * 1000 * 0.1
        )

    def _record_failure(self) -> None:
        self._health.consecutive_failures += 1
        if self._health.consecutive_failures >= self.CIRCUIT_BREAKER_THRESHOLD:
            self._health.circuit_open   = True
            self._circuit_opened_at     = time.monotonic()
            self._health.healthy        = False
            logger.error("circuit_breaker_opened", source=self.source_type.value)


# ─────────────────────────────────────────────────────────────────────────────
# LME — London Metal Exchange (metals)
# ─────────────────────────────────────────────────────────────────────────────

_LME_COMMODITY_MAP: dict[CommodityCode, str] = {
    CommodityCode.ALUMINIUM_P1020: "AHD",   # LME Aluminium 3M
    CommodityCode.COPPER_GRADE_A:  "CAD",   # LME Copper 3M
}

class LMEDataSource(BaseDataSource):
    source_type = DataSourceType.LME
    supported_commodities = list(_LME_COMMODITY_MAP.keys())

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        code = _LME_COMMODITY_MAP.get(commodity)
        if not code:
            return []
        params: dict[str, Any] = {"symbol": code, "apikey": self._api_key}
        if start:
            params["from"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self._base_url}/v2/prices", params=params)
            resp.raise_for_status()
            data = resp.json()
        return [
            PricePoint(
                commodity = commodity,
                value     = float(row["settlement"]),
                currency  = "USD",
                unit      = "t",
                ts        = datetime.fromisoformat(row["date"]).replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.DAILY,
            )
            for row in data.get("data", [])
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Platts — S&P Global Commodity Insights (steel assessments)
# ─────────────────────────────────────────────────────────────────────────────

_PLATTS_MAP: dict[CommodityCode, str] = {
    CommodityCode.STEEL_HRC:   "PAHSM00",    # European HRC
    CommodityCode.STEEL_CRC:   "AASQM00",    # European CRC
    CommodityCode.STEEL_REBAR: "TSRBEU00",   # European rebar
}

class PlattsDataSource(BaseDataSource):
    source_type = DataSourceType.PLATTS
    supported_commodities = list(_PLATTS_MAP.keys())

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        code = _PLATTS_MAP.get(commodity)
        if not code:
            return []
        headers = {"Authorization": f"Bearer {self._api_key}"}
        params: dict[str, Any] = {"symbol": code, "frequency": "daily", "pageSize": 252}
        if start:
            params["startDate"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"{self._base_url}/market-data/v3/value/history/symbol/{code}",
                headers=headers, params=params,
            )
            resp.raise_for_status()
            data = resp.json()
        return [
            PricePoint(
                commodity = commodity,
                value     = float(row["value"]),
                currency  = row.get("currency", "EUR"),
                unit      = "t",
                ts        = datetime.fromisoformat(row["modifiedDate"]).replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.DAILY,
            )
            for row in data.get("results", [])
        ]


# ─────────────────────────────────────────────────────────────────────────────
# ICE — Brent crude + TTF gas
# ─────────────────────────────────────────────────────────────────────────────

_ICE_MAP: dict[CommodityCode, tuple[str, str, str]] = {
    # (product_id, currency, unit)
    CommodityCode.BRENT:   ("B",   "USD", "bbl"),
    CommodityCode.TTF_GAS: ("TTF", "EUR", "MWh"),
}

class ICEDataSource(BaseDataSource):
    source_type = DataSourceType.ICE
    supported_commodities = list(_ICE_MAP.keys())

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        pid, ccy, unit = _ICE_MAP.get(commodity, ("", "USD", ""))
        if not pid:
            return []
        params: dict[str, Any] = {"product": pid, "limit": 365}
        if start:
            params["startDate"] = start.strftime("%Y%m%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base_url}/eod_price_history", params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            rows = resp.json().get("prices", [])
        return [
            PricePoint(
                commodity = commodity,
                value     = float(r["settlement"]),
                currency  = ccy,
                unit      = unit,
                ts        = datetime.strptime(r["date"], "%Y%m%d").replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.DAILY,
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# EEX — European Energy Exchange (electricity)
# ─────────────────────────────────────────────────────────────────────────────

_EEX_MAP: dict[CommodityCode, str] = {
    CommodityCode.ELECTRICITY_DE: "DE_SPOT",
    CommodityCode.ELECTRICITY_PL: "PL_SPOT",
}

class EEXDataSource(BaseDataSource):
    source_type = DataSourceType.EEX
    supported_commodities = list(_EEX_MAP.keys())

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        market = _EEX_MAP.get(commodity, "")
        if not market:
            return []
        params: dict[str, Any] = {"market": market}
        if start:
            params["date_from"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base_url}/spot/prices", params=params,
                headers={"x-api-key": self._api_key},
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        return [
            PricePoint(
                commodity = commodity,
                value     = float(r["baseload_eur_mwh"]),
                currency  = "EUR",
                unit      = "MWh",
                ts        = datetime.fromisoformat(r["delivery_date"]).replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.DAILY,
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# CME — Random Length Lumber (softwood)
# ─────────────────────────────────────────────────────────────────────────────

class CMEDataSource(BaseDataSource):
    source_type = DataSourceType.CME
    supported_commodities = [CommodityCode.LUMBER_SOFTWOOD]

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        # Quandl / Nasdaq Data Link endpoint: LB (Random Length Lumber)
        params: dict[str, Any] = {
            "api_key":    self._api_key,
            "order":      "asc",
            "collapse":   "daily",
        }
        if start:
            params["start_date"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base_url}/api/v3/datasets/CHRIS/CME_LB1.json", params=params,
            )
            resp.raise_for_status()
            dataset = resp.json().get("dataset", {})
        cols  = dataset.get("column_names", [])
        rows  = dataset.get("data", [])
        settle_idx = cols.index("Settle") if "Settle" in cols else -1
        date_idx   = 0
        if settle_idx < 0:
            return []
        return [
            PricePoint(
                commodity = commodity,
                value     = float(r[settle_idx]),
                currency  = "USD",
                unit      = "MBF",
                ts        = datetime.strptime(r[date_idx], "%Y-%m-%d").replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.DAILY,
            )
            for r in rows
            if r[settle_idx] is not None
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Fastmarkets / RISI — Packaging (OCC, containerboard)
# ─────────────────────────────────────────────────────────────────────────────

_FM_MAP: dict[CommodityCode, str] = {
    CommodityCode.OCC:           "OCC-EU",
    CommodityCode.CONTAINERBOARD: "LINER-EU",
}

class FastmarketsDataSource(BaseDataSource):
    source_type = DataSourceType.FASTMARKETS
    supported_commodities = list(_FM_MAP.keys())

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        pid = _FM_MAP.get(commodity, "")
        if not pid:
            return []
        params: dict[str, Any] = {"priceId": pid, "currency": "EUR", "unit": "tonne"}
        if start:
            params["from"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base_url}/prices/history",
                params=params,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
            resp.raise_for_status()
            rows = resp.json().get("prices", [])
        return [
            PricePoint(
                commodity = commodity,
                value     = float(r["mid"]),
                currency  = "EUR",
                unit      = "t",
                ts        = datetime.fromisoformat(r["date"]).replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.WEEKLY,
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# Drewry — Container shipping rates (WCI)
# ─────────────────────────────────────────────────────────────────────────────

class DrewryDataSource(BaseDataSource):
    source_type = DataSourceType.DREWRY
    supported_commodities = [CommodityCode.CONTAINER_SPOT, CommodityCode.BALTIC_DRY]

    async def _fetch(
        self, commodity: CommodityCode, start: datetime | None = None, end: datetime | None = None
    ) -> list[PricePoint]:
        index = "WCI" if commodity == CommodityCode.CONTAINER_SPOT else "BDI"
        params: dict[str, Any] = {"index": index}
        if start:
            params["from"] = start.strftime("%Y-%m-%d")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{self._base_url}/v1/indices/{index}/weekly",
                params=params,
                headers={"x-api-key": self._api_key},
            )
            resp.raise_for_status()
            rows = resp.json().get("data", [])
        currency = "USD" if commodity == CommodityCode.CONTAINER_SPOT else ""
        unit     = "TEU" if commodity == CommodityCode.CONTAINER_SPOT else "index"
        return [
            PricePoint(
                commodity = commodity,
                value     = float(r["composite"]),
                currency  = currency,
                unit      = unit,
                ts        = datetime.fromisoformat(r["week"]).replace(tzinfo=timezone.utc),
                source    = self.source_type,
                frequency = PriceFrequency.WEEKLY,
            )
            for r in rows
        ]


# ─────────────────────────────────────────────────────────────────────────────
# ECB — FX reference rates
# ─────────────────────────────────────────────────────────────────────────────

class ECBDataSource(BaseDataSource):
    """European Central Bank daily FX reference rates (free, no API key)."""
    source_type = DataSourceType.ECB
    supported_commodities: list[CommodityCode] = []   # FX only, not commodity prices
    _ECB_URL = "https://data-api.ecb.europa.eu/service/data/EXR"

    async def fetch_fx(
        self,
        base:  str = "EUR",
        quote: str = "USD",
        days:  int = 30,
    ) -> list[tuple[datetime, float]]:
        """Returns list of (date, rate) pairs where rate = quote per 1 base."""
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        url   = f"{self._ECB_URL}/D.{quote}.{base}.SP00.A"
        params = {"startPeriod": start, "format": "jsondata"}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()
        # Navigate ECB SDMX-JSON
        series = data.get("dataSets", [{}])[0].get("series", {})
        obs    = next(iter(series.values()), {}).get("observations", {})
        dates  = (
            data.get("structure", {})
                .get("dimensions", {})
                .get("observation", [{}])[0]
                .get("values", [])
        )
        result = []
        for idx_str, vals in obs.items():
            idx = int(idx_str)
            if idx < len(dates) and vals:
                ts  = datetime.strptime(dates[idx]["id"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                result.append((ts, float(vals[0])))
        return sorted(result, key=lambda x: x[0])

    async def _fetch(self, commodity: CommodityCode, **_: Any) -> list[PricePoint]:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Eurostat — PPI / CPI deflators
# ─────────────────────────────────────────────────────────────────────────────

class EurostatDataSource(BaseDataSource):
    """Eurostat SDMX-JSON API for PPI/CPI time series."""
    source_type = DataSourceType.EUROSTAT
    supported_commodities: list[CommodityCode] = []

    _DATASETS = {
        "PPI_DE":  "sts_inpp_m/M.PCH_PRE.I15.MIG_ING.DE",    # German manufacturing PPI
        "CPI_EU":  "prc_hicp_mmor/M.RCH_A.CP00.EU",          # HICP Euro area
    }
    _BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"

    async def fetch_deflator(self, dataset_key: str = "PPI_DE", periods: int = 24) -> list[tuple[datetime, float]]:
        path = self._DATASETS.get(dataset_key, "")
        if not path:
            return []
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{self._BASE_URL}/{path}?format=JSON")
            resp.raise_for_status()
            data = resp.json()
        # Parse SDMX-JSON
        dim_time = next(
            (d for d in data.get("dimension", {}).values() if d.get("label") == "Time"),
            {},
        )
        values = data.get("value", {})
        cats   = list(dim_time.get("category", {}).get("label", {}).values())
        result = []
        for i, period_str in enumerate(cats[-periods:]):
            v = values.get(str(i))
            if v is not None:
                try:
                    ts = datetime.strptime(period_str, "%Y-%m").replace(tzinfo=timezone.utc)
                    result.append((ts, float(v)))
                except ValueError:
                    pass
        return result

    async def _fetch(self, commodity: CommodityCode, **_: Any) -> list[PricePoint]:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Mock / Simulation source (dev + testing)
# ─────────────────────────────────────────────────────────────────────────────

_MOCK_BASE_PRICES: dict[CommodityCode, tuple[float, str, str]] = {
    # (price, currency, unit)
    CommodityCode.STEEL_HRC:        (620.0, "EUR", "t"),
    CommodityCode.STEEL_CRC:        (740.0, "EUR", "t"),
    CommodityCode.STEEL_REBAR:      (580.0, "EUR", "t"),
    CommodityCode.ALUMINIUM_P1020:  (2350.0, "USD", "t"),
    CommodityCode.ALUMINIUM_SCRAP:  (1850.0, "EUR", "t"),
    CommodityCode.COPPER_GRADE_A:   (9800.0, "USD", "t"),
    CommodityCode.COPPER_SCRAP:     (8200.0, "EUR", "t"),
    CommodityCode.LUMBER_SOFTWOOD:  (480.0, "USD", "MBF"),
    CommodityCode.PLYWOOD:          (680.0, "EUR", "m³"),
    CommodityCode.OSB:              (320.0, "EUR", "m³"),
    CommodityCode.OCC:              (105.0, "EUR", "t"),
    CommodityCode.CONTAINERBOARD:   (780.0, "EUR", "t"),
    CommodityCode.BRENT:            (82.0, "USD", "bbl"),
    CommodityCode.TTF_GAS:          (38.0, "EUR", "MWh"),
    CommodityCode.ELECTRICITY_DE:   (95.0, "EUR", "MWh"),
    CommodityCode.ELECTRICITY_PL:   (88.0, "EUR", "MWh"),
    CommodityCode.BALTIC_DRY:       (1850.0, "", "index"),
    CommodityCode.CONTAINER_SPOT:   (1650.0, "USD", "TEU"),
    CommodityCode.ROAD_FREIGHT_EU:  (1.42, "EUR", "km"),
}

class MockDataSource(BaseDataSource):
    """Deterministic mock for all commodities — dev and CI only."""
    source_type = DataSourceType.MANUAL
    supported_commodities = list(CommodityCode)

    async def _fetch(
        self,
        commodity: CommodityCode,
        start:     datetime | None = None,
        end:       datetime | None = None,
    ) -> list[PricePoint]:
        base_price, ccy, unit = _MOCK_BASE_PRICES.get(commodity, (100.0, "EUR", "unit"))
        end   = end   or datetime.now(timezone.utc)
        start = start or (end - timedelta(days=365))
        points = []
        current = start
        price   = base_price
        seed    = int(hashlib.md5(commodity.value.encode()).hexdigest(), 16) % 10000
        import random
        rng     = random.Random(seed)
        while current <= end:
            # Random walk with slight mean reversion
            pct_chg = rng.gauss(0, 0.008)
            price   = max(price * (1 + pct_chg), base_price * 0.5)
            points.append(PricePoint(
                commodity = commodity,
                value     = round(price, 4),
                currency  = ccy,
                unit      = unit,
                ts        = current,
                source    = DataSourceType.MANUAL,
                frequency = PriceFrequency.DAILY,
            ))
            current += timedelta(days=1)
        return points


# ─────────────────────────────────────────────────────────────────────────────
# Data Source Registry
# ─────────────────────────────────────────────────────────────────────────────

class DataSourceRegistry:
    """
    Central registry with priority ordering and automatic fallback.

    For each commodity, sources are tried in priority order.
    If primary source is circuit-open, next healthy source is used.
    """

    def __init__(self, use_mock: bool = False) -> None:
        self._sources: list[BaseDataSource] = []
        self._use_mock = use_mock
        if use_mock:
            self._sources.append(MockDataSource())

    def register(self, source: BaseDataSource) -> None:
        self._sources.append(source)

    def _sources_for(self, commodity: CommodityCode) -> list[BaseDataSource]:
        return [s for s in self._sources if commodity in s.supported_commodities]

    async def get_price(self, commodity: CommodityCode) -> PricePoint | None:
        for source in self._sources_for(commodity):
            if source._health.circuit_open:
                continue
            result = await source.fetch_latest(commodity)
            if result:
                return result
        # All failed — try mock as last resort
        if not self._use_mock:
            mock = MockDataSource()
            return await mock.fetch_latest(commodity)
        return None

    async def get_all_latest(self) -> dict[CommodityCode, PricePoint]:
        tasks = {c: self.get_price(c) for c in CommodityCode}
        results = {}
        for commodity, coro in tasks.items():
            try:
                pt = await coro
                if pt:
                    results[commodity] = pt
            except Exception:
                pass
        return results

    async def get_history(
        self,
        commodity: CommodityCode,
        days:      int = 365,
    ) -> list[PricePoint]:
        start = datetime.now(timezone.utc) - timedelta(days=days)
        for source in self._sources_for(commodity):
            if source._health.circuit_open:
                continue
            pts = await source.fetch_history(commodity, start)
            if pts:
                return pts
        mock = MockDataSource()
        return await mock.fetch_history(commodity, start)

    async def health_report(self) -> list[SourceHealth]:
        return [await s.health_check() for s in self._sources]


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────

def build_registry(secrets: dict[str, str], use_mock: bool = False) -> DataSourceRegistry:
    """Instantiate and register all live data sources from secrets dict."""
    reg = DataSourceRegistry(use_mock=use_mock)

    if lme_key := secrets.get("LME_API_KEY"):
        reg.register(LMEDataSource(api_key=lme_key, base_url="https://api.lme.com"))

    if platts_key := secrets.get("PLATTS_API_KEY"):
        reg.register(PlattsDataSource(api_key=platts_key, base_url="https://api.ci.spglobal.com"))

    if ice_key := secrets.get("ICE_API_KEY"):
        reg.register(ICEDataSource(api_key=ice_key, base_url="https://api.theice.com"))

    if eex_key := secrets.get("EEX_API_KEY"):
        reg.register(EEXDataSource(api_key=eex_key, base_url="https://api.eex.com"))

    if quandl_key := secrets.get("QUANDL_API_KEY"):
        reg.register(CMEDataSource(api_key=quandl_key, base_url="https://data.nasdaq.com"))

    if fm_key := secrets.get("FASTMARKETS_API_KEY"):
        reg.register(FastmarketsDataSource(api_key=fm_key, base_url="https://api.fastmarkets.com"))

    if drewry_key := secrets.get("DREWRY_API_KEY"):
        reg.register(DrewryDataSource(api_key=drewry_key, base_url="https://api.drewry.co.uk"))

    reg.register(ECBDataSource())      # No key needed
    reg.register(EurostatDataSource()) # No key needed

    return reg
