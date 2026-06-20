# Cost Breakdown Engine — Sections 9–11

## 9. Testing

### 9.1 Macierz testów

| Typ | Narzędzie | Cel | Pokrycie |
|-----|-----------|-----|:--------:|
| Unit — algorytm | pytest | CostBreakdownEngine, każdy sub-kalkulator | ≥ 90% |
| Unit — walidacja | pytest | ValidationEngine V001–V009 | 100% |
| Unit — alokacja | pytest | LocationRates, OverheadProfile, scrap | 100% |
| Integration — DB | pytest + testcontainers | SQL schema, trigery, widoki | ≥ 85% |
| Integration — Outbox | pytest + kafka-python | CBEOutboxPublisher E2E | ≥ 80% |
| API contract | schemathesis | OpenAPI 3.1 fuzz + property testing | Wszystkie endpointy |
| Load | k6 | POST /breakdowns P95 ≤ 2s @ 50 rps | Throughput L2 |
| Accuracy | golden dataset | Porównanie z ręcznymi kalkulacjami | MAPE ≤ 5% |

### 9.2 Unit — CostBreakdownEngine

```python
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from cbe.engine import CostBreakdownEngine, CostBreakdownRequest
from cbe.models import MaterialInput, OperationInput, ToolingInput

@pytest.fixture
def mock_engine():
    rate_repo = AsyncMock()
    rate_repo.get_labor_rates.return_value = {
        "OPERATOR": Decimal("18.00"),
        "SETUP":    Decimal("22.00"),
    }
    rate_repo.get_inspection_rate.return_value = Decimal("0.05")

    material_svc = AsyncMock()
    material_svc.resolve_price.return_value = (
        Decimal("0.82"),   # EUR/kg
        "RATE_TABLE",
        0.80,
    )

    machine_repo = AsyncMock()
    machine_repo.resolve.return_value = MagicMock(
        machine_id=uuid4(),
        machine_type="CNC_TURNING",
        capex_eur=Decimal("250000"),
        life_years=Decimal("10"),
        oee=Decimal("0.80"),
        power_kw=Decimal("18"),
        air_consumption_m3h=Decimal("5"),
        coolant_lh=Decimal("3"),
        maintenance_rate_pct=Decimal("3"),
    )

    energy_svc = AsyncMock()
    energy_svc.get_rates.return_value = {
        "electricity_eur_kwh": Decimal("0.16"),
        "air_eur_m3":          Decimal("0.018"),
        "coolant_eur_l":       Decimal("0.04"),
    }

    tooling_repo = AsyncMock()

    overhead_cfg = MagicMock()
    overhead_cfg.get_rates.return_value = {
        "factory_overhead_pct": Decimal("22"),
        "sg_and_a_pct":         Decimal("10"),
        "rnd_pct":              Decimal("2"),
        "margin_pct":           Decimal("7"),
    }

    fx_svc = AsyncMock()

    return CostBreakdownEngine(
        rate_repo=rate_repo,
        material_svc=material_svc,
        machine_repo=machine_repo,
        energy_svc=energy_svc,
        tooling_repo=tooling_repo,
        overhead_cfg=overhead_cfg,
        fx_svc=fx_svc,
    )


class TestCostBreakdownEngine:

    @pytest.mark.asyncio
    async def test_basic_breakdown_positive_total(self, mock_engine):
        req = CostBreakdownRequest(
            part_id=uuid4(),
            quantity=Decimal("100"),
            location_code="PL",
            material=MaterialInput(
                material_designation="S235JR",
                gross_weight_kg=Decimal("4.15"),
                net_weight_kg=Decimal("3.20"),
            ),
            operations=[OperationInput(
                operation_code="TURN",
                machine_type="CNC_TURNING",
                cycle_time_s=Decimal("85"),
                setup_time_s=Decimal("900"),
                batch_size=50,
                operators=Decimal("1"),
            )],
        )
        result = await mock_engine.breakdown(req)
        assert result.total_cost_eur > 0
        assert result.unit_cost_eur > 0
        assert result.material_eur > 0
        assert result.labor_eur > 0

    @pytest.mark.asyncio
    async def test_shares_sum_to_100(self, mock_engine):
        req = CostBreakdownRequest(
            part_id=uuid4(),
            quantity=Decimal("50"),
            location_code="PL",
            material=MaterialInput(
                material_designation="S235JR",
                gross_weight_kg=Decimal("2.0"),
                net_weight_kg=Decimal("1.6"),
            ),
            operations=[OperationInput(
                operation_code="MILL",
                machine_type="MILLING_3AX",
                cycle_time_s=Decimal("60"),
                batch_size=25,
            )],
        )
        result = await mock_engine.breakdown(req)
        total_pct = (result.material_pct + result.labor_pct + result.machine_pct +
                     result.energy_pct + result.tooling_pct + result.overhead_pct)
        assert abs(total_pct - 100.0) < 0.5

    @pytest.mark.asyncio
    async def test_unit_cost_decreases_with_quantity(self, mock_engine):
        """Efekt skali — tooling amortyzacja sprawia, że unit cost maleje z ilością."""
        base = CostBreakdownRequest(
            part_id=uuid4(),
            location_code="PL",
            material=MaterialInput("S235JR", Decimal("3"), Decimal("2.4")),
            operations=[OperationInput("TURN", "CNC_TURNING",
                                       cycle_time_s=Decimal("90"),
                                       setup_time_s=Decimal("1800"),
                                       batch_size=1)],
            tooling=[ToolingInput(tool_id=None,
                                  tool_cost_eur=Decimal("2000"),
                                  planned_qty=Decimal("10000"))],
        )
        r1  = await mock_engine.breakdown(base.with_quantity(1))
        r10 = await mock_engine.breakdown(base.with_quantity(10))
        r1000 = await mock_engine.breakdown(base.with_quantity(1000))

        assert r1.unit_cost_eur > r10.unit_cost_eur > r1000.unit_cost_eur

    @pytest.mark.asyncio
    async def test_no_material_returns_warning(self, mock_engine):
        req = CostBreakdownRequest(
            part_id=uuid4(),
            quantity=Decimal("10"),
            location_code="PL",
            material=None,
            operations=[OperationInput("MILL", "MILLING_3AX", Decimal("45"), batch_size=10)],
        )
        result = await mock_engine.breakdown(req)
        assert result.material_eur == 0
        assert any("material" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_tooling_amortization(self, mock_engine):
        req = CostBreakdownRequest(
            part_id=uuid4(),
            quantity=Decimal("500"),
            location_code="PL",
            tooling=[ToolingInput(
                tool_id=None,
                tool_cost_eur=Decimal("5000"),
                planned_qty=Decimal("5000"),  # 1 EUR/pcs → 500 EUR total
            )],
        )
        result = await mock_engine.breakdown(req)
        assert result.tooling_eur == Decimal("500")

    @pytest.mark.asyncio
    async def test_de_higher_labor_than_pl(self, mock_engine):
        """Robocizna w DE droższa niż w PL."""
        mock_engine._rates.get_labor_rates.side_effect = lambda loc: (
            {"OPERATOR": Decimal("42"), "SETUP": Decimal("52")} if loc == "DE"
            else {"OPERATOR": Decimal("18"), "SETUP": Decimal("22")}
        )
        op = OperationInput("TURN", "CNC_TURNING", Decimal("120"), Decimal("900"), 50)
        base = CostBreakdownRequest(part_id=uuid4(), quantity=Decimal("100"), operations=[op])

        r_de = await mock_engine.breakdown(base.with_location("DE"))
        r_pl = await mock_engine.breakdown(base.with_location("PL"))
        assert r_de.labor_eur > r_pl.labor_eur

    @pytest.mark.asyncio
    async def test_confidence_weighted_by_amount(self, mock_engine):
        """Confidence ważona kwotami — drogi ESTIMATE obniża wynik."""
        req = CostBreakdownRequest(
            part_id=uuid4(),
            quantity=Decimal("1"),
            location_code="PL",
            material=MaterialInput("S235JR", Decimal("1"), Decimal("0.8")),
        )
        # Zastąp cenę materiału ESTIMATE (confidence 0.50) na wysoką kwotę
        mock_engine._mat.resolve_price.return_value = (Decimal("500"), "ESTIMATE", 0.50)
        result = await mock_engine.breakdown(req)
        assert result.overall_confidence < 0.75


class TestAllocationRules:

    def test_gross_weight_turning(self):
        from cbe.allocation import gross_weight
        gw = gross_weight(Decimal("10.0"), "TURNING")
        assert gw == Decimal("13.000")  # 30% naddatek

    def test_gross_weight_forging(self):
        from cbe.allocation import gross_weight
        gw = gross_weight(Decimal("10.0"), "FORGING")
        assert gw == Decimal("10.800")

    def test_gross_weight_default(self):
        from cbe.allocation import gross_weight
        gw = gross_weight(Decimal("10.0"), "UNKNOWN_PROCESS")
        assert gw == Decimal("11.000")

    def test_overhead_profile_lean_multiplier(self):
        from cbe.allocation import OverheadProfile, PROFILE_MULTIPLIERS
        mult = PROFILE_MULTIPLIERS[OverheadProfile.LEAN]
        assert mult["factory"] == 0.80
        assert mult["margin"]  == 0.90

    def test_overhead_profile_premium_multiplier(self):
        from cbe.allocation import OverheadProfile, PROFILE_MULTIPLIERS
        mult = PROFILE_MULTIPLIERS[OverheadProfile.PREMIUM]
        assert mult["margin"] == 1.25


class TestValidationEngine:

    def setup_method(self):
        from cbe.validation import ValidationEngine
        self.v = ValidationEngine()

    def _make_result(self, **kwargs):
        from cbe.models import CostBreakdownResult
        defaults = dict(
            breakdown_id=uuid4(), part_id=uuid4(), bom_line_id=None,
            quantity=Decimal("100"), material_eur=Decimal("100"),
            labor_eur=Decimal("50"), machine_eur=Decimal("30"),
            energy_eur=Decimal("10"), tooling_eur=Decimal("5"),
            overhead_eur=Decimal("50"), total_cost_eur=Decimal("245"),
            unit_cost_eur=Decimal("2.45"), overall_confidence=0.80,
        )
        defaults.update(kwargs)
        return CostBreakdownResult(**defaults)

    def test_v001_negative_component_raises(self):
        from cbe.models import CostComponent, CostComponentType
        from cbe.validation import NegativeCostError
        result = self._make_result()
        result.components = [CostComponent(
            component_type=CostComponentType.RAW_MATERIAL,
            amount_eur=Decimal("-50"),
            basis="test", confidence=0.9, source="TEST",
        )]
        issues = self.v.validate(result)
        assert any(isinstance(i, NegativeCostError) for i in issues)

    def test_v003_low_confidence_blocks_approve(self):
        from cbe.validation import ConfidenceTooLowError
        result = self._make_result(overall_confidence=0.45)
        blockers = self.v.validate_for_approve(result)
        assert any(isinstance(b, ConfidenceTooLowError) for b in blockers)

    def test_v003_sufficient_confidence_no_block(self):
        result = self._make_result(overall_confidence=0.55)
        blockers = self.v.validate_for_approve(result)
        from cbe.validation import ConfidenceTooLowError
        assert not any(isinstance(b, ConfidenceTooLowError) for b in blockers)

    def test_v007_stale_rates_warning(self):
        from datetime import date
        from cbe.validation import StaleRateDataError
        old_date = date(2024, 1, 1)
        result = self._make_result()
        issues = self.v._check_stale_rates(old_date)
        assert any(isinstance(i, StaleRateDataError) for i in issues)

    def test_v008_material_anomaly(self):
        from cbe.validation import MaterialCostAnomalyError
        result = self._make_result(
            material_eur=Decimal("900"), labor_eur=Decimal("50"),
            machine_eur=Decimal("10"), energy_eur=Decimal("5"),
            tooling_eur=Decimal("0"), overhead_eur=Decimal("50"),
        )
        issues = self.v._check_material_anomaly(result)
        assert any(isinstance(i, MaterialCostAnomalyError) for i in issues)

    def test_v009_overhead_too_high(self):
        from cbe.validation import OverheadTooHighError
        result = self._make_result(
            material_eur=Decimal("10"), labor_eur=Decimal("5"),
            machine_eur=Decimal("5"),  energy_eur=Decimal("0"),
            tooling_eur=Decimal("0"),  overhead_eur=Decimal("200"),
            total_cost_eur=Decimal("220"),
        )
        issues = self.v._check_overhead_share(result)
        assert any(isinstance(i, OverheadTooHighError) for i in issues)


class TestQuantityBreakTable:

    @pytest.mark.asyncio
    async def test_quantity_break_monotone(self, mock_engine):
        from cbe.quantity_break import get_quantity_break_table, QUANTITY_BREAKS
        req = CostBreakdownRequest(
            part_id=uuid4(), location_code="PL",
            material=MaterialInput("S235JR", Decimal("5"), Decimal("4")),
            operations=[OperationInput("TURN", "CNC_TURNING", Decimal("90"), Decimal("1800"), 50)],
            tooling=[ToolingInput(None, Decimal("3000"), Decimal("10000"))],
        )
        table = await get_quantity_break_table(mock_engine, req)
        unit_costs = [row["unit_cost_eur"] for row in table]
        # Unit cost powinien maleć lub być stały wraz ze wzrostem ilości
        assert all(unit_costs[i] >= unit_costs[i + 1] for i in range(len(unit_costs) - 1))
```

