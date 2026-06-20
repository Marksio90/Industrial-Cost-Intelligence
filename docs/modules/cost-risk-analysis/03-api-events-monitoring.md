# Cost Risk Analysis Engine — Sections 7–9

## 7. API

### 7.1 Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CRAE_VIEWER` | GET risks, portfolio, dashboard, alerts (read-only) |
| `CRAE_ANALYST` | CRAE_VIEWER + GET analytics, export, VaR report, scenario |
| `CRAE_RISK_MANAGER` | CRAE_ANALYST + PATCH risk (assign/status/mitigation), POST acknowledge alert |
| `CRAE_APPROVER` | CRAE_RISK_MANAGER + POST accept/escalate, approve mitigation budget |
| `CRAE_ADMIN` | Wszystko + DELETE, POST trigger-analysis, manage thresholds |

### 7.2 Endpointy

```
── Risk Factors ─────────────────────────────────────────────────────
GET  /api/v1/crae/risks                      Lista ryzyk (filter: domain/level/status/entity)
GET  /api/v1/crae/risks/{risk_id}            Szczegóły ryzyka z historią mitygacji
PATCH /api/v1/crae/risks/{risk_id}           Aktualizacja (status, assignee, mitigation_plan)
POST /api/v1/crae/risks/{risk_id}/accept     Zaakceptuj ryzyko (ACCEPTED)
POST /api/v1/crae/risks/{risk_id}/escalate   Eskaluj (ESCALATED)
POST /api/v1/crae/risks/{risk_id}/close      Zamknij (CLOSED)

── Mitigation ────────────────────────────────────────────────────────
POST /api/v1/crae/risks/{risk_id}/mitigations     Dodaj plan mitygacji
GET  /api/v1/crae/risks/{risk_id}/mitigations     Lista akcji mitygacyjnych
PATCH /api/v1/crae/mitigations/{action_id}        Aktualizuj akcję
POST /api/v1/crae/mitigations/{action_id}/complete  Oznacz jako zrealizowaną

── Portfolio & Dashboard ─────────────────────────────────────────────
GET  /api/v1/crae/portfolio                  Bieżący portfolio z VaR i domenami
GET  /api/v1/crae/portfolio/trend            Historia portfolio (30/90/365 dni)
GET  /api/v1/crae/portfolio/heatmap          Macierz P×I per domena/kategoria
GET  /api/v1/crae/dashboard/top-risks        Top-N ryzyk wg score × impact
GET  /api/v1/crae/dashboard/summary          KPIs: critical_count, VaR, composite_score

── Analytics ─────────────────────────────────────────────────────────
GET  /api/v1/crae/analytics/supplier-heat-map     Risk map dostawców
GET  /api/v1/crae/analytics/material-risk         Ryzyko per material/BOM line
GET  /api/v1/crae/analytics/commodity-exposure    Ekspozycja na surowce (spend × vol)
GET  /api/v1/crae/analytics/var-report            VaR95 + CVaR95 + Expected Loss (Monte Carlo)
GET  /api/v1/crae/analytics/concentration         Koncentracja spend / single-source map

── Scenarios ─────────────────────────────────────────────────────────
POST /api/v1/crae/scenarios/stress-test     Stress test: zmiana cen/kursów/OEE
GET  /api/v1/crae/scenarios/{scenario_id}   Wyniki stress testu

── Alerts ────────────────────────────────────────────────────────────
GET  /api/v1/crae/alerts                    Lista alertów (filter: sent/acknowledged)
POST /api/v1/crae/alerts/{alert_id}/acknowledge  Potwierdź alert
GET  /api/v1/crae/alerts/unacknowledged     Niepotwierdzone alerty CRITICAL

── Analysis Runs ─────────────────────────────────────────────────────
GET  /api/v1/crae/runs                      Historia przebiegów analizy
POST /api/v1/crae/runs/trigger              Wyzwól analizę manualnie
GET  /api/v1/crae/runs/{run_id}             Szczegóły przebiegu
```

### 7.3 Przykładowe żądania/odpowiedzi

```http
GET /api/v1/crae/portfolio
Authorization: Bearer <JWT>
```

