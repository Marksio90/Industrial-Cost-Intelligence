# Supplier Offer Parser (SOP)

System parsowania ofert dostawców dla platformy Industrial Cost Intelligence.
Przetwarza wiadomości e-mail, PDF, Excel, EDI i inne formaty — ekstrahuje ceny,
normalizuje jednostki, rozpoznaje materiały i mapuje pozycje na linie BOM przy użyciu
NLP wielojęzycznego, reguł domenowych i embeddingów semantycznych.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-document-ingestion-nlp-entity-price.md](./01-document-ingestion-nlp-entity-price.md) | Document Ingestion (12 formatów, 8 kanałów, EmailIMAPIngestor, S3DropIngestor, konwertery), NLP Pipeline (8 etapów, NLPContext, TextNormalizer), Entity Extraction (SpacyNERBackend, RegexNERBackend, 19 EntityType, StructuredOfferExtractor), Price Extraction (PriceExtractor, EN/DE number parsing, price-break detection, IQR outlier filtering) |
| [02-unit-conversion-supplier-mapping-sql-api.md](./02-unit-conversion-supplier-mapping-sql-api.md) | Unit Conversion Engine (33 jednostki, 7 wymiarów, UnitConversionEngine, FXRateService ECB, per-100 pricing), Supplier Mapping (4 strategie: domain/VAT/DUNS/fuzzy), BOM Mapping (exact/fuzzy/embedding, MaterialMatcher), SQL Schema PostgreSQL 16 (schemat `sop`, 6 ENUMów, 9 tabel, triggery, widoki), OpenAPI 3.1 (5 ról RBAC, endpointy) |
| [03-events-errors-monitoring.md](./03-events-errors-monitoring.md) | Event System (9 tematów Kafka, 4 schematy Avro, SOPOutboxPublisher), Error Handling (12 klas błędów, OfferParseErrorHandler, DuplicateChecker SHA-256, PriceQualityGuard, ValidationEngine V001-V005), Monitoring (27 metryk Prometheus, 7 dashboardów Grafana, 8 reguł Alertmanager) |
| [04-testing-risks-roadmap.md](./04-testing-risks-roadmap.md) | Testing (8 typów: unit/integration/NLP golden set/k6 load/EDI contract/data quality/security/BOM mapping), 15 Ryzyk (R01–R15), Roadmap 32 sprinty 4 fazy (Foundation S1-S8, NLP+Mapping S9-S16, Intelligence S17-S24, Production Scale S25-S32) |

## Architektura

```
Supplier Offer (Email / PDF / Excel / EDI / CSV / API / ...)
        │
        ▼
┌─────────────────────────┐
│   Ingestion Gateway     │  IMAP / SFTP / S3 Drop / Webhook / EDI AS2
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│   Document Converter    │  PDF (PyMuPDF) / Excel (openpyxl) / HTML (BS4) / EDI
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│                        NLP Pipeline (8 etapów)                  │
│                                                                 │
│  tokenize → sentence_split → normalize_text → ner_extract →    │
│  price_extract → unit_resolve → material_match → line_item_group│
└──────────┬──────────────────────────────────────────────────────┘
           │
    ┌──────┴─────────────────────────────┐
    │                                   │
    ▼                                   ▼
┌──────────────────┐          ┌─────────────────────────┐
│  SpacyNER +      │          │  StructuredOfferExtractor│
│  RegexNER        │          │  (column header matching  │
│  (19 EntityTypes)│          │   6 field families)      │
└──────┬───────────┘          └────────────┬────────────┘
       │                                   │
       └──────────────┬────────────────────┘
                      │
                      ▼
          ┌──────────────────────┐
          │   PriceExtractor     │  EN/DE formats, price-break, IQR outliers
          └──────────┬───────────┘
                     │
                     ▼
          ┌──────────────────────┐
          │ UnitConversionEngine │  33 jednostki, 7 wymiarów, per-100 pricing
          └──────────┬───────────┘
                     │
                     ▼
          ┌──────────────────────┐
          │   FXRateService      │  ECB daily XML → EUR normalization
          └──────────┬───────────┘
                     │
              ┌──────┴──────────────────┐
              │                        │
              ▼                        ▼
  ┌──────────────────────┐  ┌──────────────────────┐
  │   SupplierMapper     │  │     BOMMapper         │
  │ domain/VAT/DUNS/fuzzy│  │ exact/fuzzy/embedding │
  └──────────┬───────────┘  └──────────┬───────────┘
             │                         │
             └──────────┬──────────────┘
                        │
                        ▼
            ┌──────────────────────┐
            │  ParsedOffer Result  │  confidence, DB write, Kafka events
            └──────────────────────┘
```

