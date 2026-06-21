# Cost Risk Analysis Engine (CRAE)

System analizy ryzyka kosztowego dla platformy Industrial Cost Intelligence.
Identyfikuje, kwantyfikuje i monitoruje ryzyka w 4 domenach (Supplier, Market,
Production, Material), oblicza VaR portfelowy metodą Monte Carlo (10k symulacji)
i wysyła alerty wielokanałowe przez PagerDuty, Slack, Teams i Email.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-risk-model-scoring-supplier.md](./01-risk-model-scoring-supplier.md) | Risk Model (24 kategorie SR01–MT06, RiskFactor/RiskPortfolio, FMEA scoring 4 wagi), ImpactCalculator Monte Carlo VaR (10k sim Bernoulli × LogNormal → VaR95 + CVaR95), composite_portfolio_score z diversity discount, SupplierRiskAnalyzer (SR01–SR06: koncentracja, single-source, Altman Z, lead time drift, PPM, geopolityczne), GeopoliticalRiskService (12 krajów tier 1–5) |
| [02-market-production-sql.md](./02-market-production-sql.md) | MarketRiskAnalyzer (MR01–MR06: volatility σ30d, forecast upside, FX exposure, energy spike, inflation passthrough, commodity scarcity), ProductionRiskAnalyzer (PR01–PR06: OEE, bottleneck, tooling, scrap, labor, yield), MaterialRiskAnalyzer (MT01–MT06: supplier gaps, stock depletion, cert expiry, lead time), SQL Schema `crae` (6 ENUMów, 5 tabel, GENERATED level column, triggery publish_critical_risk + publish_risk_escalated, widoki v_risk_dashboard + v_top_risks + v_portfolio_trend) |
| [03-api-events-monitoring.md](./03-api-events-monitoring.md) | API OpenAPI 3.1 (5 ról RBAC, 20+ endpointów, /portfolio/heatmap, /analytics/var-report, /scenarios/stress-test), RiskAnalysisOrchestrator (asyncio.gather 4 analizatory → Monte Carlo → alert processing), 10 tematów Kafka (3 schematy Avro), CRAEOutboxPublisher, 28 metryk Prometheus, 7 dashboardów Grafana, SLI/SLO |
| [04-alerts-testing-risks-roadmap.md](./04-alerts-testing-risks-roadmap.md) | AlertEngine (10 reguł AR-001–AR-010, 4 kanały, cooldown, auto-escalation), Macierz 12 testów (unit RiskScorer/VaR/Supplier/Market/Production/Material/Alert, integration SQL+Outbox+Orchestrator, accuracy convergence, k6 load), 15 Ryzyk (R01–R15), Roadmap 32 sprinty 4 fazy |

## Architektura

```
Zewnętrzne dane wejściowe
CBE / PFE / MES / ERP / SOP / Eurostat / ECB / Credit API
                │
                ▼
┌──────────────────────────────────────────────────┐
│         RiskAnalysisOrchestrator                 │
│  asyncio.gather([                                │
│    SupplierRiskAnalyzer,   ← SR01–SR06           │
│    MarketRiskAnalyzer,     ← MR01–MR06           │
│    ProductionRiskAnalyzer, ← PR01–PR06           │
│    MaterialRiskAnalyzer,   ← MT01–MT06           │
│  ])                                              │
└──────────────────┬───────────────────────────────┘
                   │ 24 RiskFactor objects
                   │
       ┌───────────┼───────────────┐
       │           │               │
       ▼           ▼               ▼
  RiskScorer   ImpactCalculator  AlertEngine
  FMEA-based   Monte Carlo VaR   AR-001–AR-010
  4 weights    10k Bernoulli×    PD+Slack+Email
  [0–100]      LogNormal sim     +Teams dispatch
       │           │               │
       └───────────┼───────────────┘
                   │
                   ▼
        ┌─────────────────────┐
        │  crae.risk_factors  │
        │  GENERATED level    │
        │  crae.portfolio_    │
        │  snapshots          │
        └──────────┬──────────┘
                   │
         ┌─────────┼──────────┐
         │         │          │
         ▼         ▼          ▼
   DB Trigger   Outbox    v_risk_dashboard
   CRITICAL →  Publisher  v_top_risks
   outbox      Kafka      v_portfolio_trend
               10 topics
```

## 24 Kategorie ryzyka

