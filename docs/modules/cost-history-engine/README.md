# Cost History Engine

Centralne repozytorium wszystkich historycznych wycen, ofert i kosztów platformy Industrial Cost Intelligence.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-domain-model-erd.md](./01-domain-model-erd.md) | Model domenowy (10 agregatów, 18 zdarzeń domenowych), pełne ASCII ERD, 5 grup kolorystycznych, 7 decyzji projektowych (append-only, bez fizycznych usunięć, soft versioning) |
| [02-cost-snapshot-quote-rfq-history.md](./02-cost-snapshot-quote-rfq-history.md) | CostSnapshotBuilder, CostSnapshotComparator, QuotePricer (floor/target/volume), RFQScoringEngine (price 50% + lead_time 20% + quality 20% + risk 10%), SupplierPriceTrendAnalyzer (anomalie 3σ), macierz konkurencyjności |
| [03-material-process-versioning-audit.md](./03-material-process-versioning-audit.md) | PriceForecastEngine (ensemble: Prophet 40% + ARIMA 35% + Linear 25%), ProcessCostTrendAnalyzer, VersionManager (rollback jako nowa wersja), SnapshotLineageTracer, AuditEvent RESTRICTIVE RLS, dekorator @log_sensitive_access, AuditQueryService |
| [04-retention-sql-schema-indexing-partitioning.md](./04-retention-sql-schema-indexing-partitioning.md) | RetentionPolicy, LegalHold, GDPRErasureHandler; pełny DDL PostgreSQL 16 (14 ENUMów, 24 tabele, 4 funkcje składowane, triggery); indeksy (BRIN, B-tree, GIN, partial, trgm); partycjonowanie (quarterly/monthly/yearly) + PartitionMaintenanceJob |
| [05-backup-security-gdpr-monitoring.md](./05-backup-security-gdpr-monitoring.md) | 4-poziomowy backup (WAL RPO=0, daily basebackup, weekly logical, monthly Glacier); 7 ról RBAC; RLS RESTRICTIVE (audit_events immutable); widok v_quotes_masked; inwentarz PII; GDPRRequest + GDPRErasureHandler; Art. 30 rejestr; DPIA; 20 metryk Prometheus; 8 reguł Alertmanager; 6 dashboardów Grafana |
| [06-api-events-testing-scalability-risks-roadmap.md](./06-api-events-testing-scalability-risks-roadmap.md) | OpenAPI 3.1 (22 endpointy, 7 tagów); 14 tematów Kafka; 5 schematów Avro; CHEOutboxPublisher; 8-typowa macierz testów; k6 load test (P95 <300ms read / <500ms write); 3 poziomy skalowalności; CHEDatabaseRouter; ClickHouse; 14 ryzyk; roadmap 30 sprintów (4 fazy) |

## Agregaty domenowe

| Agregat | Kluczowe atrybuty |
|---------|-------------------|
| CostSnapshot | snapshot_id, material_id, version (SEMVER), snapshot_hash (SHA-256), status (DRAFT/ACTIVE/SUPERSEDED/ARCHIVED) |
| SnapshotLineItem | line_item_id, snapshot_id, cost_component, amount_eur, currency, basis |
| QuoteRecord | quote_id, supplier_id, material_id, unit_price_eur, floor_price_eur (encrypted), validity_date |
| RFQRecord | rfq_id, rfq_number, status (DRAFT/PUBLISHED/EVALUATION/AWARDED/CANCELLED), award_criteria_weights |
| RFQResponse | response_id, rfq_id, supplier_id, scoring_details (JSONB) |
| SupplierPriceRecord | record_id, supplier_id, material_id, unit_price_eur, price_basis, effective_date |
| MaterialPriceRecord | record_id, material_id, price_type (MARKET/SPOT/FUTURES/INDEX), source, price_eur |
| ProcessCostRecord | record_id, process_id, cost_component (SETUP/RUNTIME/ENERGY/MAINTENANCE/SCRAP), cost_eur |
| CostVersion | version_id, entity_type, entity_id, version_number (SEMVER), change_type, superseded_by |
| AuditEvent | event_id, entity_type, entity_id, action, actor_id, actor_role, ip_address (encrypted), changes (JSONB) |

## Zdarzenia domenowe (Kafka)

