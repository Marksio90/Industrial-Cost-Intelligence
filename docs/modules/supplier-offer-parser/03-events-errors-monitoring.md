# Supplier Offer Parser — Sekcje 9–11

## 9. Event System

### 9.1 Kafka topics

```
sop.offer.received          → OfferReceived (raw ingest, before parsing)
sop.offer.parsed            → OfferParsed (pipeline complete: PARSED / FAILED)
sop.offer.mapped            → OfferMapped (BOM mapping complete)
sop.offer.needs_review      → OfferNeedsReview (low confidence, manual check)
sop.offer.rejected          → OfferRejected (unrecoverable error or duplicate)
sop.line_item.priced        → LineItemPriced (unit_price_eur available, for CEE/CLS)
sop.supplier.identified     → SupplierIdentified (supplier auto-detected from domain)
sop.bom_mapping.created     → BOMMappingCreated (new supplier↔BOM link learned)
sop.fx_rates.updated        → FXRatesUpdated (daily ECB refresh)
```

### 9.2 Avro schemas

```json
{
  "name": "OfferParsed",
  "namespace": "io.industrial_cost.sop",
  "type": "record",
  "doc": "Emitted when NLP pipeline completes for a supplier offer document.",
  "fields": [
    {"name": "event_id",            "type": "string"},
    {"name": "document_id",         "type": "string"},
    {"name": "raw_offer_id",        "type": "string"},
    {"name": "rfq_ref",             "type": ["null", "string"], "default": null},
    {"name": "rfq_id",              "type": ["null", "string"], "default": null},
    {"name": "supplier_id",         "type": ["null", "string"], "default": null},
    {"name": "supplier_name",       "type": ["null", "string"], "default": null},
    {"name": "format",              "type": "string"},
    {"name": "language",            "type": "string"},
    {"name": "status",              "type": {"type": "enum", "name": "OfferStatus",
      "symbols": ["PARSED", "FAILED", "PARTIAL"]}},
    {"name": "line_item_count",     "type": "int"},
    {"name": "total_value_eur",     "type": ["null", "double"], "default": null},
    {"name": "overall_confidence",  "type": ["null", "double"], "default": null},
    {"name": "currency",            "type": ["null", "string"], "default": null},
    {"name": "parsing_duration_ms", "type": ["null", "int"],    "default": null},
    {"name": "warnings",            "type": {"type": "array", "items": "string"}},
    {"name": "parsed_at",           "type": "string"}
  ]
}
```

```json
{
  "name": "LineItemPriced",
  "namespace": "io.industrial_cost.sop",
  "type": "record",
  "doc": "Emitted for each offer line item where unit_price_eur is available. Used by CEE and CLS.",
  "fields": [
    {"name": "event_id",                "type": "string"},
    {"name": "line_id",                 "type": "string"},
    {"name": "document_id",             "type": "string"},
    {"name": "rfq_id",                  "type": ["null", "string"], "default": null},
    {"name": "bom_line_id",             "type": ["null", "string"], "default": null},
    {"name": "bom_item_code",           "type": ["null", "string"], "default": null},
    {"name": "supplier_id",             "type": ["null", "string"], "default": null},
    {"name": "supplier_country",        "type": ["null", "string"], "default": null},
    {"name": "part_number_supplier",    "type": ["null", "string"], "default": null},
    {"name": "part_number_customer",    "type": ["null", "string"], "default": null},
    {"name": "unit_price_eur",          "type": "double"},
    {"name": "currency_raw",            "type": "string"},
    {"name": "uom_normalized",          "type": "string"},
    {"name": "quantity",                "type": ["null", "double"], "default": null},
    {"name": "lead_time_days",          "type": ["null", "int"],    "default": null},
    {"name": "moq",                     "type": ["null", "double"], "default": null},
    {"name": "tooling_cost_eur",        "type": ["null", "double"], "default": null},
    {"name": "discount_pct",            "type": ["null", "double"], "default": null},
    {"name": "incoterm",                "type": ["null", "string"], "default": null},
    {"name": "material_designation",    "type": ["null", "string"], "default": null},
    {"name": "match_method",            "type": "string"},
    {"name": "match_confidence",        "type": ["null", "double"], "default": null},
    {"name": "offer_date",              "type": "string"}
  ]
}
```

