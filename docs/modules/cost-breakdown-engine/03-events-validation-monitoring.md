# Cost Breakdown Engine — Sections 6–8

## 6. Event System

### 6.1 Topologie Kafka (8 tematów)

| Temat | Trigger | Producent | Konsumenci |
|-------|---------|-----------|------------|
| `cbe.breakdown.requested` | POST /breakdowns | API Gateway | CBE Worker |
| `cbe.breakdown.calculated` | Status → CALCULATED | CBE Worker | CEE, Notification |
| `cbe.breakdown.approved` | Status → APPROVED | DB Trigger | CEE, BOM Engine, RFQ |
| `cbe.breakdown.rejected` | Status → REVIEWED (rejected) | API | Notification, Audit |
| `cbe.breakdown.superseded` | Nowa kalkulacja zastępuje | CBE Worker | Audit |
| `cbe.rates.updated` | PUT /rates/locations | API | CBE Worker (invalidate cache) |
| `cbe.quantity_break.ready` | Tabela progów wygenerowana | CBE Worker | UI, CEE |
| `cbe.anomaly.detected` | ValidationEngine alert | CBE Worker | Alert, Human Review |

### 6.2 Avro schemas

```json
// cbe.breakdown.approved — v1
{
  "namespace": "com.industrial_cost_intelligence.cbe",
  "type": "record",
  "name": "BreakdownApproved",
  "fields": [
    {"name": "breakdown_id",     "type": "string"},
    {"name": "part_id",          "type": "string"},
    {"name": "bom_line_id",      "type": ["null", "string"], "default": null},
    {"name": "quantity",         "type": {"type": "bytes", "logicalType": "decimal",
                                          "precision": 14, "scale": 4}},
    {"name": "location_code",    "type": "string"},
    {"name": "overhead_profile", "type": "string"},
    {"name": "unit_cost_eur",    "type": {"type": "bytes", "logicalType": "decimal",
                                          "precision": 16, "scale": 6}},
    {"name": "total_cost_eur",   "type": {"type": "bytes", "logicalType": "decimal",
                                          "precision": 16, "scale": 4}},
    {"name": "material_pct",     "type": "float"},
    {"name": "labor_pct",        "type": "float"},
    {"name": "machine_pct",      "type": "float"},
    {"name": "energy_pct",       "type": "float"},
    {"name": "tooling_pct",      "type": "float"},
    {"name": "overhead_pct",     "type": "float"},
    {"name": "confidence_band",  "type": {"type": "enum",
                                          "name": "ConfidenceBand",
                                          "symbols": ["HIGH","MEDIUM","LOW","INDICATIVE"]}},
    {"name": "approved_by",      "type": "string"},
    {"name": "approved_at",      "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

```json
// cbe.breakdown.calculated — v1
{
  "namespace": "com.industrial_cost_intelligence.cbe",
  "type": "record",
  "name": "BreakdownCalculated",
  "fields": [
    {"name": "breakdown_id",     "type": "string"},
    {"name": "part_id",          "type": "string"},
    {"name": "unit_cost_eur",    "type": "double"},
    {"name": "total_cost_eur",   "type": "double"},
    {"name": "overall_confidence","type": "float"},
    {"name": "confidence_band",  "type": "string"},
    {"name": "warnings_count",   "type": "int"},
    {"name": "calculated_at",    "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

```json
// cbe.rates.updated — v1
{
  "namespace": "com.industrial_cost_intelligence.cbe",
  "type": "record",
  "name": "RatesUpdated",
  "fields": [
    {"name": "rate_id",          "type": "string"},
    {"name": "location_code",    "type": "string"},
    {"name": "overhead_profile", "type": "string"},
    {"name": "valid_from",       "type": "string"},  // ISO date
    {"name": "updated_by",       "type": "string"},
    {"name": "updated_at",       "type": {"type": "long", "logicalType": "timestamp-millis"}}
  ]
}
```

### 6.3 CBEOutboxPublisher

```python
import asyncio
from uuid import UUID

class CBEOutboxPublisher:
    """Transactional Outbox — publikuje zdarzenia z tabeli cbe.outbox_events."""

    POLL_INTERVAL_S = 0.5
    BATCH_SIZE      = 50

    def __init__(self, db: "AsyncpgPool", kafka: "AIOKafkaProducer",
                 registry: "ConfluentSchemaRegistry"):
        self._db       = db
        self._kafka    = kafka
        self._registry = registry

    async def run(self) -> None:
        while True:
            published = await self._publish_batch()
            if published < self.BATCH_SIZE:
                await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _publish_batch(self) -> int:
        async with self._db.transaction() as conn:
            rows = await conn.fetch("""
                SELECT event_id, topic, key, payload
                  FROM cbe.outbox_events
                 WHERE published = FALSE
                 ORDER BY created_at
                 LIMIT $1
                   FOR UPDATE SKIP LOCKED
            """, self.BATCH_SIZE)

            for row in rows:
                schema = await self._registry.get_schema(row["topic"])
                encoded = schema.encode(row["payload"])
                await self._kafka.send(
                    row["topic"],
                    key=row["key"].encode(),
                    value=encoded,
                )
                await conn.execute(
                    "UPDATE cbe.outbox_events SET published = TRUE WHERE event_id = $1",
                    row["event_id"]
                )
            return len(rows)
```

### 6.4 Integracja z CEE (Cost Estimation Engine)

Po zatwierdzeniu kalkulacji (`cbe.breakdown.approved`) CEE konsumuje event i
wzbogaca swój model kosztu:

```
BreakdownApproved
    │
    ▼
CEE Consumer
    ├── Update part_cost_estimate (unit_cost_eur, confidence_band)
    ├── Trigger BOM cost rollup (bottom-up, jeśli bom_line_id present)
    └── Publish cee.cost_estimate.updated → UI / RFQ Agent
```

---

## 7. Validation Rules

### 7.1 Hierarchia błędów walidacji

```python
class CBEValidationError(Exception):
    """Bazowy wyjątek walidacji CBE."""
    code: str
    severity: str  # "ERROR" | "WARNING" | "INFO"

class NegativeCostError(CBEValidationError):
    code = "V001"; severity = "ERROR"

class ZeroCostWarning(CBEValidationError):
    code = "V002"; severity = "WARNING"

class ConfidenceTooLowError(CBEValidationError):
    code = "V003"; severity = "ERROR"   # blokuje APPROVE

class ImbalancedSharesError(CBEValidationError):
    code = "V004"; severity = "WARNING"

class UnitCostOutOfRangeError(CBEValidationError):
    code = "V005"; severity = "WARNING"

class MissingMaterialDataError(CBEValidationError):
    code = "V006"; severity = "WARNING"

class StaleRateDataError(CBEValidationError):
    code = "V007"; severity = "WARNING"

class MaterialCostAnomalyError(CBEValidationError):
    code = "V008"; severity = "WARNING"

class OverheadTooHighError(CBEValidationError):
    code = "V009"; severity = "WARNING"
```

### 7.2 ValidationEngine

```python
from decimal import Decimal

class ValidationEngine:
    """Waliduje CostBreakdownResult przed zapisem i przed APPROVE."""

    MIN_UNIT_COST_EUR  = Decimal("0.001")
    MAX_UNIT_COST_EUR  = Decimal("1_000_000")
    MIN_CONFIDENCE_APPROVE = 0.50     # poniżej → blokada APPROVE
    MAX_OVERHEAD_SHARE     = 0.60     # overhead > 60% → ostrzeżenie
    MAX_STALE_RATE_DAYS    = 90       # stawki starsze niż 90 dni → ostrzeżenie

    def validate(
        self,
        result: "CostBreakdownResult",
        rate_date: "date | None" = None,
    ) -> list[CBEValidationError]:
        issues: list[CBEValidationError] = []
        issues += self._check_negatives(result)
        issues += self._check_zero_components(result)
        issues += self._check_confidence(result)
        issues += self._check_unit_cost_range(result)
        issues += self._check_overhead_share(result)
        issues += self._check_material_anomaly(result)
        if rate_date:
            issues += self._check_stale_rates(rate_date)
        return issues

    def validate_for_approve(
        self, result: "CostBreakdownResult"
    ) -> list[CBEValidationError]:
        """Walidacja blokująca APPROVE — tylko ERROR-level."""
        issues = self.validate(result)
        return [i for i in issues if i.severity == "ERROR"]

    # ── Reguły szczegółowe ──────────────────────────────────

    def _check_negatives(self, r) -> list[CBEValidationError]:
        errors = []
        for comp in r.components:
            if comp.amount_eur < 0:
                errors.append(NegativeCostError(
                    f"[V001] Negative cost in {comp.component_type}: {comp.amount_eur} EUR"))
        return errors

    def _check_zero_components(self, r) -> list[CBEValidationError]:
        warnings = []
        for cat, amount in [
            ("MATERIAL", r.material_eur),
            ("LABOR",    r.labor_eur),
            ("MACHINE",  r.machine_eur),
        ]:
            if amount == 0:
                warnings.append(ZeroCostWarning(
                    f"[V002] {cat} cost is zero — check input data."))
        return warnings

    def _check_confidence(self, r) -> list[CBEValidationError]:
        if r.overall_confidence < self.MIN_CONFIDENCE_APPROVE:
            return [ConfidenceTooLowError(
                f"[V003] overall_confidence={r.overall_confidence:.3f} "
                f"< {self.MIN_CONFIDENCE_APPROVE} — APPROVE blocked.")]
        return []

    def _check_unit_cost_range(self, r) -> list[CBEValidationError]:
        if not (self.MIN_UNIT_COST_EUR <= r.unit_cost_eur <= self.MAX_UNIT_COST_EUR):
            return [UnitCostOutOfRangeError(
                f"[V005] unit_cost_eur={r.unit_cost_eur} out of range "
                f"[{self.MIN_UNIT_COST_EUR}, {self.MAX_UNIT_COST_EUR}]")]
        return []

    def _check_overhead_share(self, r) -> list[CBEValidationError]:
        share = float(r.overhead_eur) / (float(r.total_cost_eur) or 1)
        if share > self.MAX_OVERHEAD_SHARE:
            return [OverheadTooHighError(
                f"[V009] overhead share {share:.1%} > {self.MAX_OVERHEAD_SHARE:.0%}")]
        return []

    def _check_material_anomaly(self, r) -> list[CBEValidationError]:
        # Materiał > 85% kosztów pierwotnych (bez overhead) — anomalia
        primary = r.material_eur + r.labor_eur + r.machine_eur + r.energy_eur + r.tooling_eur
        if primary > 0 and float(r.material_eur / primary) > 0.85:
            return [MaterialCostAnomalyError(
                f"[V008] material share of primary cost "
                f"{float(r.material_eur / primary):.1%} > 85% — verify gross weight.")]
        return []

    def _check_stale_rates(self, rate_date: "date") -> list[CBEValidationError]:
        from datetime import date
        age = (date.today() - rate_date).days
        if age > self.MAX_STALE_RATE_DAYS:
            return [StaleRateDataError(
                f"[V007] Rate data is {age} days old (>{self.MAX_STALE_RATE_DAYS}d). "
                "Update location_rates before approving.")]
        return []
```

### 7.3 Tabela reguł walidacji

| Kod | Reguła | Warunek | Severity | Blokuje APPROVE |
|-----|--------|---------|----------|:---------------:|
| V001 | Ujemny składnik kosztów | `component.amount_eur < 0` | ERROR | Tak |
| V002 | Zerowy koszt kategorii | `material_eur = 0 OR labor_eur = 0` | WARNING | Nie |
| V003 | Zbyt niskie confidence | `overall_confidence < 0.50` | ERROR | Tak |
| V004 | Suma udziałów ≠ 100% | `|Σ pct - 100| > 0.1` | WARNING | Nie |
| V005 | Unit cost poza zakresem | `unit_cost < 0.001 OR > 1 000 000 EUR` | WARNING | Nie |
| V006 | Brak danych materiałowych | `material is None AND material_eur = 0` | WARNING | Nie |
| V007 | Przestarzałe stawki | `rate_date > 90 dni` | WARNING | Nie |
| V008 | Anomalia udziału materiału | `material_pct_of_primary > 85%` | WARNING | Nie |
| V009 | Overhead > 60% całości | `overhead_share > 0.60` | WARNING | Nie |

### 7.4 Pre-approve checklist (API)

```python
@router.get("/breakdowns/{breakdown_id}/pre-approve-check",
            response_model=PreApproveCheckResponse)
async def pre_approve_check(
    breakdown_id: UUID,
    db: AsyncpgPool = Depends(get_db),
    validator: ValidationEngine = Depends(get_validator),
    user: TokenPayload = Depends(require_role("CBE_APPROVER")),
) -> PreApproveCheckResponse:
    bd      = await db.get_breakdown_with_components(breakdown_id)
    result  = CostBreakdownResult.from_db_row(bd)
    blockers = validator.validate_for_approve(result)
    warnings = [i for i in validator.validate(result) if i.severity == "WARNING"]
    return PreApproveCheckResponse(
        breakdown_id=breakdown_id,
        can_approve=len(blockers) == 0,
        blockers=[{"code": e.code, "message": str(e)} for e in blockers],
        warnings=[{"code": w.code, "message": str(w)} for w in warnings],
    )
```

---

## 8. Monitoring

### 8.1 Metryki Prometheus (24 metryki)

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# ─── Kalkulacje ───────────────────────────────────────────────

cbe_breakdown_total = Counter(
    "cbe_breakdown_total",
    "Łączna liczba kalkulacji kosztów",
    ["location_code", "overhead_profile", "status"]
)

cbe_breakdown_duration_seconds = Histogram(
    "cbe_breakdown_duration_seconds",
    "Czas kalkulacji kosztów",
    ["location_code"],
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10]
)

cbe_unit_cost_eur = Histogram(
    "cbe_unit_cost_eur",
    "Rozkład jednostkowych kosztów kalkulacji",
    buckets=[0.1, 1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000]
)

cbe_overall_confidence = Histogram(
    "cbe_overall_confidence",
    "Rozkład overall_confidence",
    buckets=[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 1.0]
)

cbe_confidence_band_total = Counter(
    "cbe_confidence_band_total",
    "Kalkulacje wg pasma confidence",
    ["band"]   # HIGH / MEDIUM / LOW / INDICATIVE
)

# ─── Składniki kosztów ────────────────────────────────────────

cbe_component_amount_eur = Histogram(
    "cbe_component_amount_eur",
    "Kwota składnika kosztów",
    ["component_type", "location_code"],
    buckets=[0.01, 0.1, 1, 10, 100, 1000, 10000]
)

cbe_category_share_pct = Summary(
    "cbe_category_share_pct",
    "Udział kategorii kosztów [%]",
    ["category"]   # MATERIAL / LABOR / MACHINE / ENERGY / TOOLING / OVERHEAD
)

cbe_data_source_total = Counter(
    "cbe_data_source_total",
    "Liczba składników wg źródła danych",
    ["data_source", "component_type"]
)

# ─── Walidacja ────────────────────────────────────────────────

cbe_validation_issue_total = Counter(
    "cbe_validation_issue_total",
    "Wyniki walidacji kalkulacji",
    ["rule_code", "severity"]
)

cbe_approve_blocked_total = Counter(
    "cbe_approve_blocked_total",
    "Zablokowane zatwierdzenia",
    ["rule_code"]
)

# ─── Stawki i dane referencyjne ───────────────────────────────

cbe_material_rate_age_days = Gauge(
    "cbe_material_rate_age_days",
    "Wiek stawki materiałowej [dni]",
    ["material_designation"]
)

cbe_location_rate_age_days = Gauge(
    "cbe_location_rate_age_days",
    "Wiek stawek lokalizacji [dni]",
    ["location_code"]
)

cbe_fx_rate_age_hours = Gauge(
    "cbe_fx_rate_age_hours",
    "Wiek kursu walutowego [h]",
    ["currency"]
)

# ─── API ─────────────────────────────────────────────────────

cbe_api_request_total = Counter(
    "cbe_api_request_total",
    "Żądania HTTP do API CBE",
    ["method", "endpoint", "status_code"]
)

cbe_api_duration_seconds = Histogram(
    "cbe_api_duration_seconds",
    "Latencja API CBE",
    ["method", "endpoint"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5]
)

# ─── Worker / Queue ───────────────────────────────────────────

cbe_worker_queue_depth = Gauge(
    "cbe_worker_queue_depth",
    "Głębokość kolejki kalkulacji"
)

cbe_worker_active_jobs = Gauge(
    "cbe_worker_active_jobs",
    "Aktywne kalkulacje w workerach",
    ["worker_id"]
)

# ─── Outbox ───────────────────────────────────────────────────

cbe_outbox_lag_seconds = Gauge(
    "cbe_outbox_lag_seconds",
    "Opóźnienie publikacji eventów Outbox"
)

cbe_outbox_published_total = Counter(
    "cbe_outbox_published_total",
    "Opublikowane eventy Outbox",
    ["topic"]
)
```

### 8.2 Dashboardy Grafana (6 dashboardów)

| Dashboard | Panele |
|-----------|--------|
| **CBE Overview** | Kalkulacje/h, status breakdown, confidence distribution, top locations |
| **Cost Drivers** | Waterfall chart kategorii, share% trends per location, material vs labor ratio |
| **Quantity-Break Analysis** | Unit cost vs quantity curves, tooling amortization, break-even quantity |
| **Location Comparison** | Heatmap: location × category cost, cheapest location trend |
| **Data Quality** | Rate age per location, confidence band distribution, stale data alerts |
| **API Performance** | P50/P95/P99 latency, error rate, breakdown duration percentiles |

### 8.3 Reguły Alertmanager (8 reguł)

```yaml
groups:
  - name: cbe_alerts
    rules:

      - alert: CBEHighFailureRate
        expr: |
          rate(cbe_breakdown_total{status="error"}[5m])
          / rate(cbe_breakdown_total[5m]) > 0.05
        for: 5m
        severity: critical
        annotations:
          summary: "CBE failure rate > 5%"

      - alert: CBELowConfidenceRate
        expr: |
          rate(cbe_confidence_band_total{band=~"LOW|INDICATIVE"}[30m])
          / rate(cbe_confidence_band_total[30m]) > 0.30
        for: 15m
        severity: warning
        annotations:
          summary: "30%+ kalkulacji z niskim confidence"

      - alert: CBEStaleLocationRates
        expr: cbe_location_rate_age_days > 90
        for: 1h
        severity: warning
        annotations:
          summary: "Stawki lokalizacji {{ $labels.location_code }} starsze niż 90 dni"

      - alert: CBEStaleMaterialRates
        expr: cbe_material_rate_age_days > 30
        for: 1h
        severity: warning
        annotations:
          summary: "Cena materiału {{ $labels.material_designation }} nieaktualna > 30 dni"

      - alert: CBEOutboxLag
        expr: cbe_outbox_lag_seconds > 60
        for: 5m
        severity: critical
        annotations:
          summary: "Outbox event lag > 60s — Kafka publishing issue"

      - alert: CBEAPIHighLatency
        expr: |
          histogram_quantile(0.95,
            rate(cbe_api_duration_seconds_bucket{endpoint="/api/v1/cbe/breakdowns"}[5m])
          ) > 5
        for: 5m
        severity: warning
        annotations:
          summary: "CBE API P95 latency > 5s for POST /breakdowns"

      - alert: CBEQueueDepthHigh
        expr: cbe_worker_queue_depth > 100
        for: 10m
        severity: warning
        annotations:
          summary: "CBE calculation queue depth > 100 — consider scaling workers"

      - alert: CBEApproveBlockedSpike
        expr: rate(cbe_approve_blocked_total[15m]) > 5
        for: 5m
        severity: warning
        annotations:
          summary: "Wzrost zablokowanych zatwierdzeń — sprawdź jakość danych wejściowych"
```

### 8.4 SLI / SLO

| SLI | SLO |
|-----|-----|
| `cbe_breakdown_duration_seconds` p95 | ≤ 2s (simple, CPU) |
| `cbe_breakdown_duration_seconds` p95 | ≤ 10s (full, 5+ operacji) |
| `cbe_api_duration_seconds` p95 GET | ≤ 300ms |
| Failure rate (status=error) | < 2% |
| `cbe_overall_confidence` median | ≥ 0.75 |
| HIGH+MEDIUM confidence rate | ≥ 70% kalkulacji |
| Outbox lag | < 30s (p99) |
| API Availability | ≥ 99.5% |