## Obsługiwane formaty wejściowe

| Format | Kanał | Strukturyzacja | Multilingual | Kompleksowość |
|--------|-------|:--------------:|:------------:|:------------:|
| EMAIL_HTML | IMAP / API | Częściowa | ✓ | Średnia |
| EMAIL_TEXT | IMAP / API | Niska | ✓ | Wysoka |
| PDF | SFTP / S3 / Upload | Niska–Średnia | ✓ | Wysoka |
| EXCEL (.xlsx) | SFTP / S3 / Upload | Wysoka | ✓ | Niska |
| CSV | SFTP / S3 / Upload | Wysoka | ✓ | Niska |
| WORD (.docx) | SFTP / S3 / Upload | Częściowa | ✓ | Średnia |
| EDI X12 (855) | AS2 / SFTP | Bardzo wysoka | — | Niska |
| EDI EDIFACT | AS2 / SFTP | Bardzo wysoka | — | Niska |
| PUNCHOUT XML | HTTP Webhook | Wysoka | — | Niska |
| JSON API | HTTP Webhook / Poll | Wysoka | — | Niska |
| ERP IDoc | SAP integration | Wysoka | — | Niska |
| MANUAL_FORM | HTTP Upload | Pełna | ✓ | Niska |

## NLP Pipeline — 8 etapów

| Etap | Moduł | Opis |
|------|-------|------|
| `tokenize` | spaCy per-language | Tokeny + PoS + dep-parse (EN/DE/PL/ZH/RU/FR/IT/ES) |
| `sentence_split` | spaCy sentencizer | Granice zdań dla chunked NER |
| `normalize_text` | TextNormalizer | Unicode sub., separatory liczb, Incoterms |
| `ner_extract` | NEREngine | SpacyNER + RegexNER, overlap resolution |
| `price_extract` | PriceExtractor | Detekcja walut, progów ilościowych, outlierów |
| `unit_resolve` | UnitConversionEngine | Normalizacja do SI/EUR, per-100 mapping |
| `material_match` | MaterialMatcher | 50+ aliasów, rodziny materiałów |
| `line_item_group` | LineItemGrouper | Structured first → proximity fallback (200 chars) |

## Ekstrakcja encji (19 kategorii)

| Kategoria | Przykłady |
|-----------|-----------|
| PRICE | `€12.50`, `EUR 1.234,56`, `USD 0.089/pcs` |
| QUANTITY | `500 szt`, `1.000 pcs`, `min 100` |
| UNIT | `kg`, `per 100`, `m`, `m²` |
| PART_NUMBER | `ABC-1234-X`, `7610.0012`, `P/N: XYZ` |
| MATERIAL | `S235JR`, `1.4301`, `Aluminium 6061` |
| LEAD_TIME | `4 weeks`, `6-8 Wochen`, `3 tygodnie` |
| PAYMENT_TERMS | `Net 30`, `30 dni`, `Netto 14` |
| INCOTERM | `DAP Warsaw`, `EXW`, `CIF Hamburg` |
| VALIDITY | `valid until 2025-12-31`, `ważna do` |
| CURRENCY | `EUR`, `USD`, `PLN`, `CNY` |
| MOQ | `MOQ: 500`, `Mindestmenge 100` |
| DISCOUNT | `5% rabat`, `10% discount ≥1000 pcs` |
| TOOLING_COST | `Werkzeugkosten: 2.500 EUR` |
| CERTIFICATION | `ISO 9001`, `RoHS`, `REACH` |
| SUPPLIER_NAME | fuzzy match z bazy SupplierProfile |
| CUSTOMER_PN | mapowanie na BOM line items |
| SURFACE_FINISH | `anodized`, `galvanized`, `powder coat` |
| PACKAGING | `bulk`, `reel`, `box of 50` |
| DELIVERY_ADDRESS | geolocation + Incoterm enrichment |

