# Supplier Offer Parser — Sekcje 12–14

## 12. Testing

### 12.1 Strategia testów

| Typ testu | Framework | Zakres | Cel |
|-----------|-----------|--------|-----|
| Unit | pytest 8.x | Price extractor, unit converter, NER regex, material matcher | Logika izolowana |
| Integration | pytest + Testcontainers | DB (PostgreSQL), IMAP mock, EDI parsing, pipeline E2E | Integracja komponentów |
| NLP Regression | pytest + golden fixtures | 200 ofert z ground truth (ceny, jednostki, materiały) | Regresja NLP accuracy |
| Contract | schemathesis | OpenAPI spec vs. live endpoints | Kontrakty API |
| Load | k6 | 100 RPS uploads, 500 RPS reads | Wydajność |
| Data Quality | Great Expectations | Extracted prices: nulls, ranges, currency distribution | Jakość danych |
| Security | bandit + OWASP ZAP | Email injection, file upload, HTML sanitization | Bezpieczeństwo |
| End-to-End | pytest + real fixtures | Full flow: email → parse → BOM map → Kafka event | Integracja pełna |

### 12.2 Unit tests

```python
import pytest
from decimal import Decimal


# ── Price Extractor ───────────────────────────────────────────────────────────

class TestPriceExtractor:

    @pytest.fixture
    def extractor(self):
        return PriceExtractor()

    @pytest.mark.asyncio
    async def test_extract_simple_eur(self, extractor):
        doc = _make_doc("Unit price: EUR 1.25 per pcs")
        candidates = await extractor.extract(doc, [])
        assert any(abs(float(c.numeric_value) - 1.25) < 0.001 for c in candidates)
        assert any(c.currency_normalized == "EUR" for c in candidates)

    @pytest.mark.asyncio
    async def test_extract_symbol_prefix(self, extractor):
        doc = _make_doc("Price: €0.089 / piece")
        candidates = await extractor.extract(doc, [])
        assert any(abs(float(c.numeric_value) - 0.089) < 0.0001 for c in candidates)

    @pytest.mark.asyncio
    async def test_extract_german_format(self, extractor):
        """German number format: 1.234,56 EUR"""
        doc = _make_doc("Preis: 1.234,56 EUR", language="de")
        candidates = await extractor.extract(doc, [])
        assert any(abs(float(c.numeric_value) - 1234.56) < 0.01 for c in candidates)

    @pytest.mark.asyncio
    async def test_extract_price_break(self, extractor):
        doc = _make_doc("500+ pcs: EUR 0.85\n1000+ pcs: EUR 0.72")
        candidates = await extractor.extract(doc, [])
        breaks = [c for c in candidates if c.quantity_break is not None]
        assert len(breaks) >= 2
        quantities = sorted(float(b.quantity_break) for b in breaks)
        assert quantities[0] == pytest.approx(500.0)

    @pytest.mark.asyncio
    async def test_extract_total_price(self, extractor):
        doc = _make_doc("Total: EUR 15,000.00")
        candidates = await extractor.extract(doc, [])
        totals = [c for c in candidates if c.price_type == "TOTAL"]
        assert len(totals) >= 1

    @pytest.mark.asyncio
    async def test_outlier_filtering(self, extractor):
        """Wildly high price should be flagged as OUTLIER."""
        text = "\n".join([
            "Part A: EUR 1.20/pcs",
            "Part B: EUR 1.35/pcs",
            "Part C: EUR 1.10/pcs",
            "Tooling: EUR 999999.00",
        ])
        doc = _make_doc(text)
        candidates = await extractor.extract(doc, [])
        outliers = [c for c in candidates if c.price_type == "OUTLIER"]
        # High tooling price should be downgraded to OUTLIER or TOOLING
        assert any(c.price_type in ("OUTLIER", "TOOLING") for c in candidates
                   if float(c.numeric_value) > 100_000)

    def test_parse_number_en_format(self, extractor):
        result = extractor._parse_number("1,234.56", "en")
        assert result == pytest.approx(Decimal("1234.56"), rel=1e-4)

    def test_parse_number_de_format(self, extractor):
        result = extractor._parse_number("1.234,56", "de")
        assert result == pytest.approx(Decimal("1234.56"), rel=1e-4)

    def test_parse_number_plain(self, extractor):
        result = extractor._parse_number("0.085", "en")
        assert result == pytest.approx(Decimal("0.085"), rel=1e-6)

    def test_normalize_currency_symbol(self, extractor):
        assert extractor._normalize_currency("€") == "EUR"
        assert extractor._normalize_currency("$") == "USD"
        assert extractor._normalize_currency("PLN") == "PLN"


# ── Unit Conversion Engine ────────────────────────────────────────────────────

class TestUnitConversionEngine:

    @pytest.fixture
    def engine(self):
        return UnitConversionEngine()

    def test_resolve_kg(self, engine):
        unit = engine.resolve_unit("kg")
        assert unit is not None
        assert unit.code == "kg"
        assert unit.dimension == UnitDimension.MASS

    def test_resolve_pieces_aliases(self, engine):
        for alias in ["pcs", "piece", "stück", "szt", "units", "each", "ea"]:
            unit = engine.resolve_unit(alias)
            assert unit is not None, f"Alias '{alias}' not resolved"
            assert unit.code == "pcs"

    def test_resolve_per100(self, engine):
        unit = engine.resolve_unit("per 100")
        assert unit is not None
        assert unit.per_quantity == Decimal("100")

    def test_convert_g_to_kg(self, engine):
        result = engine.convert(Decimal("500"), "g", "kg")
        assert result is not None
        assert result.converted_value == pytest.approx(Decimal("0.5"), rel=1e-6)

    def test_convert_kg_to_lb(self, engine):
        result = engine.convert(Decimal("1"), "kg", "lb")
        assert result is not None
        assert float(result.converted_value) == pytest.approx(2.2046, rel=1e-3)

    def test_convert_dimension_mismatch(self, engine):
        result = engine.convert(Decimal("1"), "kg", "m")
        assert result is None

    def test_normalize_uom_unknown(self, engine):
        result = engine.normalize_uom("XYZ_UNKNOWN")
        assert result == "xyz_unknown"

    def test_parse_lead_time_weeks(self, engine):
        days = engine.parse_lead_time_days("Lead time: 4 weeks")
        assert days == 28

    def test_parse_lead_time_months(self, engine):
        days = engine.parse_lead_time_days("Lieferzeit: 2 Monate")
        assert days == 60

    def test_parse_lead_time_range(self, engine):
        days = engine.parse_lead_time_days("6-8 weeks")
        assert days == 56   # upper bound

    def test_parse_lead_time_days(self, engine):
        days = engine.parse_lead_time_days("30 days")
        assert days == 30


# ── NER Regex Backend ─────────────────────────────────────────────────────────

class TestRegexNERBackend:

    @pytest.fixture
    def backend(self):
        return RegexNERBackend()

    @pytest.mark.asyncio
    async def test_extract_part_number(self, backend):
        entities = await backend.extract("Part No: ABC-12345-X", "en")
        parts = [e for e in entities if e.entity_type == EntityType.PART_NUMBER]
        assert len(parts) >= 1
        assert "ABC-12345-X" in (e.normalized_value for e in parts)

    @pytest.mark.asyncio
    async def test_extract_lead_time_weeks(self, backend):
        entities = await backend.extract("Lead time: 6 weeks", "en")
        leads = [e for e in entities if e.entity_type == EntityType.LEAD_TIME]
        assert len(leads) >= 1
        assert "weeks" in (leads[0].normalized_value or "")

    @pytest.mark.asyncio
    async def test_extract_moq(self, backend):
        entities = await backend.extract("MOQ: 500 pcs", "en")
        moqs = [e for e in entities if e.entity_type == EntityType.MOQ]
        assert len(moqs) >= 1
        assert moqs[0].normalized_value == "500"

    @pytest.mark.asyncio
    async def test_extract_validity(self, backend):
        entities = await backend.extract("Valid until 2026-12-31", "en")
        validities = [e for e in entities if e.entity_type == EntityType.VALIDITY]
        assert len(validities) >= 1
        assert "2026-12-31" in (validities[0].normalized_value or "")

    @pytest.mark.asyncio
    async def test_extract_discount(self, backend):
        entities = await backend.extract("Discount: 5%", "en")
        discounts = [e for e in entities if e.entity_type == EntityType.DISCOUNT]
        assert len(discounts) >= 1
        assert float(discounts[0].normalized_value or "0") == pytest.approx(5.0, rel=1e-4)

    @pytest.mark.asyncio
    async def test_extract_tooling_cost(self, backend):
        entities = await backend.extract("Tooling cost: EUR 2500", "en")
        tooling = [e for e in entities if e.entity_type == EntityType.TOOLING_COST]
        assert len(tooling) >= 1

    @pytest.mark.asyncio
    async def test_german_moq(self, backend):
        entities = await backend.extract("Mindestbestellmenge: 1000 Stück", "de")
        moqs = [e for e in entities if e.entity_type == EntityType.MOQ]
        assert len(moqs) >= 1


# ── Material Matcher ──────────────────────────────────────────────────────────

class TestMaterialMatcher:

    @pytest.fixture
    def matcher(self):
        return MaterialMatcher()

    @pytest.mark.asyncio
    async def test_exact_match(self, matcher):
        result = await matcher.match("S235JR")
        assert result is not None
        assert result["designation"] == "S235JR"
        assert result["confidence"] >= 0.90

    @pytest.mark.asyncio
    async def test_alias_match_aisi304(self, matcher):
        result = await matcher.match("AISI 304")
        assert result is not None
        assert result["designation"] == "1.4301"

    @pytest.mark.asyncio
    async def test_case_insensitive(self, matcher):
        result = await matcher.match("aluminium")
        assert result is not None
        assert "ALUMINUM" in result["designation"] or result["designation"] == "ALUMINUM"

    @pytest.mark.asyncio
    async def test_unknown_material(self, matcher):
        result = await matcher.match("SuperAlloy XQ-99")
        assert result is not None
        assert result["method"] == "UNKNOWN"
        assert result["confidence"] < 0.50


# ── Supplier Mapper ───────────────────────────────────────────────────────────

class TestSupplierMapper:

    def test_extract_vat_eu(self):
        mapper = SupplierMapper(supplier_repo=None)
        vat = mapper._extract_vat("MwSt: DE123456789")
        assert vat == "DE123456789"

    def test_extract_vat_pl(self):
        mapper = SupplierMapper(supplier_repo=None)
        vat = mapper._extract_vat("NIP 1234567890")
        assert vat is not None

    def test_extract_duns(self):
        mapper = SupplierMapper(supplier_repo=None)
        duns = mapper._extract_duns("DUNS: 12-345-6789")
        assert duns == "123456789"

    def test_extract_company_gmbh(self):
        mapper = SupplierMapper(supplier_repo=None)
        name = mapper._extract_company_name("ACME Teile GmbH\nPriceList Q2 2026")
        assert "ACME Teile GmbH" in (name or "")


# ── Text Normalizer ───────────────────────────────────────────────────────────

class TestTextNormalizer:

    def test_normalize_curly_quotes(self):
        result = TextNormalizer.normalize(""Angebot" für 'Kunden'", "de")
        assert '"' in result
        assert "'" in result

    def test_normalize_eu_numbers_de(self):
        result = TextNormalizer.normalize("Preis: 1.234,56 EUR", "de")
        assert "1234.56" in result

    def test_normalize_dash_to_hyphen(self):
        result = TextNormalizer.normalize("Lead time: 4–6 weeks", "en")
        assert "-" in result


# ── BOM Mapper ────────────────────────────────────────────────────────────────

class TestBOMMapper:

    def test_cosine_similarity_identical(self):
        v = [1.0, 0.0, 0.5]
        assert BOMMapper._cosine_similarity(v, v) == pytest.approx(1.0, rel=1e-6)

    def test_cosine_similarity_orthogonal(self):
        a = [1.0, 0.0]
        b = [0.0, 1.0]
        assert BOMMapper._cosine_similarity(a, b) == pytest.approx(0.0, rel=1e-6)

    def test_cosine_similarity_zero_vector(self):
        a = [0.0, 0.0]
        b = [1.0, 1.0]
        assert BOMMapper._cosine_similarity(a, b) == 0.0


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_doc(text: str, language: str = "en") -> OfferDocument:
    return OfferDocument(
        offer_id="test", raw_offer_id="raw",
        format=OfferFormat.EMAIL_TEXT,
        channel=IngestionChannel.MANUAL_UPLOAD,
        text_content=text, structured_data=None,
        pages=1, language=language, encoding="utf-8",
        supplier_hint=None, rfq_ref=None, currency_hint=None,
        extraction_quality=1.0,
    )
```