### 9.3 Integration — DB schema

```python
import pytest
import asyncpg

@pytest.mark.asyncio
async def test_generated_columns(pg_pool):
    """Kolumny generated: total_cost_eur i unit_cost_eur."""
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO cbe.cost_breakdowns (
                part_id, quantity, location_code,
                material_eur, labor_eur, machine_eur,
                energy_eur, tooling_eur, overhead_eur, created_by
            ) VALUES (
                gen_random_uuid(), 100, 'PL',
                500, 250, 150, 30, 20, 80, 'test'
            ) RETURNING total_cost_eur, unit_cost_eur
        """)
        assert row["total_cost_eur"] == pytest.approx(1030.0)
        assert row["unit_cost_eur"]  == pytest.approx(10.30)

@pytest.mark.asyncio
async def test_outbox_trigger_on_approve(pg_pool):
    """Trigger zapisuje event do outbox przy APPROVED."""
    async with pg_pool.acquire() as conn:
        bd_id = await conn.fetchval("""
            INSERT INTO cbe.cost_breakdowns (
                part_id, quantity, location_code,
                material_eur, labor_eur, machine_eur,
                energy_eur, tooling_eur, overhead_eur,
                status, created_by
            ) VALUES (
                gen_random_uuid(), 10, 'DE',
                100, 50, 30, 5, 10, 25, 'CALCULATED', 'test'
            ) RETURNING breakdown_id
        """)
        await conn.execute("""
            UPDATE cbe.cost_breakdowns
               SET status = 'APPROVED', approved_by = 'approver@test.com'
             WHERE breakdown_id = $1
        """, bd_id)
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM cbe.outbox_events WHERE key = $1", str(bd_id))
        assert count == 1

@pytest.mark.asyncio
async def test_confidence_band_generated(pg_pool):
    async with pg_pool.acquire() as conn:
        row = await conn.fetchrow("""
            INSERT INTO cbe.cost_breakdowns (
                part_id, quantity, location_code,
                material_eur, labor_eur, machine_eur,
                energy_eur, tooling_eur, overhead_eur,
                overall_confidence, created_by
            ) VALUES (
                gen_random_uuid(), 1, 'DE',
                10,5,3,1,0,3, 0.95, 'test'
            ) RETURNING confidence_band
        """)
        assert row["confidence_band"] == "HIGH"
```