## Normalizacja cen i jednostek

### Obsługiwane jednostki (33)

| Wymiar | Jednostki |
|--------|-----------|
| MASS | kg, g, t, lb, oz |
| LENGTH | m, cm, mm, in, ft |
| AREA | m², cm², mm², in² |
| VOLUME | m³, l, ml, gal |
| COUNT | pcs, szt, ea, set, pair |
| TIME | day, week, month, hour |
| PACKAGING | per100, per1000, reel, box |

### Obsługiwane waluty (12)

EUR (base), USD, GBP, PLN, CNY, INR, CZK, HUF, TRY, MXN, BRL, CHF

Kurs wymiany: ECB daily XML, cross-rate przez EUR, Redis cache TTL=3600s.

## Mapowanie dostawców (4 strategie)

| Strategia | Sygnał | Precyzja |
|-----------|--------|:--------:|
| Domain match | email domain → `supplier_profiles.domains[]` | ~0.95 |
| VAT regex | NIP/MwSt/VAT → `tax_id` | ~0.98 |
| DUNS | D-U-N-S number | ~1.00 |
| Fuzzy name | SequenceMatcher ≥ 0.80 → `company_name` | ~0.75 |

## Mapowanie na BOM (4 strategie)

| Strategia | Sygnał | Confidence |
|-----------|--------|:----------:|
| Exact supplier PN | `supplier_part_number` match | 1.00 |
| Exact customer PN | `customer_part_number` match | 1.00 |
| Fuzzy PN | SequenceMatcher ≥ 0.80 | 0.75 |
| Semantic embedding | cosine similarity ≥ 0.70 | 0.70 |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `SOP_VIEWER` | GET offers, line items, entities, bom-mappings |
| `SOP_OPERATOR` | SOP_VIEWER + upload, reparse, manual entity edit |
| `SOP_ANALYST` | SOP_VIEWER + analytics, price history, supplier comparison |
| `SOP_PROCUREMENT` | SOP_ANALYST + approve/reject mappings, export RFQ |
| `SOP_ADMIN` | Wszystko + DELETE, queue-stats, admin operations |

## SQL Schema (schemat `sop`)

| Tabela | Opis |
|--------|------|
| `raw_offers` | Surowe wiadomości/pliki z metadanymi kanału |
| `offer_documents` | Dokumenty po konwersji, status przetwarzania |
| `suppliers` | Profile dostawców (domains[], tax_id, duns, kontakty) |
| `offer_line_items` | Pozycje oferty z cenami EUR i jednostkami SI |
| `extracted_entities` | Wszystkie encje NER z confidence i span |
| `bom_mappings` | Mapowania pozycji oferty → BOM line items |
| `fx_rates` | Historyczne kursy wymiany z ECB |
| `outbox_events` | Transactional Outbox dla Kafka |
| `v_offer_summary` | Widok: agregaty per oferta |
| `v_price_history` | Widok: historia cen per part number |

## Event System (9 tematów Kafka)

| Temat | Trigger | Konsumenci |
|-------|---------|------------|
| `sop.offer.received` | Ingestion Gateway | NotificationSvc, AuditLog |
| `sop.offer.parsed` | Status → PARSED | CEE, BOM Engine, RFQ Agent |
| `sop.offer.failed` | Status → FAILED | AlertManager, DLQ |
| `sop.line_item.priced` | Per line item | CEE, PriceDB |
| `sop.bom.mapping.created` | BOMMapper success | BOM Engine, CLS |
| `sop.supplier.identified` | SupplierMapper hit | CRM, SupplierDB |
| `sop.material.matched` | MaterialMatcher hit | CEE, DAE |
| `sop.duplicate.detected` | DuplicateChecker | AuditLog |
| `sop.review.required` | Low quality / anomaly | Human Review Queue |

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| `sop_parse_duration_seconds` p95 | ≤ 5s (email/PDF ≤ 2MB) |
| `sop_price_extraction_f1` | ≥ 0.88 |
| `sop_unit_resolution_accuracy` | ≥ 0.92 |
| `sop_bom_mapping_coverage` | ≥ 80% line items mapped |
| `sop_parse_status_total{status=FAILED}` rate | < 3% |
| `sop_parse_queue_depth` | < 200 (alert at 500) |
| `sop_fx_rate_staleness_hours` | < 24h |