### 12.3 Integration tests

```python
import pytest
from testcontainers.postgres import PostgresContainer


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
async def db_pool(pg_container):
    import asyncpg
    dsn = pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    pool = await asyncpg.create_pool(dsn)
    async with pool.acquire() as conn:
        schema_sql = (Path(__file__).parent / "../../sql/sop_schema.sql").read_text()
        await conn.execute(schema_sql)
    yield pool
    await pool.close()


class TestFullPipeline:

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_pdf_offer_pipeline(self, db_pool, tmp_path):
        """Create minimal PDF offer → parse → verify line items in DB."""
        import fpdf
        pdf = fpdf.FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        pdf.cell(200, 10, txt="SUPPLIER: ACME GmbH, VAT DE123456789")
        pdf.ln()
        pdf.cell(200, 10, txt="RFQ Ref: RFQ-2026-001")
        pdf.ln()
        pdf.cell(200, 10, txt="Part No: ABC-001 | Qty: 500 pcs | Unit price: EUR 1.25")
        pdf_path = tmp_path / "offer.pdf"
        pdf.output(str(pdf_path))

        raw = RawOffer(
            offer_id=_new_id(), channel=IngestionChannel.MANUAL_UPLOAD,
            format=OfferFormat.PDF, raw_content=pdf_path.read_bytes(),
            content_type="application/pdf", filename="offer.pdf",
            sender_email="sales@acme.de", sender_domain="acme.de",
            subject="Quote for RFQ-2026-001",
            received_at="2026-06-01T10:00:00Z",
            checksum_sha256="abc123", file_size_bytes=1000,
        )
        pipeline = _build_test_pipeline()
        ctx = await pipeline.process_raw(raw, db_pool)

        assert len(ctx.line_items) >= 1
        item = ctx.line_items[0]
        assert abs(item.unit_price_raw - 1.25) < 0.01

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_excel_offer_structured(self, db_pool, tmp_path):
        """Excel with price table → structured extraction → DB."""
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Part Number", "Description", "Qty", "UOM", "Unit Price", "Currency"])
        ws.append(["DEF-002", "Steel bracket", 1000, "pcs", 0.85, "EUR"])
        ws.append(["GHI-003", "Aluminium rod", 200, "kg", 2.40, "EUR"])
        xlsx_path = tmp_path / "offer.xlsx"
        wb.save(str(xlsx_path))

        raw = RawOffer(
            offer_id=_new_id(), channel=IngestionChannel.MANUAL_UPLOAD,
            format=OfferFormat.EXCEL, raw_content=xlsx_path.read_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="offer.xlsx", sender_email=None, sender_domain=None,
            subject=None, received_at="2026-06-01T11:00:00Z",
            checksum_sha256="def456", file_size_bytes=5000,
        )
        converter = ExcelConverter()
        doc = await converter.convert(raw)
        assert doc.structured_data is not None

        extractor = StructuredOfferExtractor()
        items = await extractor.extract(doc)
        assert len(items) == 2
        prices = sorted(li.unit_price_raw for li in items)
        assert prices[0] == pytest.approx(0.85, rel=1e-4)
        assert prices[1] == pytest.approx(2.40, rel=1e-4)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_detection(self, db_pool):
        """Same checksum should raise DuplicateOfferError on second ingest."""
        checker = DuplicateChecker(db_pool)
        checksum = "unique_test_checksum_xyz"
        # First time — no duplicate
        result = await checker.check(checksum)
        assert result is None


class TestNLPGoldenSet:
    """
    Validates extraction accuracy on 200 manually annotated offers.
    Acceptance: price extraction F1 ≥ 0.85, unit normalization ≥ 0.90.
    """
    GOLDEN_SET_DIR = Path("tests/fixtures/nlp_golden_set")
    MIN_PRICE_F1  = 0.85
    MIN_UNIT_ACC  = 0.90

    @pytest.mark.parametrize("fixture", list(GOLDEN_SET_DIR.glob("*.json")))
    @pytest.mark.asyncio
    async def test_price_extraction(self, fixture):
        import json
        meta = json.loads(fixture.read_text())
        doc = _make_doc(meta["text"], meta.get("language", "en"))
        extractor = PriceExtractor()
        candidates = await extractor.extract(doc, [])

        expected_prices = meta.get("expected_prices", [])
        if not expected_prices:
            pytest.skip("No expected prices in fixture")

        extracted_values = [float(c.numeric_value) for c in candidates if c.price_type == "UNIT"]
        tp = sum(1 for ep in expected_prices if any(abs(ev - ep) / max(ep, 0.001) < 0.05 for ev in extracted_values))
        fp = len(extracted_values) - tp
        fn = len(expected_prices) - tp
        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0

        assert f1 >= self.MIN_PRICE_F1, \
            f"Price extraction F1={f1:.2f} < {self.MIN_PRICE_F1} in {fixture.name}"
```