### 9.4 Accuracy — golden dataset

```python
# Golden dataset: 50 ręcznie obliczonych kalkulacji (CSV)
# Kolumny: part_id, location, qty, expected_unit_cost_eur, expected_material_pct, ...

@pytest.mark.golden
@pytest.mark.asyncio
async def test_golden_accuracy(mock_engine, golden_dataset):
    mape_values = []
    for case in golden_dataset:
        req   = CostBreakdownRequest.from_golden(case)
        result = await mock_engine.breakdown(req)
        mape  = abs(float(result.unit_cost_eur) - case["expected_unit_cost_eur"]) \
                / case["expected_unit_cost_eur"]
        mape_values.append(mape)

    avg_mape = sum(mape_values) / len(mape_values)
    assert avg_mape <= 0.05, f"Golden MAPE {avg_mape:.2%} > 5%"
```

### 9.5 k6 load test

```javascript
// k6 load test — POST /api/v1/cbe/breakdowns
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const breakdownDuration = new Trend("breakdown_duration_ms");
const errorRate         = new Rate("error_rate");

export const options = {
    stages: [
        { duration: "1m",  target: 20  },  // ramp-up
        { duration: "3m",  target: 50  },  // steady 50 rps
        { duration: "1m",  target: 100 },  // peak
        { duration: "1m",  target: 0   },  // ramp-down
    ],
    thresholds: {
        "breakdown_duration_ms": ["p(95)<2000"],   // P95 < 2s
        "error_rate":            ["rate<0.02"],    // < 2% errors
        "http_req_duration":     ["p(99)<5000"],
    },
};

const PAYLOAD = JSON.stringify({
    part_id: "550e8400-e29b-41d4-a716-446655440000",
    quantity: 100,
    location_code: "PL",
    overhead_profile: "DEFAULT",
    material: {
        material_designation: "S235JR",
        gross_weight_kg: "3.5",
        net_weight_kg:   "2.8",
    },
    operations: [{
        operation_code: "TURN",
        machine_type:   "CNC_TURNING",
        cycle_time_s:   "85",
        setup_time_s:   "900",
        batch_size:     50,
        operators:      "1",
    }],
});

export default function () {
    const res = http.post(
        `${__ENV.BASE_URL}/api/v1/cbe/breakdowns`,
        PAYLOAD,
        { headers: { "Content-Type": "application/json",
                     "Authorization": `Bearer ${__ENV.TOKEN}` } }
    );
    const ok = check(res, {
        "status 201":           (r) => r.status === 201,
        "has unit_cost_eur":    (r) => JSON.parse(r.body).unit_cost_eur > 0,
        "confidence_band set":  (r) => !!JSON.parse(r.body).confidence_band,
    });
    errorRate.add(!ok);
    breakdownDuration.add(res.timings.duration);
    sleep(0.02);
}
```

