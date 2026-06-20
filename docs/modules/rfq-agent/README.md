# RFQ Agent

Autonomiczny agent AI zarządzający pełnym cyklem zapytań ofertowych (RFQ)
dla platformy Industrial Cost Intelligence. Generuje zapytania, wysyła emaile
do dostawców, scrapuje portale branżowe, normalizuje oferty, porównuje ceny
i aktualizuje bazę cen — z kontrolowanym udziałem człowieka w punktach
decyzyjnych wysokiego ryzyka.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-architecture-workflow-decision-discovery-email.md](./01-architecture-workflow-decision-discovery-email.md) | Agent Architecture (ReAct + Claude claude-opus-4-8, ToolRegistry, AgentMemory), Workflow Design (state machine 15 stanów, RFQCycleOrchestrator), Decision Engine (MCDA scoring, 5 wag, DecisionRuleEngine, auto-select vs HITL), Supplier Discovery (4 źródła: DB/SIE/SCSE/web scraping, Playwright Thomasnet/Europages), Email Generation System (RFQEmailGenerator, EmailDispatcher, SMTP retry) |
| [02-parser-normalization-comparator-risk-hitl.md](./02-parser-normalization-comparator-risk-hitl.md) | Response Parser (LLM email parser, Playwright portal scraper, EDI/JSON), Offer Normalization (FX conversion ECB, incoterms adders, payment terms NPV, risk scoring), Pricing Comparator (IQR outlier detection, market index, recommendation text), Risk Controls (5 warstw: pre-action/anti-spam/anomaly/spend/audit, RiskPolicy), Human-in-the-Loop (HITLGateway asyncio Future, NotificationService Slack+Email, 8 HITL types) |
| [03-sql-api-events-prompts.md](./03-sql-api-events-prompts.md) | SQL Schema PostgreSQL 16 (7 ENUMów, 15 tabel, 4 funkcje, triggery, 3 widoki), OpenAPI 3.1 (14 endpointów, 7 ról RBAC), Event System (10 tematów Kafka, 3 schematy Avro, 5 konsumentów zewnętrznych), Prompt Templates (6 szablonów: RFQ email/follow-up/negotiation/parse/supplier-analysis/final-recommendation, 8 języków) |
| [04-monitoring-security-antispam-testing-scalability-risks-roadmap.md](./04-monitoring-security-antispam-testing-scalability-risks-roadmap.md) | Monitoring (20 metryk Prometheus, 8 reguł Alertmanager, 7 dashboardów Grafana), Security (JWT RS256, RBAC, CredentialVault Fernet, PIIMasker, 12 kontroli), Anti-spam Rules (AntiSpamEngine: 9 reguł, 30-day cooldown, rate limits), Testing (8 typów: unit/integration/agent-sim/contract/email/scraping/load k6/security), Scalability (4 poziomy, HPA Kubernetes, AgentWorkerPool, DistributedEmailQueue Redis), 15 Ryzyk, Roadmap 32 sprinty 4 fazy |

## Architektura agenta

```
Trigger (RFQ Engine / Portal / API / Scheduler)
        │
        ▼
RFQCycleOrchestrator ──► RFQAgent (ReAct loop)
        │                       │
        │         ┌─────────────┼─────────────────┐
        │         ▼             ▼                 ▼
        │   Thought        Action           Observation
        │   (LLM reasoning) (Tool call)    (Tool result)
        │
        ├── SupplierDiscoveryEngine ──► DB + SIE + SCSE + Playwright
        ├── AntiSpamEngine ──────────► 9 reguł + DB cooldown check
        ├── RFQEmailGenerator ───────► Claude claude-opus-4-8 → 6 szablonów × 8 języków
        ├── EmailDispatcher ─────────► SMTP + retry + logging
        ├── EmailResponseParser ─────► Claude NLP extraction
        ├── SupplierPortalScraper ───► Playwright (Thomasnet, Europages, Kompass)
        ├── OfferNormalizer ─────────► FX + Incoterms + Payment Terms NPV
        ├── PricingComparator ───────► MCDA scoring + IQR outlier + ranking
        ├── RiskController ──────────► 5-layer risk evaluation
        ├── HITLGateway ─────────────► asyncio Future + Slack + Email notification
        └── PriceDBUpdater ──────────► market_price_index + CHE via Kafka
```

## Cykl RFQ — stany

```
DRAFT → SUPPLIER_DISCOVERY → [HITL?] → RFQ_GENERATION
      → EMAIL_DISPATCH → AWAITING_RESPONSES / SCRAPING_PORTALS
      → RESPONSE_PARSING → OFFER_NORMALIZATION → OFFER_COMPARISON
      → [HITL?] → RECOMMENDATION → PRICE_DB_UPDATE → COMPLETED
```

## Decision Engine — scoring