| Domena | Kategoria | Opis |
|--------|-----------|------|
| **SUPPLIER** | SR01 | Koncentracja dostawcy (spend share) |
| | SR02 | Single-source parts (brak alternatywy) |
| | SR03 | Stabilność finansowa (Altman Z-Score) |
| | SR04 | Lead time drift (opóźnienia dostaw) |
| | SR05 | Jakość dostawcy (PPM defects) |
| | SR06 | Ryzyko geopolityczne (country tier) |
| **MARKET** | MR01 | Zmienność cen commodity (σ 30d) |
| | MR02 | Ryzyko prognozy cen (PFE forecast upside) |
| | MR03 | Ekspozycja walutowa (FX foreign share) |
| | MR04 | Spike cen energii (> 120 EUR/MWh) |
| | MR05 | Presja inflacyjna (PPI-HICP delta) |
| | MR06 | Scarcity commodity (supply disruption) |
| **PRODUCTION** | PR01 | OEE maszyny (< 75% avg 3d) |
| | PR02 | Wąskie gardło procesu (throughput > 35%) |
| | PR03 | Zużycie oprzyrządowania (tooling life < 20%) |
| | PR04 | Wskaźnik braków (scrap > 2× baseline) |
| | PR05 | Dostępność pracowników (< 85%) |
| | PR06 | Wydajność procesu (yield drop > 5pp) |
| **MATERIAL** | MT01 | Luka pokrycia BOM (zero active suppliers) |
| | MT02 | Wyczerpanie zapasów (stock < reorder) |
| | MT03 | Substytucja materiałowa (brak zamiennika) |
| | MT04 | Wygaśnięcie certyfikatu (< 60 dni) |
| | MT05 | Długi lead time materiału (> 90 dni) |
| | MT06 | Zmiana specyfikacji (design freeze risk) |

## FMEA Scoring

```
score = (prob × 0.35 + impact_norm × 0.35 + velocity_norm × 0.15 + detect_score × 0.15) × 100

impact_norm    = min(1.0, log10(max(1, impact_eur)) / 6)   # log10(10M EUR) = 1.0
velocity_norm  = max(0.0, 1.0 - velocity_days / 180)
detect_score   = 1.0 - detectability
```

| Zakres | Poziom | Kolor |
|--------|--------|-------|
| 0–25 | LOW | Zielony |
| 26–50 | MEDIUM | Żółty |
| 51–75 | HIGH | Pomarańczowy |
| 76–100 | CRITICAL | Czerwony |

## Monte Carlo VaR

```
Dla każdej symulacji i = 1..10_000:
  loss_i = Σ [Bernoulli(p_j) × LogNormal(μ=log(impact_j), σ=0.30)]

VaR_95  = percentile(losses, 95)
CVaR_95 = mean(losses[losses ≥ VaR_95])
```

## Altman Z-Score — strefy finansowe

| Strefa | Z-Score | Prawdopodobieństwo SR03 |
|--------|---------|------------------------|
| DISTRESS | < 1.23 | 0.85 |
| GREY ZONE | 1.23–2.90 | 0.45 |
| SAFE | > 2.90 | 0.10 |

## Geopolitical Risk Tiers

| Tier | Kraje | prob SR06 |
|------|-------|-----------|
| 1 — Minimal | DE, PL | 0.02 |
| 2 — Low | IN, MX | 0.15 |
| 3 — Medium | CN, TR | 0.40 |
| 4 — High | TW, UA | 0.70 |
| 5 — Critical | RU, BY | 0.90 |

## SQL Schema (schemat `crae`)

| Tabela | Opis |
|--------|------|
| `analysis_runs` | Przebiegi analizy (status, scope, czas) |
| `risk_factors` | Czynniki ryzyka z GENERATED level (LOW/MEDIUM/HIGH/CRITICAL) |
| `portfolio_snapshots` | Snapshoty VaR95, CVaR95, expected_loss, composite_score |
| `risk_mitigations` | Działania mitygacyjne (owner, due_date, status) |
| `outbox_events` | Transactional Outbox dla Kafka |

## Alert Rules (10 reguł)

| Reguła | Trigger | Severity | Kanały | Cooldown |
|--------|---------|----------|--------|---------|
| AR-001 | score ≥ 75 | CRITICAL | PD + Slack | 30 min |
| AR-002 | VaR-95 > 5M EUR | CRITICAL | Email + Teams | 120 min |
| AR-003 | Spend share > 40% | WARNING | Email + Slack | 24 h |
| AR-004 | Altman Z < 1.23 | CRITICAL | PD + Email | 240 min |
| AR-005 | Vol > 10% + forecast +10% | WARNING | Slack + Teams | 180 min |
| AR-006 | OEE avg 3d < 65% | CRITICAL | PD + Slack | 30 min |
| AR-007 | Stock < reorder | CRITICAL | Email + Teams + Slack | 60 min |
| AR-008 | Cert expiry < 30d | WARNING | Email | 24 h |
| AR-009 | Portfolio score +15pts/24h | WARNING | Slack + Teams | 360 min |
| AR-010 | Geo tier-5 supplier | DISASTER | PD + Email + Teams | 48 h |

