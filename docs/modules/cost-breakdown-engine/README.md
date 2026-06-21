# Cost Breakdown Engine (CBE)

System rozbijania kosztów wytworzenia na składniki dla platformy Industrial Cost
Intelligence. Kalkuluje i zarządza strukturą kosztów (materiał, robocizna, maszyny,
energia, narzędzia, overhead + marża) na podstawie danych z BOM Engine, Drawing
Analysis Engine i Supplier Offer Parser.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-cost-structure-breakdown-allocation.md](./01-cost-structure-breakdown-allocation.md) | Cost Structure Model (22 CostComponentType, 6 kategorii, CostBreakdownResult), Breakdown Algorithm (CostBreakdownEngine orchestrator, 5 sub-kalkulatorów: material/labor/machine/energy/tooling, overhead & marża, ważona confidence), Allocation Rules (LocationRates 5 lokalizacji, OverheadProfile 6 trybów, scrap per proces, quantity-break table 9 progów) |
| [02-sql-schema-api.md](./02-sql-schema-api.md) | SQL Schema PostgreSQL 16 (schemat `cbe`, 5 ENUMów, 8 tabel, generated columns, triggery, widoki v_breakdown_summary + v_cost_drivers), OpenAPI 3.1 (5 ról RBAC, 18 endpointów, quantity-break, location comparison, material sensitivity) |
| [03-events-validation-monitoring.md](./03-events-validation-monitoring.md) | Event System (8 tematów Kafka, 3 schematy Avro, CBEOutboxPublisher), Validation Rules (9 reguł V001–V009, ValidationEngine, pre-approve checklist), Monitoring (24 metryki Prometheus, 6 dashboardów Grafana, 8 reguł Alertmanager, SLI/SLO) |
| [04-testing-risks-roadmap.md](./04-testing-risks-roadmap.md) | Testing (8 typów: unit/integration/accuracy golden/k6 load/API contract), 15 Ryzyk (R01–R15), Roadmap 28 sprintów 3 fazy (Foundation S1-S8, Enrichment+Analytics S9-S18, Intelligence+Scale S19-S28) |

## Architektura

```
BOM Engine (bom_line.released)
DAE (drawing.parsed)              ─────────────────────┐
SOP (line_item.priced)            Kafka enrichment      │
                                                        ▼
                                          ┌─────────────────────────┐
POST /api/v1/cbe/breakdowns ──────────►  │   CostBreakdownEngine   │
CostBreakdownRequest                     │   (Orchestrator)        │
  ├── MaterialInput                      └────────────┬────────────┘
  ├── OperationInput[]                                │
  ├── ToolingInput[]                     ┌────────────┼──────────────────────┐
  ├── location_code                      │            │                      │
  └── overhead_profile                   ▼            ▼                      ▼
                                   asyncio.gather (równolegle)
                                   ┌──────────┐ ┌──────────┐ ┌──────────────────┐
                                   │ Material │ │  Labor   │ │     Machine      │
                                   │ Calc     │ │  Calc    │ │     Calc         │
                                   └────┬─────┘ └────┬─────┘ └──────┬───────────┘
                                        │             │              │
                                   ┌────┴─────┐ ┌────┴─────┐        │
                                   │ Energy   │ │ Tooling  │        │
                                   │ Calc     │ │ Calc     │        │
                                   └────┬─────┘ └────┬─────┘        │
                                        │             │              │
                                        └──────┬──────┘──────────────┘
                                               │ primary_total
                                               ▼
                                   ┌───────────────────────┐
                                   │  Overhead & Margin    │
                                   │  (factory + SG&A +    │
                                   │   R&D + margin %)     │
                                   └───────────┬───────────┘
                                               │
                                               ▼
                                   ┌───────────────────────┐
                                   │  ValidationEngine     │  V001–V009
                                   └───────────┬───────────┘
                                               │
                                               ▼
                                   ┌───────────────────────┐
                                   │  CostBreakdownResult  │  DB write + Kafka
                                   └───────────────────────┘
```

## Struktura kosztów (6 kategorii)