```json
{
  "portfolio_date": "2025-06-20",
  "composite_score": 58.4,
  "total_risks": 34,
  "critical_count": 3,
  "high_count": 12,
  "medium_count": 15,
  "low_count": 4,
  "total_impact_eur": 4850000,
  "var_95_eur":       2140000,
  "cvar_95_eur":      3220000,
  "expected_loss_eur": 890000,
  "domain_scores": {
    "SUPPLIER":   71.2,
    "MARKET":     64.8,
    "PRODUCTION": 52.1,
    "MATERIAL":   45.5
  },
  "top_risks": [
    {
      "risk_id":     "a1b2c3d4-...",
      "category":    "SR02",
      "entity_name": "Metalworks GmbH",
      "score":       82.0,
      "level":       "CRITICAL",
      "impact_eur":  420000,
      "status":      "OPEN",
      "due_date":    null
    }
  ]
}
```

```http
POST /api/v1/crae/scenarios/stress-test
Content-Type: application/json

{
  "name": "steel_price_shock",
  "description": "Cena stali +25% przez 6 miesięcy",
  "shocks": [
    {"type": "COMMODITY_PRICE", "commodity": "STEEL_HRC", "change_pct": 25},
    {"type": "COMMODITY_PRICE", "commodity": "STEEL_CRC", "change_pct": 22},
    {"type": "FX", "pair": "EURUSD", "change_pct": -8},
    {"type": "OEE", "location": "DE", "change_pp": -5}
  ],
  "horizon_months": 6
}
```

```json
{
  "scenario_id": "sc-001",
  "name": "steel_price_shock",
  "base_composite_score": 58.4,
  "stressed_composite_score": 79.1,
  "base_expected_loss_eur":   890000,
  "stressed_expected_loss_eur": 2840000,
  "base_var_95_eur":    2140000,
  "stressed_var_95_eur": 5120000,
  "delta_impact_eur": 1950000,
  "new_critical_risks": [
    {"category": "MR01", "entity": "STEEL_HRC", "stressed_score": 88.2},
    {"category": "MR02", "entity": "STEEL_CRC", "stressed_score": 84.5},
    {"category": "MR03", "entity": "FX_EXPOSURE", "stressed_score": 77.0}
  ]
}
```

### 7.4 PATCH risk — mitigation workflow

```http
PATCH /api/v1/crae/risks/a1b2c3d4-.../
Content-Type: application/json

{
  "status": "MITIGATED",
  "assigned_to": "supply.manager@company.com",
  "mitigation_plan": "Dual sourcing: negocjacje z Fasteners Ltd. jako backup. Q3 2025.",
  "due_date": "2025-09-30",
  "residual_score": 42.0
}
```

### 7.5 FastAPI implementation sketch

```python
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from uuid import UUID

router = APIRouter(prefix="/api/v1/crae", tags=["cost-risk-analysis"])

@router.get("/portfolio", response_model=PortfolioResponse)
async def get_portfolio(
    db:   AsyncpgPool = Depends(get_db),
    user: TokenPayload = Depends(require_role("CRAE_VIEWER")),
) -> PortfolioResponse:
    portfolio = await db.get_latest_portfolio()
    if portfolio is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No portfolio available yet")
    return PortfolioResponse.from_db(portfolio)

@router.get("/risks", response_model=RiskListResponse)
async def list_risks(
    domain:   str | None = None,
    level:    str | None = None,
    status_:  str | None = None,
    entity_type: str | None = None,
    limit:    int = 50,
    offset:   int = 0,
    db:       AsyncpgPool = Depends(get_db),
    user:     TokenPayload = Depends(require_role("CRAE_VIEWER")),
) -> RiskListResponse:
    risks = await db.list_risks(
        domain=domain, level=level, status=status_,
        entity_type=entity_type, limit=limit, offset=offset,
    )
    return RiskListResponse(risks=risks, total=len(risks))

@router.patch("/risks/{risk_id}", response_model=RiskResponse)
async def update_risk(
    risk_id: UUID,
    body:    RiskUpdateRequest,
    db:      AsyncpgPool = Depends(get_db),
    user:    TokenPayload = Depends(require_role("CRAE_RISK_MANAGER")),
) -> RiskResponse:
    rf = await db.get_risk(risk_id)
    if rf is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Risk not found")
    updated = await db.update_risk(risk_id, body, updated_by=user.sub)
    return RiskResponse.from_db(updated)

@router.post("/runs/trigger", status_code=status.HTTP_202_ACCEPTED,
             response_model=AnalysisRunResponse)
async def trigger_analysis(
    body:   TriggerAnalysisRequest,
    tasks:  BackgroundTasks,
    engine: "RiskAnalysisOrchestrator" = Depends(get_orchestrator),
    db:     AsyncpgPool = Depends(get_db),
    user:   TokenPayload = Depends(require_role("CRAE_ADMIN")),
) -> AnalysisRunResponse:
    run_id = await db.create_analysis_run(triggered_by=user.sub)
    tasks.add_task(engine.run_analysis, run_id, body.domains)
    return AnalysisRunResponse(run_id=run_id, status="RUNNING")

@router.post("/scenarios/stress-test", response_model=StressTestResponse)
async def stress_test(
    body:   StressTestRequest,
    engine: "StressTestEngine" = Depends(get_stress_engine),
    user:   TokenPayload = Depends(require_role("CRAE_ANALYST")),
) -> StressTestResponse:
    return await engine.run(body)
```