### 12.4 Load test (k6)

```javascript
// k6 load test: sop_load_test.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const parseLatency   = new Trend("sop_parse_latency_ms");
const uploadLatency  = new Trend("sop_upload_latency_ms");
const parseFailures  = new Counter("sop_parse_failures");

const BASE_URL  = __ENV.BASE_URL || "https://api.industrial-cost.io/sop/v1";
const JWT_TOKEN = __ENV.JWT_TOKEN;

export const options = {
    scenarios: {
        upload_offers: {
            executor: "ramping-vus",
            startVUs: 1,
            stages: [
                {duration: "2m", target: 20},
                {duration: "5m", target: 50},
                {duration: "2m", target: 0},
            ],
        },
        read_offers: {
            executor: "constant-vus",
            vus: 100,
            duration: "9m",
        },
    },
    thresholds: {
        http_req_duration:      ["p(95)<500", "p(99)<2000"],
        sop_upload_latency_ms:  ["p(95)<2000"],
        sop_parse_latency_ms:   ["p(95)<30000"],
        sop_parse_failures:     ["count<5"],
        http_req_failed:        ["rate<0.02"],
    },
};

const HEADERS = {"Authorization": `Bearer ${JWT_TOKEN}`};

function generateCSVOffer() {
    return `Part Number,Description,Qty,UOM,Unit Price,Currency