| Kategoria | Składniki | Typowy udział |
|-----------|-----------|:-------------:|
| **MATERIAL** | RAW_MATERIAL, PURCHASED_COMPONENT, SURFACE_TREATMENT, SCRAP_ALLOWANCE | 30–50% |
| **LABOR** | DIRECT_LABOR, SETUP_LABOR, INSPECTION_LABOR, REWORK_LABOR | 15–35% |
| **MACHINE** | MACHINE_DEPRECIATION, MACHINE_MAINTENANCE, TOOLING_WEAR | 10–20% |
| **ENERGY** | ELECTRICITY, COMPRESSED_AIR, COOLANT, HEATING_COOLING | 2–8% |
| **TOOLING** | TOOL_AMORTIZATION, FIXTURE_COST, PROGRAMMING_COST | 1–15% (prototypy) |
| **OVERHEAD** | FACTORY_OVERHEAD, SG&A, R&D, PROFIT_MARGIN | 15–35% |

## Stawki robocizny (EUR/h)

| Lokalizacja | Operator | Setup | Overhead | Marża |
|-------------|:--------:|:-----:|:--------:|:-----:|
| DE | 42.00 | 52.00 | 28% | 8% |
| PL | 18.00 | 22.00 | 22% | 7% |
| CN |  8.00 | 10.00 | 18% | 6% |
| MX | 12.00 | 14.00 | 20% | 7% |
| IN |  6.00 |  8.00 | 16% | 6% |

## Overhead Profiles

| Profil | Overhead | SG&A | Marża | Zastosowanie |
|--------|:--------:|:----:|:-----:|-------------|
| DEFAULT | 100% | 100% | 100% | Standardowa kalkulacja |
| LEAN | 80% | 80% | 90% | Optymistyczne założenia |
| PREMIUM | 110% | 110% | 125% | Produkty high-end |
| EXPORT | 100% | 115% | 100% | Dodatkowe koszty eksportu |
| PROTOTYPE | 120% | 100% | 50% | Prototypy — pełny tooling |
| SERIES | 95% | 95% | 100% | Produkcja seryjna |

## Priorytety źródeł danych

| Priorytet | Źródło | Confidence |
|-----------|--------|:----------:|
| 1 | `QUOTE` — cena z oferty dostawcy (SOP) | 0.95 |
| 2 | `BOM` — dane z BOM Engine | 0.90 |
| 3 | `DRAWING` — dane z DAE (masa, materiał) | 0.85 |
| 4 | `RATE_TABLE` — tabele stawek | 0.75–0.85 |
| 5 | `MACHINE_DB` — dane maszyn | 0.78–0.85 |
| 6 | `ESTIMATE` — szacunek parametryczny | 0.50–0.65 |
| 7 | `DEFAULT` — wartość domyślna (fallback) | 0.40 |

## Confidence Bands

| Pasmo | Zakres | Znaczenie |
|-------|:------:|-----------|
| HIGH | ≥ 0.90 | Dane z ofert/BOM; APPROVE bezproblemowy |
| MEDIUM | 0.70–0.89 | Mix danych; APPROVE po przeglądzie |
| LOW | 0.50–0.69 | Głównie szacunki; APPROVE z override |
| INDICATIVE | < 0.50 | Dane domyślne; APPROVE zablokowany |

## Quantity Break — efekt skali

Koszty stałe (tooling, setup, programming) amortyzują się przy wyższych
ilościach. Endpoint `GET /parts/{id}/quantity-breaks` zwraca tabelę:

| Ilość | Unit cost EUR | Tooling % |
|------:|:-------------:|:---------:|
| 1 | 89.42 | 28.1% |
| 100 | 31.52 | 4.3% |
| 500 | 28.47 | 0.84% |
| 5 000 | 26.48 | 0.09% |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `CBE_VIEWER` | GET breakdowns, components, rates (read-only) |
| `CBE_ANALYST` | CBE_VIEWER + POST calculate, analytics, export |
| `CBE_ENGINEER` | CBE_ANALYST + PATCH draft, recalculate |
| `CBE_APPROVER` | CBE_ENGINEER + approve/reject, rate overrides |
| `CBE_ADMIN` | Wszystko + DELETE, manage machines, audit logs |

## SQL Schema (schemat `cbe`)