## SLA i KPIs (cel po S32)

| Metryka | Cel |
|---------|-----|
| Price extraction F1 | ≥ 0.88 |
| Unit resolution accuracy | ≥ 0.92 |
| BOM line mapping coverage | ≥ 80% |
| Auto-mapped (no human review) | ≥ 70% |
| Supplier identification rate | ≥ 85% |
| Parse P95 (email/PDF ≤ 2MB) | ≤ 5s |
| Parse P95 (Excel/CSV ≤ 10MB) | ≤ 10s |
| API P95 GET | ≤ 300ms |
| Availability | ≥ 99.5% |
| Throughput (L3) | ≥ 5 000 ofert/day |

## Skalowalność

| Poziom | Wolumen | Infrastruktura |
|--------|---------|----------------|
| L1 | ≤ 100 ofert/day | 1 API pod, 1 worker, CPU NLP |
| L2 | ≤ 1 000 ofert/day | 2–4 API pods, 2–3 NLP workers |
| L3 | ≤ 5 000 ofert/day | HPA 3–10 API + 2–8 NLP workers, Redis queue |
| L4 | > 5 000 ofert/day | Multi-region, streaming EDI, partitioned DB |

HPA: `sop-api` 2–10 pods (CPU 70% + queue depth), `sop-nlp-worker` 1–8 pods.

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `sop`, 9 tabel, GIN index na JSONB + domains[])
- **Cache / Queue:** Redis 7+ (FX rates TTL=3600s, read models, job queue)
- **NLP:** spaCy 3.x (8 modeli językowych) + custom EntityRuler + RegexNER
- **Embeddings:** sentence-transformers (all-mpnet-base-v2) dla semantic BOM matching
- **PDF:** PyMuPDF (fitz) — tekst wektorowy + fallback OCR
- **Excel:** openpyxl (streaming reader dla plików > 10MB)
- **HTML:** BeautifulSoup4 + lxml
- **EDI:** custom X12 855 parser + EDIFACT QUOTES parser
- **FX Rates:** ECB daily XML API (cross-rate via EUR)
- **Messaging:** Apache Kafka 3+ (9 tematów, Avro + Schema Registry, Transactional Outbox)
- **Monitoring:** Prometheus (27 metryk) + Grafana (7 dashboardów) + Alertmanager (8 reguł)
- **Security:** JWT RS256, RBAC 5 ról, AES-256 at-rest, TLS in-transit
- **Storage:** S3-compatible (MinIO / AWS S3), SHA-256 deduplication
- **Kubernetes:** HPA sop-api 2–10 pods, sop-nlp-worker 1–8 pods

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| CEE API | → | Kafka | Priced line items → enriched cost estimate |
| BOM Engine | ↔ | Kafka | BOM line item lookup + mapping confirmation |
| DAE | ← | Kafka | Material spec z rysunków → enriches offer context |
| RFQ Agent | ← | Kafka | Zapytania ofertowe → trigger ingestion |
| CLS | → | Kafka | Price history → cost model training |
| ECB | ← | REST/XML | Daily FX rates |
| AWS S3 / MinIO | ↔ | S3 API | Offer file storage |
| Email servers | ← | IMAP / Gmail API / O365 | Offer email ingestion |
| EDI partners | ↔ | AS2 / SFTP | X12 855, EDIFACT QUOTES |
| Grafana / Prometheus | ← | Pull | Metrics scraping |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S8 | DB, Email+PDF ingestion, RegexNER, basic price extraction, Kafka, monitoring |
| NLP + Mapping | S9–S16 | spaCy multilingual, SupplierMapper, BOMMapper fuzzy, FXRateService, Excel/EDI |
| Intelligence | S17–S24 | Embedding BOM matching, active learning, price anomaly detection, PUNCHOUT XML |
| Production Scale | S25–S32 | HPA, multi-tenant S3, L4 streaming, SLA hardening, DR |