```json
{
  "name": "OfferReceived",
  "namespace": "io.industrial_cost.sop",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "offer_id",        "type": "string"},
    {"name": "channel",         "type": "string"},
    {"name": "format",          "type": "string"},
    {"name": "sender_email",    "type": ["null", "string"], "default": null},
    {"name": "sender_domain",   "type": ["null", "string"], "default": null},
    {"name": "rfq_ref",         "type": ["null", "string"], "default": null},
    {"name": "file_size_bytes", "type": "long"},
    {"name": "received_at",     "type": "string"}
  ]
}
```

```json
{
  "name": "BOMMappingCreated",
  "namespace": "io.industrial_cost.sop",
  "type": "record",
  "doc": "Emitted when a new supplier↔BOM part number mapping is learned or confirmed.",
  "fields": [
    {"name": "event_id",                "type": "string"},
    {"name": "mapping_id",              "type": "string"},
    {"name": "supplier_id",             "type": "string"},
    {"name": "supplier_part_number",    "type": "string"},
    {"name": "customer_part_number",    "type": "string"},
    {"name": "bom_line_id",             "type": ["null", "string"], "default": null},
    {"name": "match_method",            "type": "string"},
    {"name": "confidence",              "type": "double"},
    {"name": "created_at",              "type": "string"}
  ]
}
```

### 9.3 Outbox Publisher

```python
import json
import asyncio
import structlog
from confluent_kafka import Producer

log = structlog.get_logger()


class SOPOutboxPublisher:
    """
    Polls sop.outbox_events every 500ms, publishes to Kafka.
    Transactional outbox — at-least-once delivery guarantee.
    """
    POLL_INTERVAL_MS = 500
    BATCH_SIZE       = 200

    def __init__(self, db_pool, kafka_config: dict):
        self.db_pool = db_pool
        self.producer = Producer(kafka_config)

    async def run(self):
        while True:
            try:
                await self._publish_batch()
            except Exception as e:
                log.error("sop_outbox_error", error=str(e))
            await asyncio.sleep(self.POLL_INTERVAL_MS / 1000)

    async def _publish_batch(self):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, topic, key, payload, headers
                FROM sop.outbox_events
                WHERE published_at IS NULL
                ORDER BY created_at
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                self.BATCH_SIZE,
            )
            if not rows:
                return
            for row in rows:
                try:
                    self.producer.produce(
                        topic=row["topic"],
                        key=row["key"].encode(),
                        value=json.dumps(dict(row["payload"])).encode(),
                        headers=dict(row["headers"]) if row["headers"] else {},
                    )
                except Exception as e:
                    log.warning("kafka_produce_error", event_id=str(row["event_id"]), error=str(e))
                    await conn.execute(
                        "UPDATE sop.outbox_events SET retry_count = retry_count + 1 WHERE event_id = $1",
                        row["event_id"],
                    )
                    continue
                await conn.execute(
                    "UPDATE sop.outbox_events SET published_at = now() WHERE event_id = $1",
                    row["event_id"],
                )
            self.producer.flush(timeout=5.0)
```

### 9.4 Event consumers (inbound)

| System | Topic consumed | Action |
|--------|---------------|--------|
| RFQ Agent | `rfqa.rfq.issued` | Registers RFQ reference — used to match incoming offers |
| BOME | `bome.bom.released` | Updates BOM line cache for mapping |
| DAE | `dae.drawing.material_ready` | Enriches material matching knowledge base |
| CLS | `sop.line_item.priced` | Uses actual supplier prices for cost model retraining |
| CEE API | `sop.line_item.priced` | Updates price benchmarks for cost estimates |

---

## 10. Error Handling

### 10.1 Error taxonomy