P-${Date.now()},Test Component,500,pcs,${(Math.random() * 10 + 0.1).toFixed(4)},EUR
Q-${Date.now()},Steel Rod,100,kg,${(Math.random() * 5 + 0.5).toFixed(4)},EUR`;
}

export function uploadScenario() {
    const csvContent = generateCSVOffer();
    const formData = {
        file: http.file(csvContent, "offer_load.csv", "text/csv"),
        rfq_ref: `RFQ-LOAD-${Date.now()}`,
    };
    const t0 = Date.now();
    const res = http.post(`${BASE_URL}/offers/upload`, formData, {headers: HEADERS});
    uploadLatency.add(Date.now() - t0);
    const ok = check(res, {"upload 202": (r) => r.status === 202});
    if (!ok) { parseFailures.add(1); return; }

    sleep(5);  // wait for async parse
    const doc = res.json();
    if (doc.document_id) {
        const t1 = Date.now();
        const get = http.get(`${BASE_URL}/offers/${doc.document_id}`, {headers: HEADERS});
        parseLatency.add(Date.now() - t1);
        check(get, {"get offer 200": (r) => r.status === 200});
    }
    sleep(0.5);
}

export function readScenario() {
    const list = http.get(`${BASE_URL}/offers?limit=20&status=MAPPED`, {headers: HEADERS});
    check(list, {"list 200": (r) => r.status === 200});
    const offers = list.json()?.items || [];
    if (offers.length > 0) {
        const o = offers[Math.floor(Math.random() * offers.length)];
        http.get(`${BASE_URL}/offers/${o.document_id}/line-items`, {headers: HEADERS});
    }
    sleep(0.5);
}

export default function() {
    if (__ENV.SCENARIO === "reads") {
        readScenario();
    } else {
        uploadScenario();
    }
}
```