---

## 8. Event System

### 8.1 Topologie Kafka (10 tematów)

| Temat | Trigger | Producent | Konsumenci |
|-------|---------|-----------|------------|
| `crae.risk.detected` | Nowy RiskFactor (any level) | Analysis Worker | Audit, Dashboard |
| `crae.risk.critical` | DB Trigger (level=CRITICAL) | DB Trigger | Alert, CBE, UI |
| `crae.risk.escalated` | DB Trigger (status=ESCALATED) | DB Trigger | Alert, Management |
| `crae.risk.mitigated` | Status → MITIGATED | API | Audit, Dashboard |
| `crae.risk.closed` | Status → CLOSED | API | Audit |
| `crae.portfolio.updated` | Nowy portfolio snapshot | Analysis Worker | CBE, PFE, UI |
| `crae.alert.sent` | AlertEngine wyśle alert | Alert Worker | Audit |
| `crae.analysis.completed` | Run zakończony | Analysis Worker | UI, Scheduler |
| `crae.stress_test.ready` | Stress test zakończony | Stress Engine | UI |
| `crae.mitigation.overdue` | Przeterminowana akcja | Scheduler | Alert |

### 8.2 Avro schemas

```json
// crae.risk.critical — v1
{
  "namespace": "com.industrial_cost_intelligence.crae",
  "type": "record",
  "name": "RiskCritical",
  "fields": [
    {"name": "risk_id",     "type": "string"},
    {"name": "domain",      "type": {"type": "enum", "name": "RiskDomain",
                                     "symbols": ["SUPPLIER","MARKET","PRODUCTION","MATERIAL"]}},
    {"name": "category",    "type": "string"},
    {"name": "entity_name", "type": "string"},
    {"name": "score",       "type": "double"},
    {"name": "probability", "type": "double"},
    {"name": "impact_eur",  "type": "double"},
    {"name": "detected_at", "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

```json
// crae.portfolio.updated — v1
{
  "namespace": "com.industrial_cost_intelligence.crae",
  "type": "record",
  "name": "PortfolioUpdated",
  "fields": [
    {"name": "portfolio_id",      "type": "string"},
    {"name": "portfolio_date",    "type": "string"},
    {"name": "composite_score",   "type": "double"},
    {"name": "critical_count",    "type": "int"},
    {"name": "total_impact_eur",  "type": "double"},
    {"name": "var_95_eur",        "type": "double"},
    {"name": "expected_loss_eur", "type": "double"},
    {"name": "supplier_score",    "type": ["null","double"], "default": null},
    {"name": "market_score",      "type": ["null","double"], "default": null},
    {"name": "production_score",  "type": ["null","double"], "default": null},
    {"name": "material_score",    "type": ["null","double"], "default": null}
  ]
}
```

```json
// crae.mitigation.overdue — v1
{
  "namespace": "com.industrial_cost_intelligence.crae",
  "type": "record",
  "name": "MitigationOverdue",
  "fields": [
    {"name": "action_id",    "type": "string"},
    {"name": "risk_id",      "type": "string"},
    {"name": "entity_name",  "type": "string"},
    {"name": "due_date",     "type": "string"},
    {"name": "days_overdue", "type": "int"},
    {"name": "assigned_to",  "type": "string"},
    {"name": "risk_score",   "type": "double"},
    {"name": "risk_level",   "type": "string"}
  ]
}
```

### 8.3 RiskAnalysisOrchestrator

```python
import asyncio
import time
from uuid import UUID
from datetime import date