```python
class SOPError(Exception):
    http_status: int = 500
    error_code: str  = "SOP_INTERNAL_ERROR"


class UnsupportedFormatError(SOPError):
    http_status = 400
    error_code  = "SOP_UNSUPPORTED_FORMAT"


class DuplicateOfferError(SOPError):
    http_status = 409
    error_code  = "SOP_DUPLICATE_OFFER"
    def __init__(self, existing_id: str):
        super().__init__(f"Duplicate offer — existing document_id: {existing_id}")
        self.existing_id = existing_id


class CorruptedDocumentError(SOPError):
    http_status = 422
    error_code  = "SOP_CORRUPTED_DOCUMENT"


class NoPricesFoundError(SOPError):
    """Document parsed but no price candidates extracted."""
    http_status = 422
    error_code  = "SOP_NO_PRICES_FOUND"


class LowExtractionQualityError(SOPError):
    """Extraction quality below minimum threshold."""
    http_status = 422
    error_code  = "SOP_LOW_EXTRACTION_QUALITY"
    MIN_QUALITY = 0.30


class SupplierNotFoundError(SOPError):
    """Cannot identify supplier from document."""
    http_status = 200   # non-fatal — offer still stored, supplier = NULL
    error_code  = "SOP_SUPPLIER_NOT_FOUND"


class FXRateUnavailableError(SOPError):
    """Cannot convert price to EUR — no rate available."""
    http_status = 200   # non-fatal — price stored in original currency
    error_code  = "SOP_FX_RATE_UNAVAILABLE"


class BOMMapNotFoundError(SOPError):
    """Line item cannot be matched to any BOM line."""
    http_status = 200   # non-fatal — item marked UNMATCHED
    error_code  = "SOP_BOM_MATCH_NOT_FOUND"


class UnitNotRecognizedError(SOPError):
    """Unit string cannot be resolved to canonical form."""
    http_status = 200
    error_code  = "SOP_UNIT_NOT_RECOGNIZED"


class ParseTimeoutError(SOPError):
    http_status = 504
    error_code  = "SOP_PARSE_TIMEOUT"


class RecoverableNLPError(SOPError):
    """One NLP stage failed but pipeline can continue."""
    http_status = 200
    error_code  = "SOP_NLP_STAGE_DEGRADED"


class EDIParseError(SOPError):
    http_status = 422
    error_code  = "SOP_EDI_PARSE_ERROR"
```

### 10.2 Recovery strategies