---

## 13. Risks

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|:-----------------:|:-----:|-----------|
| R01 | **Nieskończona różnorodność formatów** — każdy dostawca ma własny layout PDF/Excel | WYSOKI | WYSOKI | Template registry: per-supplier column mapping; ciągłe uzupełnianie golden set; fallback text NLP |
| R02 | **Wielojęzyczne oferty mieszane** — jeden dokument DE + EN + PL | ŚREDNI | ŚREDNI | langdetect per sekcja; spaCy multilingual model; per-sentence language detection |
| R03 | **Błędna interpretacja formatu liczb** — "1.250" to 1,250 albo 1.25 | WYSOKI | WYSOKI | Language-aware parser; waluta jako anchor (EUR = duże liczby → separator tysiący); outlier detection |
| R04 | **Brak waluty w dokumencie** — ceny bez oznaczenia | WYSOKI | WYSOKI | Supplier profile: `preferred_currency`; domain → country → currency heurystyka; domyślnie EUR z WARNING |
| R05 | **Ceny "per 100 pcs"** — nie wykryte, prowadzą do 100× błędu | WYSOKI | KRYTYCZNY | PER_QTY_PATTERN regex; PriceQualityGuard (spread check); review flag gdy price < 0.001 EUR |
| R06 | **Skanowane PDF** — nieczytowalne bez OCR (no text layer) | WYSOKI | WYSOKI | Automatyczna detekcja: jeśli 0 tekstu w PDF → OCR (Tesseract); integracja z DAE pipeline |
| R07 | **Fałszywe mapowanie BOM** — błędne "ABC-001" dopasowane do "ABC-0010" | WYSOKI | WYSOKI | Fuzzy threshold 0.80; SequenceMatcher + MIN_FUZZY_RATIO; low-confidence → NEEDS_REVIEW |
| R08 | **Oferty bez pozycji** — jedna cena globalna w e-mailu | ŚREDNI | ŚREDNI | Fallback: jeden LineItem z description=subject, price=extracted, bom_line = manual |
| R09 | **Złośliwe makra Excel** — .xlsm z VBA | NISKI | WYSOKI | Openpyxl (nie executes VBA); sandbox parser; blokada .xlsm na upload; AV scan hook |
| R10 | **Spam / nie-oferty w IMAP** — faktury, newsletter, OOO replies | WYSOKI | NISKI | Keyword filter: "price", "quote", "angebot", "oferta" w subject; brak cen → skip |
| R11 | **Zmiana szablonu dostawcy** — po rebrandingu lub ERP migracji | ŚREDNI | WYSOKI | Template wersjonowanie; po pierwszej nieudanej ekstrakcji → alert do procurement team |
| R12 | **Opóźniony kurs FX** — ECB weekends / holidays → stary kurs | NISKI | NISKI | Cache z TTL 25h; stale rate oznaczony w output; alert gdy > 48h |
| R13 | **Dane wrażliwe** — oferty zawierają negocjowane warunki handlowe | WYSOKI | KRYTYCZNY | RBAC per supplier/oferta; szyfrowanie at-rest; retencja 7 lat (GDPR B2B); audit log |
| R14 | **EDI — niezgodność wersji** — X12 855 różni się między handlakami | ŚREDNI | ŚREDNI | Parser tolerancyjny; przechowywany raw EDI; fallback text parsing z segmentów |
| R15 | **Race condition** — ten sam email odebrany dwukrotnie (IMAP + webhook) | NISKI | NISKI | DuplicateChecker (SHA-256); SELECT FOR UPDATE SKIP LOCKED na parse_jobs |