class RiskAnalysisOrchestrator:
    """
    Główny worker: uruchamia wszystkie analyzery, zapisuje wyniki,
    oblicza portfolio, generuje alerty.
    """

    def __init__(
        self,
        supplier_analyzer:   "SupplierRiskAnalyzer",
        market_analyzer:     "MarketRiskAnalyzer",
        production_analyzer: "ProductionRiskAnalyzer",
        material_analyzer:   "MaterialRiskAnalyzer",
        impact_calc:         "ImpactCalculator",
        alert_engine:        "AlertEngine",
        db:                  "AsyncpgPool",
    ):
        self._analyzers = {
            "SUPPLIER":   supplier_analyzer,
            "MARKET":     market_analyzer,
            "PRODUCTION": production_analyzer,
            "MATERIAL":   material_analyzer,
        }
        self._impact = impact_calc
        self._alerts = alert_engine
        self._db     = db

    async def run_analysis(
        self,
        run_id:  UUID,
        domains: list[str] | None = None,
    ) -> None:
        t0 = time.monotonic()
        domains = domains or list(self._analyzers.keys())

        try:
            await self._db.update_run(run_id, status="RUNNING")

            # Równoległe uruchomienie analyzerów
            domain_results = await asyncio.gather(
                *[self._analyzers[d].analyze_all()
                  for d in domains if d in self._analyzers],
                return_exceptions=True,
            )

            all_risks: list[RiskFactor] = []
            for result in domain_results:
                if isinstance(result, list):
                    all_risks.extend(result)

            # Zapis ryzyk do DB (upsert by entity+category)
            new_count, closed_count = await self._db.upsert_risks(all_risks, run_id)

            # Monte Carlo VaR
            var_data = await self._impact.monte_carlo_var(all_risks)

            # Portfolio snapshot
            portfolio = await self._build_portfolio(run_id, all_risks, var_data)
            await self._db.save_portfolio(portfolio)

            # Alerty
            escalated = await self._alerts.process_new_risks(all_risks)

            duration = time.monotonic() - t0
            await self._db.update_run(run_id, status="DONE",
                                      risk_count_new=new_count,
                                      risk_count_closed=closed_count,
                                      risk_count_escalated=escalated,
                                      portfolio_score=portfolio.composite_score,
                                      var_95_eur=portfolio.var_95_eur,
                                      duration_s=duration)

            # Publish portfolio event
            await self._db.insert_outbox_event(
                topic="crae.analysis.completed",
                key=str(run_id),
                payload={
                    "run_id":         str(run_id),
                    "composite_score": portfolio.composite_score,
                    "critical_count": portfolio.critical_count,
                    "duration_s":     round(duration, 2),
                },
            )

        except Exception as exc:
            await self._db.update_run(run_id, status="FAILED",
                                      error_message=str(exc))
            raise

    async def _build_portfolio(
        self,
        run_id: UUID,
        risks:  list[RiskFactor],
        var_data: dict,
    ) -> "RiskPortfolio":
        from uuid import uuid4

        domain_scores = {}
        for domain in RiskDomain:
            domain_risks = [r for r in risks if r.domain == domain]
            if domain_risks:
                scorer = RiskScorer()
                domain_scores[domain] = scorer.composite_portfolio_score(domain_risks)

        composite = RiskScorer().composite_portfolio_score(risks)

        return RiskPortfolio(
            portfolio_id=uuid4(),
            run_id=run_id,
            calculated_at=date.today(),
            total_risks=len(risks),
            critical_count=sum(1 for r in risks if r.level == RiskLevel.CRITICAL),
            high_count=sum(1 for r in risks if r.level == RiskLevel.HIGH),
            medium_count=sum(1 for r in risks if r.level == RiskLevel.MEDIUM),
            low_count=sum(1 for r in risks if r.level == RiskLevel.LOW),
            total_impact_eur=sum(r.impact_eur for r in risks),
            composite_score=composite,
            domain_scores=domain_scores,
            top_risks=sorted(risks, key=lambda r: r.score, reverse=True)[:10],
            var_95_eur=var_data["var_95_eur"],
            expected_loss_eur=var_data["expected_loss_eur"],
        )