```python
import asyncio
from functools import wraps
import structlog

log = structlog.get_logger()


def with_timeout(seconds: float):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                raise ParseTimeoutError(f"{fn.__name__} exceeded {seconds}s")
        return wrapper
    return decorator


def with_retry(max_attempts: int = 3, backoff: float = 2.0, on: tuple = (Exception,)):
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except on as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = backoff ** attempt
                        await asyncio.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


class OfferParseErrorHandler:
    """
    Decides pipeline abort vs. continue-degraded for each error type.
    Non-fatal errors produce warnings; fatal errors abort and mark offer FAILED.
    """

    ABORT_ERRORS      = (CorruptedDocumentError, ParseTimeoutError, UnsupportedFormatError)
    DEGRADED_CONTINUE = (RecoverableNLPError, UnitNotRecognizedError, FXRateUnavailableError,
                         BOMMapNotFoundError, SupplierNotFoundError)
    NEEDS_REVIEW      = (NoPricesFoundError, LowExtractionQualityError)

    async def handle(self, error: Exception, stage: str, ctx: "NLPContext") -> str:
        """Returns: 'abort' / 'continue' / 'needs_review'."""
        if isinstance(error, self.ABORT_ERRORS):
            ctx.warnings.append(f"[{stage}] FATAL: {error}")
            log.error("parse_fatal", stage=stage, error=str(error))
            return "abort"
        if isinstance(error, self.NEEDS_REVIEW):
            ctx.warnings.append(f"[{stage}] NEEDS_REVIEW: {error}")
            log.warning("parse_review", stage=stage, error=str(error))
            return "needs_review"
        if isinstance(error, self.DEGRADED_CONTINUE):
            ctx.warnings.append(f"[{stage}] DEGRADED: {error}")
            log.warning("parse_degraded", stage=stage, error=str(error))
            return "continue"
        # Unknown
        ctx.warnings.append(f"[{stage}] UNEXPECTED: {error}")
        log.error("parse_unexpected", stage=stage, error=str(error), exc_info=True)
        return "continue"


class DuplicateChecker:
    """
    Checks SHA-256 checksum before storing raw offer.
    Prevents reprocessing identical files.
    """
    def __init__(self, db_pool):
        self.db_pool = db_pool

    async def check(self, checksum: str) -> Optional[str]:
        """Returns existing document_id if duplicate, else None."""
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT d.document_id
                FROM sop.offer_documents d
                JOIN sop.raw_offers r ON r.offer_id = d.raw_offer_id
                WHERE r.checksum_sha256 = $1
                LIMIT 1
                """,
                checksum,
            )
            return str(row["document_id"]) if row else None


class PriceQualityGuard:
    """
    Post-parse quality checks on extracted prices.
    Flags offers with suspicious prices for manual review.
    """
    MIN_PRICE_EUR     = Decimal("0.0001")
    MAX_PRICE_EUR     = Decimal("1_000_000")
    MAX_PRICE_SPREAD  = 100.0    # max ratio between highest and lowest unit price

    def validate(self, line_items: list[OfferLineItem]) -> list[str]:
        warnings = []
        unit_prices = [li.unit_price_eur for li in line_items if li.unit_price_eur]
        if not unit_prices:
            warnings.append("No unit prices extracted — needs manual review")
            return warnings

        for li in line_items:
            if li.unit_price_eur is not None:
                price = Decimal(str(li.unit_price_eur))
                if price < self.MIN_PRICE_EUR:
                    warnings.append(
                        f"Line {li.position}: price {price} EUR suspiciously low"
                    )
                    li.needs_review = True
                if price > self.MAX_PRICE_EUR:
                    warnings.append(
                        f"Line {li.position}: price {price} EUR suspiciously high"
                    )
                    li.needs_review = True

        if len(unit_prices) >= 2:
            lo = min(float(p) for p in unit_prices)
            hi = max(float(p) for p in unit_prices)
            if lo > 0 and hi / lo > self.MAX_PRICE_SPREAD:
                warnings.append(
                    f"Large price spread: {lo:.4f}–{hi:.4f} EUR (ratio {hi/lo:.1f}x)"
                    " — check for per-100 vs per-1 pricing mix"
                )
        return warnings


class ValidationEngine:
    """Post-parse offer-level validation."""

    def validate(self, ctx: NLPContext, doc: OfferDocument) -> tuple[bool, list[str]]:
        issues = []
        line_items = ctx.line_items

        if not line_items:
            issues.append("V001: No line items extracted")
        else:
            unit_items = [li for li in line_items if li.unit_price_eur is not None]
            if not unit_items:
                issues.append("V002: No items with EUR unit price")

        if doc.currency_hint and doc.currency_hint not in ("EUR","USD","GBP","PLN","CNY"):
            issues.append(f"V003: Unusual currency {doc.currency_hint} — verify FX conversion")

        unmapped = sum(1 for li in line_items if li.bom_line_id is None)
        if line_items and unmapped / len(line_items) > 0.50:
            issues.append(f"V004: {unmapped}/{len(line_items)} items unmapped to BOM")

        low_conf = [li for li in line_items if li.confidence < 0.60]
        if low_conf:
            issues.append(f"V005: {len(low_conf)} items with confidence < 0.60")

        is_valid = not any("V001" in i or "V002" in i for i in issues)
        return is_valid, issues
```

### 10.3 FastAPI exception handlers

```python
import uuid
from fastapi import Request
from fastapi.responses import JSONResponse


async def sop_exception_handler(request: Request, exc: SOPError) -> JSONResponse:
    request_id = str(uuid.uuid4())
    log.error("sop_api_error", error_code=exc.error_code,
              detail=str(exc), path=str(request.url), request_id=request_id)
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.error_code, "detail": str(exc), "request_id": request_id},
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    log.error("sop_unhandled", path=str(request.url), request_id=request_id, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "detail": "Unexpected error", "request_id": request_id},
    )
```

---

## 11. Monitoring

### 11.1 Prometheus metrics

