# BOM Engine (BOME)

Silnik zarządzania strukturami materiałowymi (Bill of Materials) dla platformy
Industrial Cost Intelligence. Obsługuje wielopoziomowe BOM, warianty konfiguracyjne,
podstawienia materiałowe, kalkulację kosztów z rollupem oraz pełny cykl zmian
inżynierskich (ECO/MCO/DCO) — zintegrowany z systemami CAD (Teamcenter, Windchill).

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-domain-model-structure-variants-substitution-cost-rollup.md](./01-domain-model-structure-variants-substitution-cost-rollup.md) | Domain Model (BOMType, BOMStatus, BOMLineType, BOMLine, BOMNode), Multi-level BOM (BOMTreeService, rekurencyjne CTE, phantom expansion, where-used), Variant Management (Discrete/Configurable/Modular/Planning, VariantRule, condition_expr), Material Substitution (SubstitutionReason, RoHS/REACH, approval flow), Cost Roll-up (CostRollupService, DFS bottom-up, overhead rates per location, confidence scoring) |
| [02-sql-api-events-version-control.md](./02-sql-api-events-version-control.md) | SQL Schema PostgreSQL 16 (schemat `bome`, 6 ENUMów, 12 tabel, GENERATED ALWAYS AS, triggery snapshot+edit-guard, ltree, indeksy), OpenAPI 3.1 (18 endpointów, 6 ról RBAC), Event Model (8 tematów Kafka, 4 schematy Avro, Transactional Outbox), Version Control (BOMVersionControl, revision copy BFS, diff_versions, BOMDiff) |
| [03-change-management-cad-validation-monitoring.md](./03-change-management-cad-validation-monitoring.md) | Change Management (ECO/MCO/DCO/DEVIATION/CORRECTION, ChangeOrderService, APPROVAL_CHAINS, RISK_REQUIRED_ROLES, numeracja ECO-YYYY-NNNN), CAD Integration (Teamcenter SOA REST, Windchill REST, CSV import, BOMImporter BFS, PLMItem normalization), Validation Rules (V001–V016, 7 ERROR + 9 WARNING/INFO), Monitoring (25 metryk Prometheus, 7 dashboardów Grafana, Alertmanager) |
| [04-testing-scalability-risks-roadmap.md](./04-testing-scalability-risks-roadmap.md) | Testing (8 typów: unit/integration/ML/validation/snapshot/CAD/load k6/contract), Scalability (4 poziomy L1–L4, HPA Kubernetes, RollupCacheService Redis, ltree GIST, mv_where_used materialized), 15 Ryzyk (R01–R15), Roadmap 40 sprintów 4 fazy |

## Architektura

```
CAD Systems (Teamcenter / Windchill / CSV)
        │
        ▼
BOMImporter (BFS hierarchy) ──► bome.bom_headers + bome.bom_lines
                                        │
                          ┌─────────────┼─────────────┐
                          ▼             ▼              ▼
               BOMTreeService    VariantConfigurator  BOMValidator
               (recursive CTE    (condition_expr      (V001–V016,
                ltree GIST)       eval isolated)       16 reguł)
                          │
                          ▼
               CostRollupService ──► bome.cost_rollups
               (DFS bottom-up        │
                overhead per loc)    ▼ Redis TTL=6h
                                RollupCacheService
                          │
                          ▼
               ChangeOrderService ──► bome.change_orders
               (ECO/MCO/DCO          │
                APPROVAL_CHAINS)     ▼
                              TransactionalOutbox ──► Kafka ──► CEE/CLS/ERP
                          │
                          ▼
               BOMVersionControl ──► bome.bom_versions (JSONB snapshots)
               (snapshot trigger      bome.bom_revisions
                on status change)
```

## Cykl życia BOM

```
DRAFT → IN_REVIEW → RELEASED → FROZEN → OBSOLETE
                             ↓
                        SUPERSEDED (gdy tworzona nowa rewizja)
```

### Warunki przejścia do RELEASED

| Warunek | Sprawdzany przez |
|---------|-----------------|
| Brak reguł ERROR (V001–V016) | BOMValidator |
| Rollup confidence ≥ 0.70 | V015 |
| Conajmniej 1 aktywna linia non-REFERENCE | V014 |
| Zatwierdzona przez APPROVAL_CHAIN | ChangeOrderService |