| Temat | Klucz partycji | Retention |
|-------|---------------|-----------|
| `che.cost-snapshot.created` | snapshot_id | 365d |
| `che.cost-snapshot.versioned` | snapshot_id | 365d |
| `che.cost-snapshot.archived` | snapshot_id | 365d |
| `che.quote.recorded` | quote_id | 365d |
| `che.quote.approved` | quote_id | 365d |
| `che.quote.expired` | quote_id | 90d |
| `che.rfq.created` | rfq_id | 365d |
| `che.rfq.published` | rfq_id | 365d |
| `che.rfq.awarded` | rfq_id | 365d |
| `che.rfq.cancelled` | rfq_id | 90d |
| `che.supplier-price.recorded` | supplier_id | 730d |
| `che.material-price.recorded` | material_id | 730d |
| `che.process-cost.recorded` | process_id | 365d |
| `che.audit.written` | entity_id | 2555d (7 lat) |

## Role RBAC

| Rola | Uprawnienia |
|------|------------|
| COST_VIEWER | SELECT snapshots/quotes (bez floor_price), raporty publiczne |
| COST_ANALYST | + pełne szczegóły quote, tworzenie analiz, eksport |
| COST_MANAGER | + zatwierdzanie wycen, zarządzanie RFQ, edycja polityk |
| PROCUREMENT_SPECIALIST | + praca z RFQ, ocena dostawców, scoring |
| FINANCE_CONTROLLER | + floor_price, margin_pct, widoki finansowe |
| AUDIT_READER | SELECT audit_events (tylko odczyt) |
| CHE_ADMIN | Pełny dostęp, zarządzanie retencją, legal hold |
| SYSTEM_INTEGRATOR | INSERT via API (brak SELECT poza własnym zakresem) |

## Polityki retencji

| Kategoria | Retencja aktywna | Archiwum zimne | Usunięcie |
|-----------|-----------------|----------------|-----------|
| Cost Snapshots (ACTIVE) | 7 lat | 3 lata S3 Glacier | 10 lat |
| Cost Snapshots (ARCHIVED) | 2 lata | 5 lat S3 Glacier | 7 lat |
| Quote Records | 7 lat | 3 lata S3 Glacier | 10 lat |
| RFQ Records | 7 lat | 3 lata S3 Glacier | 10 lat |
| Supplier Price Records | 10 lat | 5 lat S3 Glacier | 15 lat |
| Material Price Records | 10 lat | 5 lat S3 Glacier | 15 lat |
| Audit Events | 7 lat | Nigdy | Po 7 latach |
| GDPR-erasable PII | Do żądania | N/A | 30 dni od żądania |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | Schemat DB, partycjonowanie, audit trail, backup, podstawowe API |
| RFQ & Pricing | S9–S16 | RFQ engine, scoring, quote approval workflow, Kafka eventing |
| Intelligence & Compliance | S17–S24 | Forecasting (Prophet/ARIMA), anomaly detection, GDPR erasure, legal hold |
| Scale & Analytics | S25–S30 | ClickHouse analytics, AI embeddings, Copilot, Carbon Cost Layer |

## Kluczowe SLA

| Operacja | P95 | P99 |
|----------|-----|-----|
| GET snapshot (cached) | <50ms | <100ms |
| GET snapshot (DB) | <150ms | <300ms |
| POST snapshot | <300ms | <500ms |
| RFQ scoring (10 responses) | <500ms | <1000ms |
| Price forecast (single) | <2s | <5s |
| Semantic search (pgvector) | <200ms | <400ms |

## Zależności techniczne

- **Baza danych:** PostgreSQL 16+ (`pgvector`, `pg_trgm`, `uuid-ossp`, `pg_partman`)
- **Cache:** Redis 7+ (9 wzorców cache, TTL 5min–4h)
- **Messaging:** Apache Kafka 3+ (14 tematów, Avro + Schema Registry)
- **Analytics:** ClickHouse 23+ (MergeTree, materialized views)
- **AI Forecasting:** Prophet + ARIMA + scikit-learn (ensemble, MAPE <8%)
- **Field Encryption:** AES-256-GCM via AWS KMS (floor_price, margin_pct, ip_address)
- **Backup:** WAL archiving + pg_basebackup + pg_dump + S3 Glacier
- **Monitoring:** Prometheus + Grafana + Alertmanager (20 metryk, 8 reguł)

## Integracje

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| Cost Calculation Engine | ← | REST + Kafka | Zapisuje snapshoty kosztów |
| Supplier Intelligence Engine | ← | Kafka | Ceny dostawców, scoringi |
| Material Intelligence Engine | ← | Kafka | Ceny rynkowe materiałów |
| Manufacturing Process Engine | ← | Kafka | Koszty procesów produkcyjnych |
| RFQ Engine | ↔ | REST + Kafka | RFQ records, award events |
| ERP (SAP / Oracle) | ← | REST | Purchase orders, invoices |
| Finance / Controlling | → | REST | Raporty kosztowe, budgety |
| Audit & Compliance | → | Kafka | audit_events stream |
| Data Warehouse / BI | → | ClickHouse | Historyczne agregaty analityczne |