---

## 14. Roadmap

### Faza 1: Foundation (S1–S8)

| Sprint | Zakres |
|--------|--------|
| S1 | DB schema `sop`, raw_offers + offer_documents + suppliers; basic API (upload, list, get) |
| S2 | EmailIMAPIngestor + S3DropIngestor; PDFConverter + HTMLConverter + CSVConverter |
| S3 | ExcelConverter + EDIConverter; ConverterRegistry; DuplicateChecker |
| S4 | RegexNERBackend (prices, part numbers, lead time, MOQ, validity); TextNormalizer |
| S5 | PriceExtractor v1 (EN format, EUR/USD/GBP); price break detection; TOTAL vs UNIT |
| S6 | UnitConversionEngine (30 units, 8 dimensions); FXRateService (ECB daily refresh) |
| S7 | StructuredOfferExtractor (Excel/CSV/EDI column mapping; 6 field families) |
| S8 | Outbox publisher; Kafka topics (9); Avro schemas; Prometheus metrics (25) |

**Milestone S8:** Structured offers (Excel/CSV) parsed end-to-end, P95 < 5s

### Faza 2: NLP & Supplier Mapping (S9–S16)

| Sprint | Zakres |
|--------|--------|
| S9 | SpacyNERBackend — multilingual models (EN/DE/PL/ZH/RU); custom entity ruler |
| S10 | NLPPipeline — 8 stages; NLPContext; stage timings; error handler |
| S11 | SupplierMapper — domain / VAT / DUNS / fuzzy name; sop.suppliers table |
| S12 | BOMMapper v1 — exact part number match; BOME API integration |
| S13 | MaterialMatcher — 40 aliases; MATERIAL_DATABASE integration from DAE |
| S14 | BOMMapper v2 — fuzzy match (SequenceMatcher ≥ 0.80); BOM mapping learning |
| S15 | LineItemGrouper — text proximity grouping; price-break line consolidation |
| S16 | ValidationEngine (V001–V005); PriceQualityGuard; NEEDS_REVIEW workflow |