```python
from prometheus_client import Counter, Histogram, Gauge, Summary


# ── Ingestion ─────────────────────────────────────────────────────────────────
sop_offers_received_total = Counter(
    "sop_offers_received_total",
    "Total offers received",
    ["channel", "format"],
)

sop_offers_duplicate_total = Counter(
    "sop_offers_duplicate_total",
    "Duplicate offers rejected",
    ["channel"],
)

sop_ingestion_file_size_bytes = Histogram(
    "sop_ingestion_file_size_bytes",
    "Distribution of ingested offer file sizes",
    ["format"],
    buckets=[10_000, 50_000, 200_000, 1_000_000, 5_000_000, 20_000_000, 50_000_000],
)

# ── Parsing Pipeline ──────────────────────────────────────────────────────────
sop_parse_duration_seconds = Histogram(
    "sop_parse_duration_seconds",
    "End-to-end offer parse pipeline duration",
    ["format", "status"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

sop_stage_duration_seconds = Histogram(
    "sop_stage_duration_seconds",
    "Duration of individual NLP pipeline stages",
    ["stage"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 15.0],
)

sop_parse_status_total = Counter(
    "sop_parse_status_total",
    "Offer parse completions by outcome",
    ["status", "format"],
)

sop_parse_queue_depth = Gauge(
    "sop_parse_queue_depth",
    "Current number of offers pending parsing",
)

sop_active_workers = Gauge(
    "sop_active_workers",
    "Number of active parse workers",
)

# ── NLP Quality ───────────────────────────────────────────────────────────────
sop_overall_confidence = Histogram(
    "sop_overall_confidence",
    "Overall parse confidence per offer",
    ["format", "language"],
    buckets=[0.3, 0.4, 0.5, 0.6, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

sop_line_items_extracted_total = Counter(
    "sop_line_items_extracted_total",
    "Total line items extracted",
    ["source", "format"],   # source: STRUCTURED / TEXT_NLP / EDI
)

sop_line_items_priced_total = Counter(
    "sop_line_items_priced_total",
    "Line items with EUR unit price resolved",
)

sop_line_items_unmapped_total = Counter(
    "sop_line_items_unmapped_total",
    "Line items that could not be mapped to BOM",
    ["match_reason"],
)

# ── Entity Extraction ─────────────────────────────────────────────────────────
sop_entities_extracted_total = Counter(
    "sop_entities_extracted_total",
    "Total entities extracted by type and source",
    ["entity_type", "source"],
)

sop_price_candidates_per_offer = Histogram(
    "sop_price_candidates_per_offer",
    "Price candidates extracted per offer",
    buckets=[0, 1, 2, 5, 10, 20, 50, 100, 200],
)

sop_outlier_prices_total = Counter(
    "sop_outlier_prices_total",
    "Price candidates flagged as outliers",
    ["reason"],
)

# ── Supplier Mapping ──────────────────────────────────────────────────────────
sop_supplier_match_total = Counter(
    "sop_supplier_match_total",
    "Supplier identification results",
    ["method", "result"],   # method: DOMAIN / VAT / DUNS / FUZZY; result: FOUND / NOT_FOUND
)

sop_bom_match_total = Counter(
    "sop_bom_match_total",
    "BOM line item matching results",
    ["method"],   # EXACT / FUZZY / AI / UNMATCHED
)

sop_bom_match_confidence = Histogram(
    "sop_bom_match_confidence",
    "BOM mapping match confidence",
    ["method"],
    buckets=[0.5, 0.6, 0.7, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

# ── FX Rates ─────────────────────────────────────────────────────────────────
sop_fx_rate_age_seconds = Gauge(
    "sop_fx_rate_age_seconds",
    "Age of the current FX rates in seconds",
)

sop_fx_conversion_total = Counter(
    "sop_fx_conversion_total",
    "Currency conversions performed",
    ["from_currency", "to_currency"],
)

sop_fx_missing_total = Counter(
    "sop_fx_missing_total",
    "Prices that could not be converted (missing FX rate)",
    ["currency"],
)

# ── Email Ingestion ───────────────────────────────────────────────────────────
sop_email_poll_duration_seconds = Histogram(
    "sop_email_poll_duration_seconds",
    "IMAP mailbox poll duration",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

sop_email_attachments_total = Counter(
    "sop_email_attachments_total",
    "Email attachments ingested",
    ["format"],
)

# ── Errors ───────────────────────────────────────────────────────────────────
sop_parse_errors_total = Counter(
    "sop_parse_errors_total",
    "Parse errors by error code",
    ["error_code", "format"],
)

sop_validation_failures_total = Counter(
    "sop_validation_failures_total",
    "Offer validation failures by rule",
    ["rule"],
)

sop_needs_review_total = Counter(
    "sop_needs_review_total",
    "Offers flagged for manual review",
    ["reason"],
)
```