---

## 10. Risks

| ID | Ryzyko | Prawdop. | Wpływ | Mitygacja |
|----|--------|:--------:|:-----:|-----------|
| R01 | Nieaktualne stawki materiałów → błędne kalkulacje | Wysoka | Wysoki | Alert V007 > 30 dni; integracja SOP (ceny z ofert); daily batch import |
| R02 | Brak danych maszyn (capex, OEE) → brak kosztów MACHINE | Średnia | Wysoki | Fallback na `flat_rate_eur_h`; ESTIMATE source z confidence 0.50; warning V006 |
| R03 | Błędny czas cyklu z BOM → przekłamania labor/machine | Średnia | Wysoki | Golden dataset CI gate; tolerancja ±10% vs historycznych danych |
| R04 | Waga brutto zaniżona (DAE) → niedoszacowanie RAW_MATERIAL | Średnia | Wysoki | Domyślny scrap factor per operacja; walidacja V008 material > 85% |
| R05 | Różne stawki overhead dla różnych klientów → konflikt danych | Niska | Średni | OverheadProfile per request; customer-specific profiles w konfiguracji |
| R06 | Kurs walutowy przestarzały → błędne przeliczenia | Niska | Średni | FXRateService z ECB; alert cbe_fx_rate_age_hours > 25h |
| R07 | Skalowanie: > 500 równoległych kalkulacji → bottleneck DB | Niska | Wysoki | FOR UPDATE SKIP LOCKED; asyncio.gather per request; HPA; connection pooling |
| R08 | Outbox event niedostarczony do CEE → desync kosztów | Niska | Wysoki | Transactional Outbox; retry z backoff; DLQ; alert outbox lag > 60s |
| R09 | Overhead profile "PREMIUM" używany zamiast "DEFAULT" → zawyżenie | Niska | Średni | Pre-approve check wyświetla overhead_pct; audit log zmiany profilu |
| R10 | Tooling cost one-time w prototypach → mylące unit cost | Średnia | Średni | PROTOTYPE overhead profile; quantity-break table zawsze pokazana w UI |
| R11 | Nowe lokalizacje bez stawek → fallback na DEFAULT | Wysoka | Niski | Walidacja location_code przy zapisie; warning w API response |
| R12 | Zbyt niskie confidence blokuje workflow → frustracja użytkownika | Średnia | Średni | Próg APPROVE = 0.50 (INDICATIVE); REVIEWED = manual override |
| R13 | Integracja z DAE niedostępna → brak wagi/materiału | Niska | Średni | Async enrichment (nie blokuje kalkulacji); ESTIMATE fallback |
| R14 | Złośliwe dane wejściowe (negative quantity, huge values) | Niska | Niski | Pydantic validators; `quantity > 0`; `unit_cost < 1M` |
| R15 | Zmiany stawek wpływają na historyczne kalkulacje | Niska | Wysoki | Stawki snapshoted w `input_snapshot` JSONB; wersjonowanie `cbe.breakdown_versions` |