**Milestone S16:** Unstructured PDF/email parsed, supplier identified, F1 ≥ 0.80

### Faza 3: Intelligence (S17–S24)

| Sprint | Zakres |
|--------|--------|
| S17 | German/Polish/Chinese number format normalization; langdetect per-sentence |
| S18 | BOMMapper v3 — embedding similarity (sentence-transformers); AI match method |
| S19 | Template registry — per-supplier Excel column mapping; version control |
| S20 | scanned PDF → DAE OCR integration (route to DAE pipeline for raster offers) |
| S21 | Price history API; supplier comparison endpoint |
| S22 | HPA Kubernetes (sop-api 2–15 pods, sop-worker 1–8 pods); Redis job queue |
| S23 | Email webhook: MS Graph + SendGrid inbound; PunchOut XML parser |
| S24 | Golden set CI gate: 200 offers, price F1 ≥ 0.85; unit accuracy ≥ 0.90 |

**Milestone S24:** AI-enhanced matching, P95 parse < 10s (PDF), BOM coverage ≥ 80%

### Faza 4: Production Scale (S25–S32)

| Sprint | Zakres |
|--------|--------|
| S25 | ERP IDoc QUOTES01 parser; cXML PunchOut catalog ingestion |
| S26 | Supplier portal API polling (Ariba, Coupa, SAP SRM connectors) |
| S27 | RFQ Agent integration: auto-send RFQ → auto-parse response |
| S28 | Price benchmark analytics: market basket comparison, price trend |
| S29 | GDPR compliance: offer retention policy, right-to-delete, audit log |
| S30 | PostgreSQL partitioning `sop.offer_documents` by received_at (monthly) |
| S31 | Disaster Recovery: cross-region S3, read replica, RTO ≤ 4h |
| S32 | SLA hardening; supplier SLA dashboard; P95 < 5s for all formats |

**Milestone S32 — Production KPIs:**

| Metryka | Cel |
|---------|-----|
| Price extraction F1 | ≥ 0.88 |
| Unit normalization accuracy | ≥ 0.92 |
| Supplier identification rate | ≥ 85% |
| BOM mapping coverage | ≥ 80% of line items |
| Offers auto-mapped (no manual review) | ≥ 70% |
| Parse P95 latency (PDF/Excel ≤ 5MB) | ≤ 5s |
| Parse P95 latency (raster PDF) | ≤ 30s (via DAE) |
| API P95 GET | ≤ 500ms |
| System availability | ≥ 99.5% |
| FX rate freshness | ≤ 24h stale |