| Tabela | Opis |
|--------|------|
| `machines` | Profile maszyn (capex, OEE, power, maintenance rate) |
| `location_rates` | Stawki robocizny/energii/overhead per lokalizacja i data |
| `material_rates` | Historia cen materiałów (z SOP lub rate table) |
| `cost_breakdowns` | Kalkulacje kosztów z agregatami i generated columns |
| `cost_components` | Składniki kalkulacji z basis, confidence, data_source |
| `quantity_breaks` | Cache tabeli progów ilościowych |
| `breakdown_versions` | Snapshoty wersji przy zmianach statusu |
| `outbox_events` | Transactional Outbox dla Kafka |

## Event System (8 tematów Kafka)

| Temat | Trigger | Konsumenci |
|-------|---------|------------|
| `cbe.breakdown.requested` | POST /breakdowns | CBE Worker |
| `cbe.breakdown.calculated` | Status → CALCULATED | CEE, Notification |
| `cbe.breakdown.approved` | DB Trigger (APPROVED) | CEE, BOM Engine, RFQ Agent |
| `cbe.breakdown.rejected` | Status → REVIEWED (reject) | Notification, Audit |
| `cbe.breakdown.superseded` | Nowa kalkulacja zastępuje | Audit |
| `cbe.rates.updated` | PUT /rates/locations | CBE Worker (cache invalidation) |
| `cbe.quantity_break.ready` | Tabela progów wygenerowana | UI, CEE |
| `cbe.anomaly.detected` | ValidationEngine alert | Alert, Human Review |

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| `cbe_breakdown_duration_seconds` p95 (simple) | ≤ 2s |
| `cbe_breakdown_duration_seconds` p95 (full) | ≤ 10s |
| `cbe_api_duration_seconds` p95 GET | ≤ 300ms |
| `cbe_breakdown_total{status="error"}` rate | < 2% |
| `cbe_overall_confidence` median | ≥ 0.75 |
| HIGH + MEDIUM confidence rate | ≥ 70% |
| `cbe_outbox_lag_seconds` | < 30s |
| Availability | ≥ 99.5% |

## Skalowalność

| Poziom | Wolumen | Infrastruktura |
|--------|---------|----------------|
| L1 | ≤ 100 kalkulacji/h | 1 API pod, 1 worker, CPU |
| L2 | ≤ 500 kalkulacji/h | 2–4 API pods, 2–3 workers |
| L3 | ≤ 2 000 kalkulacji/h | HPA 3–10 API + 2–8 workers, Redis cache |
| L4 | > 2 000 kalkulacji/h | Multi-region, partitioned DB, batch endpoint |

HPA: `cbe-api` 2–15 pods (CPU 70%), `cbe-worker` 1–8 pods.

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `cbe`, 8 tabel, generated columns, GIN indexes)
- **Cache:** Redis 7+ (quantity-break cache TTL=3600s, rate lookup cache)
- **Messaging:** Apache Kafka 3+ (8 tematów, Avro + Schema Registry, Transactional Outbox)
- **Monitoring:** Prometheus (24 metryki) + Grafana (6 dashboardów) + Alertmanager (8 reguł)
- **Security:** JWT RS256, RBAC 5 ról, audit log full
- **Kubernetes:** HPA cbe-api 2–15 pods, cbe-worker 1–8 pods

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| BOM Engine | ← | Kafka | `bome.bom_line.released` → auto-trigger kalkulacji |
| DAE | ← | Kafka | `dae.drawing.parsed` → waga, materiał, operacje |
| SOP | ← | Kafka | `sop.line_item.priced` → aktualne ceny materiałów |
| CEE | → | Kafka | `cbe.breakdown.approved` → enriched cost estimate |
| RFQ Agent | → | Kafka | `cbe.breakdown.approved` → target cost w RFQ |
| Grafana / Prometheus | ← | Pull | Metrics scraping |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | DB, 5 kalkulatorów, API, validation, outbox |
| Enrichment + Analytics | S9–S18 | Quantity-break, location comparison, sensitivity, integracje DAE/SOP/BOME, dashboards |
| Intelligence + Scale | S19–S28 | Parametric estimation, anomaly detection, HPA L2, customer profiles, DR |