| Kryterium | Waga | Źródło danych |
|-----------|------|--------------|
| Cena (vs target + rynek) | 45% | NormalizedOffer.unit_price_eur |
| Jakość dostawcy | 25% | SIE scorecard (overall_score) |
| Czas dostawy | 15% | ParsedResponse.delivery_days |
| Certyfikaty | 10% | Pokrycie wymaganych certów |
| Relacja historyczna | 5% | past_transactions.quality_rating |

**Auto-select** gdy: composite_score ≥ 0.80 AND spend ≤ €50K AND brak risk_flags AND ≥ 2 oferty

## Anti-spam — reguły

| Reguła | Próg | Akcja |
|--------|------|-------|
| Same material cooldown | 30 dni/dostawca | DEFER |
| Daily supplier limit | 3 emaile/dzień | DEFER |
| Weekly supplier limit | 8 emaile/tydzień | DEFER |
| Global hourly rate | 200 emaili/h | DEFER |
| Blocked domain | Lista | BLOCK |
| Supplier blacklist | Aktywny wpis | BLOCK |

## Human-in-the-Loop — triggery

| Sytuacja | Priorytet | Timeout |
|----------|-----------|---------|
| Wydatek > €50K | Urgent | 4h |
| Anomalia cenowa (< 30% target) | Urgent | 4h |
| Przekroczenie budżetu | Urgent | 4h |
| Lista dostawców > 5 (nowych) | Normal | 24h |
| Brak wymaganych certyfikatów | Normal | 24h |
| Pewność porównania < 0.70 | Normal | 24h |
| Tylko 1 oferta | Normal | 48h |

## Stack techniczny

- **Agent LLM:** Claude claude-opus-4-8 (tool use, ReAct pattern)
- **Backend:** Python 3.12 + FastAPI + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `rfqa`, 15 tabel)
- **Cache/Queue:** Redis 7+ (email queue, FX cache, rate limits)
- **Web Scraping:** Playwright (headless Chromium)
- **Email:** SMTP (TLS) + async retry
- **Messaging:** Apache Kafka 3+ (10 tematów, Avro + Schema Registry)
- **Security:** JWT RS256, Fernet encryption, RBAC 7 ról
- **Monitoring:** Prometheus (20 metryk) + Grafana (7 dashboardów) + Alertmanager (8 reguł)
- **Kubernetes:** HPA API pods 2-20 + Agent worker pool 2-30

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| RFQA_VIEWER | Odczyt RFQ, dostawców, analityki |
| RFQA_USER | RFQA_VIEWER + tworzenie i anulowanie RFQ |
| RFQA_ANALYST | RFQA_USER + traces, offers, market prices |
| RFQA_REVIEWER | RFQA_ANALYST + HITL queue + zatwierdzanie decyzji |
| RFQA_PROCUREMENT | RFQA_REVIEWER + zarządzanie dostawcami + blacklist |
| RFQA_OPS | Wszystko + portal credentials + agent config |
| RFQA_ADMIN | Pełny dostęp + DELETE + blacklist admin |

## SLA i KPIs

| Metryka | Cel |
|---------|-----|
| Czas cyklu RFQ (end-to-end) | < 48h |
| Auto-approval rate | > 60% |
| Email reply rate | > 35% |
| Średnie oszczędności vs target | > 5% |
| HITL timeout rate | < 10% |
| Scraping success rate | > 70% |
| API P95 latency | < 500ms |
| Agent cost per RFQ | < $2.00 USD |

## Integracje

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| Cost Estimation Engine (CEE) | ← | REST | Cena docelowa per RFQ |
| Similarity Cost Search Engine (SCSE) | ← | REST | Podobne historyczne wyceny → kandydaci na dostawców |
| Supplier Intelligence Engine (SIE) | ← | REST + Kafka | Scorecard dostawców, certyfikaty |
| Cost History Engine (CHE) | ← / → | Kafka | Historyczne ceny (discovery) / nowe ceny (update) |
| Material Intelligence Engine (MIE) | ← | Kafka | Aktualizacje cen materiałów docelowych |
| Procurement Portal (UI) | → | REST + WebSocket | HITL decisions, RFQ dashboard |
| ERP (SAP MM/Ariba) | ↔ | REST/EDI | PO creation after RFQ approval |
| Email Gateway | ↔ | SMTP + Webhook | Send RFQ emails / receive replies |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | DB schema, supplier discovery, email dispatch, offer normalization, HITL |
| Autonomous Agent | S9–S16 | ReAct agent, Playwright scraping, Kafka events, full automation |
| Intelligence | S17–S24 | Negocjacje AI, ML supplier scoring, 8 języków, analytics |
| Scale & Compliance | S25–S32 | EDI/ERP, multi-tenant, GDPR, GPU inference |