---

## 11. Roadmap

### Cele KPI (po S28)

| Metryka | Cel |
|---------|-----|
| Breakdown P95 (pełna kalkulacja) | ≤ 2s |
| Breakdown P95 (quantity-break table) | ≤ 10s |
| API P95 GET | ≤ 300ms |
| Failure rate | < 2% |
| overall_confidence median | ≥ 0.78 |
| HIGH + MEDIUM confidence rate | ≥ 72% |
| Availability | ≥ 99.5% |
| Throughput L2 | ≥ 500 kalkulacji/h |

### Faza 1 — Foundation (S1–S8)

| Sprint | Cel |
|--------|-----|
| S1 | DB schema `cbe` (9 tabel, ENUMy, triggery, widoki); migracje Alembic |
| S2 | `CostBreakdownEngine` core: material + overhead; `LocationRates` DEFAULT_DE |
| S3 | Labor kalkulator: direct / setup / inspection; `RateRepository` + tabele stawek |
| S4 | Machine kalkulator: depreciation + maintenance; `MachineRepository`; 5 typów maszyn |
| S5 | Energy kalkulator: electricity + air + coolant; `EnergyRateService` per location |
| S6 | Tooling kalkulator: amortyzacja, PROTOTYPE profile; `ToolingRepository` |
| S7 | `ValidationEngine` V001–V009; pre-approve check endpoint; `CBEOutboxPublisher` |
| S8 | Pełne API FastAPI (POST /breakdowns, GET /breakdowns/{id}/components, approve/reject) |