## Event System (10 tematów Kafka)

| Temat | Trigger | Konsumenci |
|-------|---------|------------|
| `crae.analysis.requested` | POST /analyses | Risk Worker |
| `crae.analysis.completed` | Orchestrator done | CBE, SOP, UI |
| `crae.risk.critical` | DB Trigger (CRITICAL) | Alert, PagerDuty |
| `crae.risk.escalated` | status=ESCALATED | Alert, Management |
| `crae.risk.resolved` | status=RESOLVED | Audit |
| `crae.portfolio.updated` | Snapshot upserted | UI, Reporting |
| `crae.alert.fired` | AlertEngine dispatch | Audit, DLQ |
| `crae.mitigation.created` | POST /mitigations | Workflow |
| `crae.mitigation.overdue` | APScheduler daily | Alert |
| `crae.scenario.computed` | POST /scenarios/stress-test | CBE, UI |

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| `crae_analysis_duration_seconds` p95 | ≤ 5s |
| `crae_api_duration_seconds` p95 GET | ≤ 400ms |
| `crae_risk_score` (CRITICAL) | Alert gdy score ≥ 75 |
| `crae_portfolio_var_eur` | Alert gdy > 5 000 000 EUR |
| `crae_alert_dispatch_failures_total` | < 1% |
| `crae_outbox_lag_seconds` | ≤ 60s |
| Availability API | ≥ 99.5% |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CRAE_VIEWER` | GET risks, portfolio, dashboard |
| `CRAE_ANALYST` | VIEWER + analytics, VAR report, stress-test, export |
| `CRAE_RISK_MANAGER` | ANALYST + POST analyses, mitigations, escalate |
| `CRAE_APPROVER` | RISK_MANAGER + approve/reject mitigations |
| `CRAE_ADMIN` | Wszystko + konfiguracja reguł alertów, thresholds, DELETE |

## Skalowalność

| Poziom | Wolumen | Infrastruktura |
|--------|---------|----------------|
| L1 | ≤ 5 analiz/h | 1 API pod, 1 worker (CPU) |
| L2 | ≤ 20 analiz/h | 2–3 API pods, 2 workers |
| L3 | ≤ 100 analiz/h | HPA 3–8 API + 2–4 workers, Redis cache |
| L4 | > 100 analiz/h | Multi-region, Kafka Streams real-time, TimescaleDB |

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Risk Scoring:** NumPy, math (log10 normalization), FMEA weights
- **Monte Carlo:** NumPy vectorized (Bernoulli × LogNormal, 10k simulations)
- **Alerting:** AsyncIO dispatch, PagerDuty REST, Slack Bolt, Teams Webhook
- **Database:** PostgreSQL 16 (schemat `crae`, GENERATED columns, triggers)
- **Cache:** Redis 7+ (portfolio snapshot TTL=900s, risk list TTL=300s)
- **Messaging:** Apache Kafka 3+ (10 tematów, Avro + Schema Registry, Outbox)
- **Monitoring:** Prometheus (28 metryk) + Grafana (7 dashboardów) + Alertmanager (10 reguł)
- **Security:** JWT RS256, RBAC 5 ról
- **Kubernetes:** HPA crae-api 2–8 pods, crae-worker 1–4 pods

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| CBE | ← | Kafka / DB | `breakdown_id`, unit_cost_eur, material costs |
| PFE | ← | REST / Kafka | `pfe.forecast.ready` → price forecasts MR01–MR02 |
| SOP | ← | Kafka | Supplier offer prices → SR01, MT01 |
| MES | ← | REST API | OEE, scrap rate, throughput → PR01–PR06 |
| ERP | ← | DB / REST | Stock levels, BOM, lead times → MT01–MT06 |
| ECB | ← | REST/XML | EUR/USD/CNY/PLN FX → MR03 |
| Eurostat | ← | SDMX-JSON | PPI metals, HICP → MR05 |
| Dun & Bradstreet | ← | REST API | Altman Z-Score → SR03 |
| PagerDuty | → | REST Events v2 | CRITICAL/DISASTER alerts |
| Slack | → | Bolt API | WARNING/CRITICAL notifications |
| Microsoft Teams | → | Incoming Webhook | All severity notifications |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | Taxonomy, SQL, RiskScorer, SupplierRisk, podstawowe API, Kafka, monitoring |
| Market + Production | S9–S18 | MarketRisk + PFE, ProductionRisk + MES, MaterialRisk, Monte Carlo VaR, AlertEngine, stress-test |
| Intelligence + Scale | S19–S28 | PFE drift integration, Supplier financial API, domain RBAC, trend analytics, TimescaleDB, HPA |
| Advanced Analytics | S29–S32 | Copula model, ML risk prediction (XGBoost), real-time Kafka Streams, threat intelligence |