```

### 8.4 CRAEOutboxPublisher

```python
class CRAEOutboxPublisher:
    POLL_INTERVAL_S = 0.5
    BATCH_SIZE      = 50

    def __init__(self, db: "AsyncpgPool", kafka: "AIOKafkaProducer",
                 registry: "ConfluentSchemaRegistry"):
        self._db = db; self._kafka = kafka; self._registry = registry

    async def run(self) -> None:
        while True:
            n = await self._publish_batch()
            if n < self.BATCH_SIZE:
                await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _publish_batch(self) -> int:
        async with self._db.transaction() as conn:
            rows = await conn.fetch("""
                SELECT event_id, topic, key, payload
                  FROM crae.outbox_events
                 WHERE published = FALSE
                 ORDER BY created_at LIMIT $1 FOR UPDATE SKIP LOCKED
            """, self.BATCH_SIZE)
            for row in rows:
                schema  = await self._registry.get_schema(row["topic"])
                encoded = schema.encode(row["payload"])
                await self._kafka.send(row["topic"],
                                       key=row["key"].encode(), value=encoded)
                await conn.execute(
                    "UPDATE crae.outbox_events SET published=TRUE WHERE event_id=$1",
                    row["event_id"])
            return len(rows)
```

---

## 9. Monitoring

### 9.1 Metryki Prometheus (28 metryk)

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# ─── Analiza ryzyka ────────────────────────────────────────────

crae_analysis_run_total = Counter(
    "crae_analysis_run_total",
    "Liczba przebiegów analizy",
    ["status"]   # DONE / FAILED
)

crae_analysis_duration_seconds = Histogram(
    "crae_analysis_duration_seconds",
    "Czas pełnej analizy ryzyka",
    buckets=[10, 30, 60, 120, 300, 600]
)

crae_risks_detected_total = Counter(
    "crae_risks_detected_total",
    "Nowo wykryte ryzyka",
    ["domain", "category", "level"]
)

crae_risks_closed_total = Counter(
    "crae_risks_closed_total",
    "Zamknięte ryzyka",
    ["domain", "level"]
)

# ─── Stan portfolio ────────────────────────────────────────────

crae_portfolio_composite_score = Gauge(
    "crae_portfolio_composite_score",
    "Composite risk score portfela (0–100)"
)

crae_portfolio_critical_count = Gauge(
    "crae_portfolio_critical_count",
    "Liczba ryzyk CRITICAL w portfelu"
)

crae_portfolio_high_count = Gauge(
    "crae_portfolio_high_count",
    "Liczba ryzyk HIGH w portfelu"
)

crae_portfolio_total_impact_eur = Gauge(
    "crae_portfolio_total_impact_eur",
    "Całkowity szacowany wpływ finansowy EUR"
)

crae_portfolio_var_95_eur = Gauge(
    "crae_portfolio_var_95_eur",
    "Value-at-Risk 95% (Monte Carlo) EUR"
)

crae_portfolio_expected_loss_eur = Gauge(
    "crae_portfolio_expected_loss_eur",
    "Expected Loss EUR"
)

crae_domain_score = Gauge(
    "crae_domain_score",
    "Risk score per domena (0–100)",
    ["domain"]
)

# ─── Ryzyka per kategoria ─────────────────────────────────────

crae_risk_score_current = Gauge(
    "crae_risk_score_current",
    "Bieżący score dla konkretnego ryzyka",
    ["category", "entity_name"]
)

crae_open_risks_by_level = Gauge(
    "crae_open_risks_by_level",
    "Otwarte ryzyka wg poziomu",
    ["domain", "level"]
)

crae_mitigation_coverage_pct = Gauge(
    "crae_mitigation_coverage_pct",
    "% ryzyk HIGH+ z planem mitygacji",
    ["domain"]
)

crae_mitigation_overdue_count = Gauge(
    "crae_mitigation_overdue_count",
    "Przeterminowane akcje mitygacyjne"
)

# ─── Dostawcy ────────────────────────────────────────────────

crae_supplier_risk_score = Gauge(
    "crae_supplier_risk_score",
    "Score ryzyka dostawcy",
    ["supplier_name"]
)

crae_single_source_count = Gauge(
    "crae_single_source_count",
    "Liczba części z jednym dostawcą"
)

crae_geopolitical_risk_count = Gauge(
    "crae_geopolitical_risk_count",
    "Dostawcy w kraju risk_tier ≥ 3",
    ["risk_tier"]
)

# ─── Rynek ───────────────────────────────────────────────────

crae_commodity_vol_30d = Gauge(
    "crae_commodity_vol_30d",
    "Zmienność 30-dniowa surowca (annualized)",
    ["commodity"]
)

crae_fx_exposure_eur = Gauge(
    "crae_fx_exposure_eur",
    "Ekspozycja walutowa (non-EUR spend EUR)",
    ["currency"]
)

crae_energy_price_eur_mwh = Gauge(
    "crae_energy_price_eur_mwh",
    "Bieżąca cena energii DE EUR/MWh"
)

# ─── Produkcja ───────────────────────────────────────────────

crae_oee_min_active = Gauge(
    "crae_oee_min_active",
    "Minimalne OEE z aktywnych maszyn"
)

crae_machines_below_oee_target = Gauge(
    "crae_machines_below_oee_target",
    "Maszyny z OEE < 75%"
)

# ─── API ─────────────────────────────────────────────────────

crae_api_request_total = Counter(
    "crae_api_request_total",
    "Żądania HTTP do CRAE API",
    ["method", "endpoint", "status_code"]
)

crae_api_duration_seconds = Histogram(
    "crae_api_duration_seconds",
    "Latencja CRAE API",
    ["endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5]
)

# ─── Outbox ───────────────────────────────────────────────────

crae_outbox_lag_seconds = Gauge(
    "crae_outbox_lag_seconds",
    "Opóźnienie publikacji eventów Outbox"
)

crae_outbox_published_total = Counter(
    "crae_outbox_published_total",
    "Opublikowane eventy",
    ["topic"]
)
```