### Faza 2 — Enrichment + Analytics (S9–S18)

| Sprint | Cel |
|--------|-----|
| S9 | Quantity-break table: 9 progów, `get_quantity_break_table`, caching Redis |
| S10 | Location comparison API (5 lokalizacji równolegle); `LocationComparisonResponse` |
| S11 | Material sensitivity analysis; `material_elasticity` coefficient |
| S12 | Integracja SOP: `MaterialPriceService` konsumuje `sop.line_item.priced` |
| S13 | Integracja DAE: enrichment z `dae.drawing.parsed` (waga, materiał, operacje) |
| S14 | Integracja BOM Engine: auto-trigger kalkulacji na `bome.bom_line.released` |
| S15 | `BreakdownVersioning`: snapshot przy każdej zmianie statusu |
| S16 | Analytics dashboards (6 Grafana); Alertmanager 8 reguł; SLO baseline |
| S17 | Multilingual rate tables (CN, MX, IN, BR); overhead profiles LEAN/PREMIUM/EXPORT |
| S18 | Export CSV/XLSX (/analytics/export); waterfall chart data endpoint |

### Faza 3 — Intelligence + Scale (S19–S28)

| Sprint | Cel |
|--------|-----|
| S19 | Parametric cost estimation: regression model (feature vectors z DAE → cost estimate) |
| S20 | Confidence improvement: Bayesian update gdy quote ≠ estimate |
| S21 | Anomaly detection: IQR-based alert dla cost components odbiegających od historii |
| S22 | HPA `cbe-api` 2–15 pods, `cbe-worker` 1–8 pods; load test L2 |
| S23 | `OverheadProfile` per customer/project (customer_id w kalkulacji) |
| S24 | Cost target analysis: target cost → reverse-engineer required rates/margins |
| S25 | Multi-currency output: full conversion chain EUR→USD/PLN/CNY przy approvals |
| S26 | Audit log full (kto zmienił jaką stawkę, kiedy, jaki wpływ na kalkulacje) |
| S27 | API rate limiting per user; bulk breakdown endpoint (POST /breakdowns/batch) |
| S28 | DR testing; SLO hardening; penetration test; GDPR data retention |