## Typy BOM

| Typ | Zastosowanie |
|-----|-------------|
| `ENGINEERING` | Struktura konstruktorska, wersja projektowa |
| `MANUFACTURING` | BOM produkcyjny z phantom expansion |
| `SERVICE` | Części zamienne, serwis posprzedażowy |
| `COSTING` | Kalkulacja kosztów (może różnić się od MFGBOM) |
| `PLANNING` | 150% BOM z % splitami dla MRP |

## Warianty

| Strategia | Opis | Przypadek użycia |
|-----------|------|-----------------|
| Discrete | Osobne BOM per wariant | < 10 wariantów |
| Configurable 150% | VariantRule + condition_expr | > 10 wariantów, opcje |
| Modular | Moduły składane w run-time | Maszyny konfigurowane na zamówienie |
| Planning | Splits procentowe | Prognozowanie MRP |

## Cost Roll-up — stawki overhead per lokalizacja

| Lokalizacja | Overhead |
|------------|---------|
| DE | 25% |
| US | 22% |
| PL | 18% |
| CZ | 19% |
| HU | 17% |
| RO | 15% |
| MX | 14% |
| TR | 13% |
| CN | 12% |
| IN | 10% |

### Priorytety źródeł kosztu

| Źródło | Confidence |
|--------|-----------|
| OVERRIDE (ręczny) | 1.00 |
| PRICE_MASTER | 0.95 |
| CEE prediction | 0.75 |
| ZERO (brak danych) | 0.00 |

### Progi confidence rollup

| Próg | Ocena |
|------|-------|
| ≥ 0.90 | HIGH |
| 0.70–0.89 | MEDIUM |
| 0.50–0.69 | LOW |
| < 0.50 | INDICATIVE |

Minimalne confidence do RELEASE: **0.70**

## Change Management

| Typ CO | Łańcuch zatwierdzenia | Typowy czas |
|--------|----------------------|------------|
| ECO | engineering_lead → quality_manager → procurement | 5–10 dni |
| MCO | manufacturing_engineer → quality_manager | 2–5 dni |
| DCO | engineering_lead | 1–2 dni |
| DEVIATION | quality_manager → customer_approval | 3–7 dni |
| CORRECTION | change_owner | < 1 dzień |

Numeracja: `ECO-2026-0042`, `MCO-2026-0007`, ...

## Integracja CAD

| System | Protokół | Format | Synchronizacja |
|--------|---------|--------|---------------|
| Teamcenter | SOA REST | Własny JSON | Pull on-demand + webhook |
| Windchill | REST API | Windchill JSON | Pull on-demand |
| Generic CAD | CSV upload | RFC 4180 | Batch import |

`BOMImporter` używa BFS (deque) — gwarantuje wstawienie rodzica przed dzieckiem.

## Reguły walidacji

| Reguła | Opis | Poziom | Blokuje release |
|--------|------|--------|----------------|
| V001 | Pusty BOM | ERROR | ✓ |
| V002 | Cykliczne referencje | ERROR | ✓ |
| V003 | Głębokość > 15 | WARNING | — |
| V004 | Ilość ≤ 0 | ERROR | ✓ |
| V005 | Scrap ≥ 50% | ERROR | ✓ |
| V006 | Scrap 20–50% | WARNING | — |
| V007 | Pusty item_code | ERROR | ✓ |
| V008 | TOOLING bez kosztu | WARNING | — |
| V010 | REFERENCE qty ≠ 1 | INFO | — |
| V011 | Brak ceny w MM | WARNING | — |
| V012 | Duplikat pozycji | ERROR | ✓ |
| V013 | Phantom bez sub-BOM | WARNING | — |
| V014 | Brak linii non-REF | ERROR | ✓ |
| V015 | Confidence < 0.70 | ERROR | ✓ |
| V016 | Krytyczny item bez lead_time | WARNING | — |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `BOME_VIEWER` | GET BOM, linie, wersje, koszty |
| `BOME_ENGINEER` | BOME_VIEWER + tworzenie/edycja BOM DRAFT, import CAD |
| `BOME_ANALYST` | BOME_VIEWER + analiza kosztów, warianty, where-used |
| `BOME_REVIEWER` | BOME_ENGINEER + review, przejście IN_REVIEW → RELEASED |
| `BOME_PROCUREMENT` | BOME_VIEWER + podstawienia materiałowe, approvale |
| `BOME_ADMIN` | Wszystko + DELETE, zarządzanie schematem, FROZEN/OBSOLETE |

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| `bome_tree_build_duration_seconds` p95 | < 300ms |
| `bome_cost_rollup_duration_seconds` p95 | < 2s |
| `bome_where_used_duration_seconds` p95 | < 200ms |
| `bome_validation_errors_total` | monitorowane |
| `bome_change_order_approval_duration_hours` | < SLA per typ |
| `bome_rollup_cache_hit_ratio` | > 80% |
| `bome_cad_import_duration_seconds` | < 30s |

