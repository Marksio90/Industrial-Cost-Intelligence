# Supplier Intelligence Engine — Domain Model

## 1. Supplier Domain Model

### Kontekst domenowy

Supplier Intelligence Engine (SIE) to centralny system wiedzy o dostawcach platformy Industrial Cost Intelligence. Agreguje dane z transakcji zakupowych, ocen jakości, wyników dostaw, danych rynkowych i zewnętrznych sygnałów ryzyka — tworząc kompleksowy profil każdego dostawcy, gotowy do konsumpcji przez kalkulacje kosztów, RFQ, AI i planowanie zakupów.

### Context Map

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                       SUPPLIER INTELLIGENCE ENGINE                            │
│                                                                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  Supplier    │  │  Rating &    │  │  Risk        │  │  Market          │ │
│  │  Master Data │─▶│  Scoring     │─▶│  Analysis    │  │  Intelligence    │ │
│  │  (Profile)   │  │  Engine      │  │  (Financial  │  │  (Benchmarks,    │ │
│  └──────────────┘  └──────────────┘  │  + Geopolit.)│  │   Indices)       │ │
│         │                │           └──────────────┘  └──────────────────┘ │
│         │                │                  │                  │              │
│         ▼                ▼                  ▼                  ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────────────┐   │
│  │  Delivery    │  │  Quality     │  │         AI Layer                  │   │
│  │  Performance │  │  Metrics     │  │  (Similarity, Recommendations,   │   │
│  │  + Lead Time │  │  (PPM, NCR)  │  │   Embeddings, Anomaly Detection) │   │
│  └──────────────┘  └──────────────┘  └──────────────────────────────────┘   │
│         │                │                                                    │
│         └────────────────┴──────────────────────────────────────────────────┤
│                                    │                                          │
│                    ┌───────────────▼──────────────┐                          │
│                    │   Historical Pricing &        │                          │
│                    │   MOQ / Lead Time Catalogue   │                          │
│                    └──────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────────────────────┘

External upstream:
  ← ERP (SAP MM, Oracle SCM)        purchase orders, invoices, GR/GI
  ← MES / Quality System            NCRs, inspection results, PPM
  ← Financial data providers        Dun & Bradstreet, Creditsafe, Moody's
  ← Geopolitical risk feeds         Control Risks, Coface, Atradius
  ← Logistics providers             carrier tracking, customs data

External downstream:
  → Cost Calculation Engine          supplier price inputs
  → Material Intelligence Engine     supplier-material mappings
  → RFQ Engine                       preferred supplier lists, scores
  → Manufacturing Process Engine     lead times, MOQ constraints
  → Procurement Workflows            auto-approval thresholds
```

### Agregaty domenowe

| Agregat | Korzeń | Encje wewnętrzne |
|---------|--------|-----------------|
| SupplierAggregate | Supplier | SupplierContact, SupplierAddress, SupplierCertification |
| PerformanceAggregate | PerformancePeriod | DeliveryRecord, QualityRecord, ResponseRecord |
| ScorecardAggregate | SupplierScorecard | ScoreComponent, ScoreHistory |
| PricingAggregate | PriceOffer | PriceHistory, PriceIndex |
| RiskAggregate | RiskProfile | RiskFactor, RiskAlert |
| FinancialAggregate | FinancialProfile | FinancialSignal, CreditRecord |
| RelationshipAggregate | SupplierRelationship | PreferredStatus, Contract, ApprovalRecord |
| CategoryMappingAggregate | SupplierCategory | MaterialMapping, ProcessMapping |

### Zdarzenia domenowe

| Zdarzenie | Wyzwalacz | Konsumenci |
|-----------|-----------|-----------|
| SupplierRegistered | Nowy dostawca | Search Index, Embedding Service, ERP Sync |
| SupplierApproved | Zatwierdzenie | Procurement, RFQ Engine |
| SupplierSuspended | Zawieszenie | Procurement (block), RFQ Engine |
| SupplierDeactivated | Wyłączenie | All consumers |
| ScorecardUpdated | Przeliczenie scorecadu | RFQ Engine, Cost Calc, Dashboard |
| DeliveryRecorded | Nowa dostawa (GR) | Performance Calc, Scorecard |
| NCRRaised | Reklamacja jakości | Quality Dashboard, Scorecard |
| NCRClosed | Zamknięcie NCR | Scorecard update |
| PriceOfferReceived | Nowa oferta cenowa | Material Intelligence, Cost Calc |
| PriceExpired | Wygaśnięcie oferty | Procurement Alert |
| RiskAlertRaised | Nowy sygnał ryzyka | Procurement Director, Dashboard |
| RiskAlertResolved | Zamknięcie alertu | Dashboard |
| FinancialSignalReceived | Dane finansowe (ext.) | Risk Engine |
| LeadTimeChanged | Zmiana lead time | Scheduler, MRP, Cost Calc |
| EmbeddingRefreshed | Zmiana profilu | Vector Search Index |

### Typy dostawców

| Typ | Kod | Opis |
|-----|-----|------|
| Producent | MANUFACTURER | Wytwarza produkty samodzielnie |
| Hurtownia | WHOLESALER | Kupuje i odsprzedaje hurtowo |
| Dystrybutor | DISTRIBUTOR | Autoryzowany dystrybutor marki |
| Podwykonawca | SUBCONTRACTOR | Świadczy usługi produkcyjne/montażowe |
| Trader | TRADER | Pośrednik, broker towarowy |
| Agent | AGENT | Pełnomocnik producenta |
| Leasingodawca | LESSOR | Wypożyczalnia maszyn/sprzętu |