### 11.2 Grafana dashboards (7 dashboards)

| Dashboard | Panele | Cel |
|-----------|--------|-----|
| **SOP Overview** | Offers received/hr, parse queue, status donut, P95 duration | Operacyjny |
| **NLP Quality** | Confidence by format/language, entity extraction rate, price candidates/offer | Jakość NLP |
| **Price Extraction** | Price types distribution, outlier rate, EUR conversion success, price spread heatmap | Ceny |
| **Supplier Mapping** | Match rate by method, unmapped ratio, top unidentified domains, BOM mapping coverage | Mapowanie |
| **BOM Mapping** | Match confidence histogram, method breakdown, UNMATCHED trend, top unmatched items | BOM |
| **FX Rates** | Rate age, missing conversions by currency, conversion volume heatmap | Waluty |
| **Errors & Review** | Error rate by code, needs-review queue depth, validation failures, SLA breach | Błędy |

### 11.3 Alertmanager rules

```yaml
groups:
  - name: sop.critical
    rules:
      - alert: SOPParseQueueDepthHigh
        expr: sop_parse_queue_depth > 300
        for: 5m
        labels:
          severity: critical
          team: procurement
        annotations:
          summary: "SOP parse queue > 300 offers pending"
          description: "{{ $value }} offers queued. Check worker scaling."

      - alert: SOPParseFailureRateHigh
        expr: |
          rate(sop_parse_status_total{status="FAILED"}[10m])
          / rate(sop_parse_status_total[10m]) > 0.20
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "SOP parse failure rate > 20% ({{ $value | humanizePercentage }})"

      - alert: SOPBOMMappingRateLow
        expr: |
          rate(sop_bom_match_total{method="UNMATCHED"}[1h])
          / rate(sop_line_items_extracted_total[1h]) > 0.40
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "SOP: > 40% of line items cannot be mapped to BOM"

      - alert: SOPFXRatesStale
        expr: sop_fx_rate_age_seconds > 86400
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "SOP: FX rates are > 24h old — refresh failed?"

      - alert: SOPNeedsReviewQueueHigh
        expr: |
          increase(sop_needs_review_total[1h]) > 50
        for: 0m
        labels:
          severity: warning
        annotations:
          summary: "SOP: > 50 offers needing manual review in last hour"

      - alert: SOPWorkersDown
        expr: sop_active_workers == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "SOP: No active parse workers!"

      - alert: SOPEmailIngestorDown
        expr: |
          increase(sop_email_poll_duration_seconds_count[10m]) == 0
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "SOP: Email ingestor has not polled for 10 minutes"

      - alert: SOPConfidenceLow
        expr: |
          histogram_quantile(0.50, rate(sop_overall_confidence_bucket[1h])) < 0.60
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "SOP: Median parse confidence < 0.60 — check NLP model health"
```

### 11.4 Structured logging

```python
import structlog

log = structlog.get_logger()


class OfferParseLogger:
    """Structured per-offer logging throughout NLP pipeline."""

    def __init__(self, document_id: str, offer_id: str, format_: str):
        self.log = log.bind(document_id=document_id, raw_offer_id=offer_id, format=format_)

    def stage_start(self, stage: str):
        self.log.info("nlp_stage_start", stage=stage)

    def stage_done(self, stage: str, duration_ms: float, **kw):
        self.log.info("nlp_stage_done", stage=stage, duration_ms=round(duration_ms, 1), **kw)

    def stage_failed(self, stage: str, error: str, duration_ms: float):
        self.log.warning("nlp_stage_failed", stage=stage, error=error, duration_ms=round(duration_ms, 1))

    def parse_done(self, ctx: NLPContext):
        self.log.info(
            "offer_parsed",
            line_items=len(ctx.line_items),
            entities=len(ctx.entities),
            prices=len(ctx.price_candidates),
            warnings=len(ctx.warnings),
            supplier_id=ctx.supplier_id,
            stage_timings={k: round(v, 1) for k, v in ctx.stage_timings.items()},
        )

    def bom_mapping_done(self, mapped: int, unmapped: int, total: int):
        self.log.info(
            "bom_mapping_done",
            mapped=mapped, unmapped=unmapped, total=total,
            coverage_pct=round(100 * mapped / max(total, 1), 1),
        )
```