7 dashboardów Grafana: BOM Overview, Cost Analysis, Change Management, CAD Integration, Performance, Validation, Kafka Events.

## SLA i KPIs (cel po S40)

| Metryka | Cel |
|---------|-----|
| Tree build p95 | < 100ms (L1–L3) |
| Cost rollup p95 | < 2s (L1–L2), < 5s (L3) |
| Where-used p95 | < 200ms |
| CAD import (1000 nodes) | < 30s |
| BOM release lead time | < 2h (z approvalami) |
| Rollup cache hit rate | > 80% |
| Rollup confidence ≥ 0.90 | > 75% BOM |
| ECO cycle time | < 5 dni avg |
| API availability | > 99.9% |
| Kafka event delivery | at-least-once, < 500ms lag |

## Skalowalność

| Poziom | BOM nodes | Strategia |
|--------|-----------|-----------|
| L1 | ≤ 1 000 | Rekurencyjne CTE, indeksy B-tree |
| L2 | ≤ 10 000 | + Redis rollup cache |
| L3 | ≤ 100 000 | + ltree GIST, mv_where_used CONCURRENTLY |
| L4 | > 100 000 | + CQRS WhereUsedReadModel (Redis Set), partycjonowanie |

HPA: `bome-api` 2–20 pods, `bome-rollup-worker` 1–10 pods.

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `bome`, 12 tabel, ltree extension)
- **Cache:** Redis 7+ (rollup TTL=6h, where-used read model)
- **Messaging:** Apache Kafka 3+ (8 tematów, Avro + Schema Registry, Transactional Outbox)
- **Monitoring:** Prometheus (25 metryk) + Grafana (7 dashboardów) + Alertmanager
- **Security:** JWT RS256, RBAC 6 ról
- **Kubernetes:** HPA bome-api 2–20 pods, bome-rollup-worker 1–10 pods
- **CAD Connectors:** Teamcenter SOA REST, Windchill REST, CSV import

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| CEE API | → | Kafka | Zapytania o wycenę komponentów (koszty jednostkowe) |
| CLS | → | Kafka | Feedback kosztowy dla uczenia ciągłego |
| SAP ERP (MM/PP) | ↔ | REST / IDoc | Material Master, Production Orders |
| Teamcenter | ← | SOA REST | Struktura produktu, BOM inżynierski |
| Windchill | ← | REST | BOM inżynierski, revisions |
| RFQ Agent | → | Kafka | Zapytania o rynkowe ceny komponentów |
| Grafana / Prometheus | ← | Pull (HTTP) | Metrics scraping |
| Slack / PagerDuty | → | Webhook | Alerty walidacji, approval reminders |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S10 | DB schema, BOMTreeService, CostRollupService, REST API v1, podstawowy import CSV |
| Variants & Substitution | S11–S20 | VariantConfigurator, MaterialSubstitution, BOMValidator (V001–V016), CAD connectors |
| Change Management | S21–S30 | ChangeOrderService, APPROVAL_CHAINS, BOMVersionControl, diff, snapshot trigger |
| Intelligence & Scale | S31–S40 | ltree L3+, Redis CQRS L4, A/B cost scenarios, ITAR/EAR checks, DR, SLA hardening |