### 9.2 Dashboardy Grafana (7 dashboardów)

| Dashboard | Panele kluczowe |
|-----------|----------------|
| **Risk Overview** | Composite score gauge, critical/high/medium counts, domain scores radar, VaR trend |
| **Risk Heat Map** | 2D macierz Probability × Impact per domena, score heatmap per kategoria |
| **Supplier Risk** | Top 10 dostawców wg score, single-source count, geopolitical map, financial health |
| **Market Risk** | Commodity volatility trend, FX exposure breakdown, price forecast deviation, energy spike alert |
| **Production Risk** | OEE heatmap (machine × week), scrap rate trend, tooling shortage countdown, labor availability |
| **Material Risk** | Stock depletion radar, BOM coverage gaps, cert expiry calendar, lead time histogram |
| **Mitigation Tracker** | Open vs. mitigated vs. accepted, overdue actions, residual score trend, mitigation cost vs. impact |

### 9.3 SLI / SLO

| SLI | SLO |
|-----|-----|
| Pełna analiza (wszystkie domeny) P95 | ≤ 5 min |
| API P95 GET /portfolio | ≤ 500ms |
| API P95 POST /scenarios/stress-test | ≤ 10s |
| Alert latency (critical risk → notification) | ≤ 2 min |
| Analysis freshness (od ostatniego przebiegu) | ≤ 25h |
| Outbox lag | < 30s (p99) |
| Availability | ≥ 99.5% |
| Monte Carlo VaR computation (10k sim) | ≤ 30s |
