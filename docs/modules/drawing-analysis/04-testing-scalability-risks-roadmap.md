# Drawing Analysis Engine — Sekcje 14–17

## 14. Testing

### 14.1 Strategia testów

| Typ testu | Framework | Zakres | Cel |
|-----------|-----------|--------|-----|
| Unit | pytest 8.x | Parsery, regex, geometry math, material lookup | Logika izolowana |
| Integration | pytest + Testcontainers | DB (PostgreSQL), S3 mock (MinIO), pipeline E2E | Integracja komponentów |
| OCR Quality | pytest + golden fixtures | Known drawings vs. ground truth | Regresja OCR accuracy |
| CV Model | pytest + torchtest | ViT, YOLO, DETR: forward pass + output shapes | Regresja modeli AI |
| API Contract | schemathesis | OpenAPI spec vs. live endpoints | Kontrakty API |
| Load | k6 | 50 RPS uploads, 200 RPS reads, 10 concurrent parses | Wydajność |
| Data Quality | Great Expectations | Extracted data: null rates, value ranges, confidence | Jakość danych |
| Security | bandit + OWASP ZAP | File upload validation, path traversal, injection | Bezpieczeństwo |

### 14.2 Unit tests

```python
import pytest
from decimal import Decimal
from pathlib import Path
import numpy as np


# ── Geometry Extractor ────────────────────────────────────────────────────────

class TestGeometryExtractor:

    def test_dimension_regex_linear(self):
        extractor = GeometryExtractor()
        words = [OCRWord("25.5", confidence=0.90, bbox=(0,0,50,20), page=0, line_id=1)]
        dims = extractor._extract_dims_from_ocr(words)
        assert any(d.dim_type == "LINEAR" and abs(d.value - 25.5) < 0.001 for d in dims)

    def test_dimension_regex_diameter(self):
        extractor = GeometryExtractor()
        words = [OCRWord("⌀12.0", confidence=0.88, bbox=(0,0,60,20), page=0, line_id=1)]
        dims = extractor._extract_dims_from_ocr(words)
        assert any(d.dim_type == "DIAMETER" and abs(d.value - 12.0) < 0.001 for d in dims)

    def test_dimension_with_tolerance(self):
        extractor = GeometryExtractor()
        words = [OCRWord("50±0.05", confidence=0.85, bbox=(0,0,80,20), page=0, line_id=1)]
        dims = extractor._extract_dims_from_ocr(words)
        dim = next((d for d in dims if abs(d.value - 50.0) < 0.001), None)
        assert dim is not None
        assert abs((dim.tolerance_upper or 0) - 0.05) < 0.001

    def test_bounding_box_empty(self):
        extractor = GeometryExtractor()
        bbox = extractor._compute_bbox([], [], [], [])
        assert bbox == (0, 0, 0, 0)

    def test_bounding_box_circles(self):
        extractor = GeometryExtractor()
        circles = [Circle2D(center=Point2D(10, 20), radius=5.0)]
        bbox = extractor._compute_bbox([], [], circles, [])
        assert bbox[0] == pytest.approx(5.0)
        assert bbox[1] == pytest.approx(15.0)
        assert bbox[2] == pytest.approx(15.0)
        assert bbox[3] == pytest.approx(25.0)


# ── Feature Detector ──────────────────────────────────────────────────────────

class TestFeatureDetector:

    @pytest.fixture
    def detector(self):
        return FeatureDetector(cv_model=None)

    def test_detect_thread_m10(self, detector):
        features = detector._detect_from_text("M10 x 1.5 - 6H THRU")
        threads = [f for f in features if f.feature_type == FeatureType.THREAD_INTERNAL]
        assert len(threads) == 1
        assert threads[0].parameters["nominal_diameter"] == pytest.approx(10.0)
        assert threads[0].parameters["pitch"] == pytest.approx(1.5)

    def test_detect_hole_thru(self, detector):
        features = detector._detect_from_text("⌀8.5 THRU 3x")
        holes = [f for f in features if f.feature_type == FeatureType.HOLE_THRU]
        assert any(abs(h.parameters["diameter"] - 8.5) < 0.01 for h in holes)

    def test_detect_hole_blind(self, detector):
        features = detector._detect_from_text("⌀6.0 BLIND 12.0")
        blind = [f for f in features if f.feature_type == FeatureType.HOLE_BLIND]
        assert len(blind) >= 1
        assert blind[0].parameters.get("depth") == pytest.approx(12.0)

    def test_detect_fillet(self, detector):
        features = detector._detect_from_text("R3.0 ALL FILLETS")
        fillets = [f for f in features if f.feature_type == FeatureType.FILLET]
        assert any(abs(f.parameters["radius"] - 3.0) < 0.01 for f in fillets)

    def test_detect_chamfer(self, detector):
        features = detector._detect_from_text("1.5x45° CHAM")
        chamfers = [f for f in features if f.feature_type == FeatureType.CHAMFER]
        assert len(chamfers) >= 1

    def test_detect_weld(self, detector):
        features = detector._detect_from_text("WELD ALL AROUND")
        welds = [f for f in features if f.feature_type == FeatureType.WELD_JOINT]
        assert len(welds) >= 1

    def test_iso_coarse_pitch_m8(self, detector):
        pitch = detector._iso_coarse_pitch(8.0)
        assert pitch == pytest.approx(1.25)

    def test_deduplicate_same_diameter(self, detector):
        features = [
            DetectedFeature(FeatureType.HOLE_THRU, None, {"diameter": 10.0}, 1, 0.85, "OCR_REGEX"),
            DetectedFeature(FeatureType.HOLE_THRU, None, {"diameter": 10.0}, 1, 0.80, "GEOMETRY"),
        ]
        deduped = detector._deduplicate(features)
        assert len(deduped) == 1
        assert deduped[0].count == 2


# ── Tolerance Parser ──────────────────────────────────────────────────────────

class TestToleranceParser:

    @pytest.fixture
    def parser(self):
        return ToleranceParser()

    def test_bilateral_symmetric(self, parser):
        tols = parser._parse_bilateral("Dim = 25.0 ±0.05 mm")
        assert len(tols) >= 1
        t = tols[0]
        assert t.nominal == pytest.approx(25.0)
        assert t.upper_dev == pytest.approx(0.05)
        assert t.lower_dev == pytest.approx(-0.05)

    def test_bilateral_asymmetric(self, parser):
        tols = parser._parse_bilateral("100.0 +0.10/-0.05")
        assert len(tols) >= 1
        t = tols[0]
        assert t.upper_dev == pytest.approx(0.10)
        assert t.lower_dev == pytest.approx(-0.05)

    def test_iso_2768_medium(self, parser):
        tols = parser._parse_iso_2768("ISO 2768-m")
        assert len(tols) >= 1
        assert tols[0].iso_2768_class == "m"
        assert tols[0].upper_dev == pytest.approx(0.10)

    def test_it_grade_extraction(self, parser):
        tols = parser._parse_it_grade("Bore tolerance IT7")
        assert any(t.it_grade == "IT7" for t in tols)

    def test_surface_finish_ra(self, parser):
        tols = parser._parse_surface_finish("Ra 1.6 μm max.")
        assert len(tols) >= 1
        assert tols[0].surface_ra == pytest.approx(1.6)

    def test_surface_finish_rz(self, parser):
        tols = parser._parse_surface_finish("Rz = 6.3 um")
        assert len(tols) >= 1
        assert tols[0].surface_rz == pytest.approx(6.3)

    def test_gdt_perpendicularity(self, parser):
        tols = parser._parse_gdt_frames("⊥ 0.05 | A")
        assert len(tols) >= 1
        t = tols[0]
        assert t.gdt_symbol == GDTSymbol.PERPENDICULARITY
        assert t.gdt_value == pytest.approx(0.05)
        assert "A" in t.datum_refs


# ── Material Inferencer ───────────────────────────────────────────────────────

class TestMaterialInferencer:

    @pytest.fixture
    def inferencer(self):
        return MaterialInferencer()

    def test_exact_match_s235jr(self, inferencer):
        result = inferencer._lookup("S235JR")
        assert result is not None
        assert result["designation"] == "S235JR"

    def test_alias_match_aisi304(self, inferencer):
        result = inferencer._lookup("AISI 304")
        assert result is not None
        assert "1.4301" in result["designation"] or "304" in str(result.get("aliases", []))

    def test_to_candidate_confidence(self, inferencer):
        mat = _MAT_INDEX["S235JR"]
        cand = inferencer._to_candidate(mat, confidence=0.92)
        assert cand.confidence == pytest.approx(0.92)
        assert cand.family == MaterialFamily.STEEL_CARBON
        assert cand.density_kg_m3 == 7850

    @pytest.mark.asyncio
    async def test_infer_from_title_block(self, inferencer):
        tb = TitleBlock(
            part_number="P001", part_name="Shaft",
            revision="A", material="42CrMo4",
            surface_finish=None, scale="1:1",
            confidence=0.88,
        )
        result = await inferencer.infer(tb, [], [], [])
        assert result is not None
        assert result.best_candidate.designation == "42CrMo4"
        assert result.confidence >= 0.85
        assert result.confidence_level == MaterialConfidence.HIGH

    @pytest.mark.asyncio
    async def test_infer_unknown_material(self, inferencer):
        result = await inferencer.infer(None, [], [], [])
        assert result is None


# ── Title Block Extractor ─────────────────────────────────────────────────────

class TestTitleBlockExtractor:

    @pytest.fixture
    def extractor(self):
        return TitleBlockExtractor()

    def test_extract_part_number(self, extractor):
        elements = [
            {"text": "PART NO: ABC-12345", "size": 10},
            {"text": "Scale: 1:2", "size": 8},
        ]
        tb = extractor.extract(elements)
        assert tb.part_number == "ABC-12345"

    def test_extract_scale(self, extractor):
        elements = [{"text": "SCALE 1:5", "size": 9}]
        tb = extractor.extract(elements)
        assert tb.scale is not None
        assert "1" in tb.scale and "5" in tb.scale

    def test_extract_material(self, extractor):
        elements = [
            {"text": "MATERIAL: S355J2", "size": 9},
        ]
        tb = extractor.extract(elements)
        assert tb.material == "S355J2"

    def test_detect_projection_first_angle(self, extractor):
        proj = extractor._detect_projection("ISO first angle projection")
        assert proj == "1st angle"

    def test_detect_projection_third_angle(self, extractor):
        proj = extractor._detect_projection("ANSI 3rd angle projection")
        assert proj == "3rd angle"


# ── Preprocessor ─────────────────────────────────────────────────────────────

class TestDrawingPreprocessor:

    def test_assess_quality_high_contrast(self):
        preprocessor = DrawingPreprocessor()
        img = np.zeros((500, 500), dtype=np.uint8)
        img[100:400, 100:400] = 255  # high contrast
        score = preprocessor._assess_quality(img, dpi=300)
        assert score > 0.4

    def test_assess_quality_low_dpi(self):
        preprocessor = DrawingPreprocessor()
        img = np.full((200, 200), 128, dtype=np.uint8)
        score = preprocessor._assess_quality(img, dpi=72)
        assert score < 0.5

    def test_detect_format_pdf(self):
        pipeline = DrawingParsingPipeline.__new__(DrawingParsingPipeline)
        fmt = pipeline._detect_format(Path("drawing.pdf"))
        assert fmt == DrawingFormat.PDF

    def test_detect_format_step(self):
        pipeline = DrawingParsingPipeline.__new__(DrawingParsingPipeline)
        fmt = pipeline._detect_format(Path("part.stp"))
        assert fmt == DrawingFormat.STEP
```

### 14.3 Integration tests

```python
import pytest
from testcontainers.postgres import PostgresContainer
from testcontainers.minio import MinioContainer


@pytest.fixture(scope="session")
def pg_container():
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def minio_container():
    with MinioContainer() as minio:
        yield minio


@pytest.fixture(scope="session")
async def db_pool(pg_container):
    import asyncpg
    dsn = pg_container.get_connection_url().replace("postgresql+psycopg2", "postgresql")
    pool = await asyncpg.create_pool(dsn)
    # Apply schema
    async with pool.acquire() as conn:
        schema_sql = (Path(__file__).parent / "../../sql/dae_schema.sql").read_text()
        await conn.execute(schema_sql)
    yield pool
    await pool.close()


class TestDrawingUploadPipeline:

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_upload_and_parse_dxf(self, db_pool, tmp_path):
        """Full pipeline: upload DXF → parse → verify DB state."""
        import ezdxf
        # Create minimal DXF
        doc = ezdxf.new()
        msp = doc.modelspace()
        msp.add_line((0, 0), (100, 0))
        msp.add_circle((50, 50), radius=10)
        dxf_path = tmp_path / "test.dxf"
        doc.saveas(str(dxf_path))

        drawing_id = await _upload_drawing(db_pool, dxf_path, "TEST-001")

        # Wait for parse (synchronous for test)
        pipeline = _build_test_pipeline()
        result = await pipeline.process(str(drawing_id), dxf_path)

        assert result.overall_confidence > 0.0
        assert len(result.errors) == 0

        # Verify DB
        async with db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT parse_status, overall_confidence FROM dae.drawings WHERE drawing_id = $1",
                drawing_id,
            )
        assert row["parse_status"] in ("DONE", "PARTIAL")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_duplicate_detection(self, db_pool, tmp_path):
        """Same file uploaded twice should return DUPLICATE status."""
        content = b"PDF mock content"
        path = tmp_path / "dup.pdf"
        path.write_bytes(content)

        id1 = await _upload_drawing(db_pool, path, "P001")
        response = await _try_upload(db_pool, path, "P001")

        assert response.get("status") == "DUPLICATE"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_outbox_publishes_event(self, db_pool, tmp_path):
        """Verify outbox event is created when parse completes."""
        path = tmp_path / "event_test.dxf"
        path.write_bytes(b"DXF mock")
        drawing_id = await _upload_drawing(db_pool, path, "EVT-001")

        async with db_pool.acquire() as conn:
            # Simulate status update
            await conn.execute(
                "UPDATE dae.drawings SET parse_status = 'DONE' WHERE drawing_id = $1",
                drawing_id,
            )
            events = await conn.fetch(
                "SELECT topic FROM dae.outbox_events WHERE key = $1",
                str(drawing_id),
            )
        assert any(e["topic"] == "dae.drawing.parsed" for e in events)


class TestMaterialInferenceIntegration:

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_infer_and_store(self, db_pool):
        import uuid
        drawing_id = uuid.uuid4()
        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dae.drawings
                    (drawing_id, format, file_size_bytes, checksum_sha256,
                     filename, storage_uri, uploaded_by)
                VALUES ($1, 'DXF', 1000, 'abc123', 'test.dxf', '/tmp/test.dxf', 'tester')
                """,
                drawing_id,
            )

        tb = TitleBlock(
            part_number="X", part_name="Y", revision="A",
            material="S235JR", surface_finish=None, scale=None, confidence=0.9,
        )
        inferencer = MaterialInferencer()
        result = await inferencer.infer(tb, [], [], [])

        async with db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO dae.material_inferences
                    (drawing_id, designation, family, confidence, confidence_level, inference_sources)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                drawing_id,
                result.best_candidate.designation,
                result.best_candidate.family.value,
                result.confidence,
                result.confidence_level.value,
                result.inference_sources,
            )
            row = await conn.fetchrow(
                "SELECT designation FROM dae.material_inferences WHERE drawing_id = $1",
                drawing_id,
            )
        assert row["designation"] == "S235JR"
```

### 14.4 OCR Golden Set Tests

```python
import json
from pathlib import Path


GOLDEN_SET_DIR = Path("tests/fixtures/ocr_golden_set")


class TestOCRGoldenSet:
    """
    Compares OCR extraction against manually annotated ground truth.
    Golden set: 100 drawings (25 PDF raster, 25 DXF, 25 TIFF, 25 PDF vector).
    Acceptance: title block extraction F1 ≥ 0.85, dimension extraction F1 ≥ 0.75.
    """

    @pytest.fixture(scope="class")
    def ocr_engine(self):
        return OCREngine(use_cloud=False)

    @pytest.mark.parametrize("fixture", list(GOLDEN_SET_DIR.glob("*.json")))
    @pytest.mark.asyncio
    async def test_golden_title_block(self, ocr_engine, fixture):
        meta = json.loads(fixture.read_text())
        image_path = GOLDEN_SET_DIR / meta["image"]
        ground_truth = meta["title_block"]

        preprocessor = DrawingPreprocessor()
        preprocessed = await preprocessor.process(Path(image_path), DrawingFormat.PNG)
        ocr_result = await ocr_engine.run(preprocessed)
        extractor = TitleBlockExtractor()
        tb = extractor.extract([{"text": w.text, "size": 10} for w in ocr_result.words])

        if ground_truth.get("part_number"):
            assert tb.part_number is not None, f"Missing part_number in {fixture.name}"
            assert ground_truth["part_number"] in (tb.part_number or ""), \
                f"Part number mismatch in {fixture.name}: got {tb.part_number}"

        if ground_truth.get("material"):
            assert tb.material is not None, f"Missing material in {fixture.name}"

    @pytest.mark.asyncio
    async def test_ocr_confidence_floor(self, ocr_engine, tmp_path):
        """Minimum acceptable mean OCR confidence on clean 300 DPI scan."""
        img = np.full((500, 500), 255, dtype=np.uint8)
        # Draw synthetic text region (simulated)
        cv2.putText(img, "PART NO: ABC-123", (50, 100), cv2.FONT_HERSHEY_SIMPLEX, 1, 0, 2)
        path = tmp_path / "synth.png"
        cv2.imwrite(str(path), img)

        preprocessor = DrawingPreprocessor()
        preprocessed = await preprocessor.process(path, DrawingFormat.PNG)
        result = await ocr_engine.run(preprocessed)

        assert result.mean_confidence >= 0.60, \
            f"OCR confidence {result.mean_confidence:.3f} below floor 0.60"
```

### 14.5 Load test (k6)

```javascript
// k6 load test: drawing_analysis_load.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const parseLatency     = new Trend("dae_parse_latency_ms");
const uploadLatency    = new Trend("dae_upload_latency_ms");
const jobPollLatency   = new Trend("dae_job_poll_latency_ms");
const parseFailures    = new Counter("dae_parse_failures");

const BASE_URL = __ENV.BASE_URL || "https://api.industrial-cost.io/dae/v1";
const JWT_TOKEN = __ENV.JWT_TOKEN;

export const options = {
    scenarios: {
        upload_ramp: {
            executor: "ramping-vus",
            startVUs: 1,
            stages: [
                {duration: "2m", target: 20},
                {duration: "5m", target: 50},
                {duration: "2m", target: 0},
            ],
        },
        steady_reads: {
            executor: "constant-vus",
            vus: 100,
            duration: "9m",
        },
    },
    thresholds: {
        http_req_duration:        ["p(95)<500", "p(99)<1000"],
        dae_upload_latency_ms:    ["p(95)<3000"],
        dae_parse_latency_ms:     ["p(95)<60000"],  // 60s for full parse
        dae_job_poll_latency_ms:  ["p(95)<200"],
        dae_parse_failures:       ["count<10"],
        http_req_failed:          ["rate<0.02"],
    },
};

const HEADERS = {
    "Authorization": `Bearer ${JWT_TOKEN}`,
    "Content-Type": "multipart/form-data",
};

function generateMinimalDXF() {
    return `0
SECTION
2
ENTITIES
0
LINE
8
0
10
0.0
20
0.0
30
0.0
11
100.0
21
0.0
31
0.0
0
ENDSEC
0
EOF`;
}

export function uploadScenario() {
    const dxfContent = generateMinimalDXF();
    const formData = {
        file: http.file(dxfContent, "test_load.dxf", "application/dxf"),
        part_number: `LOAD-TEST-${Date.now()}`,
        priority: "5",
    };

    const t0 = Date.now();
    const uploadRes = http.post(`${BASE_URL}/drawings/upload`, formData, {headers: HEADERS});
    uploadLatency.add(Date.now() - t0);

    const ok = check(uploadRes, {
        "upload 202": (r) => r.status === 202,
    });
    if (!ok) { parseFailures.add(1); return; }

    const { job_id } = uploadRes.json();
    if (!job_id) return;

    // Poll job until done or timeout
    const timeout = Date.now() + 90_000;
    while (Date.now() < timeout) {
        sleep(2);
        const t1 = Date.now();
        const jobRes = http.get(`${BASE_URL}/jobs/${job_id}`, {headers: HEADERS});
        jobPollLatency.add(Date.now() - t1);

        if (jobRes.status !== 200) continue;
        const job = jobRes.json();
        if (["DONE", "FAILED", "PARTIAL"].includes(job.status)) {
            parseLatency.add(Date.now() - t0);
            if (job.status === "FAILED") parseFailures.add(1);
            return;
        }
    }
    parseFailures.add(1);  // timeout
}

export function readScenario() {
    const listRes = http.get(`${BASE_URL}/drawings?limit=20`, {headers: HEADERS});
    check(listRes, {"list 200": (r) => r.status === 200});

    const drawings = listRes.json()?.items || [];
    if (drawings.length === 0) { sleep(0.5); return; }
    const drawing = drawings[Math.floor(Math.random() * drawings.length)];

    http.get(`${BASE_URL}/drawings/${drawing.drawing_id}`, {headers: HEADERS});
    http.get(`${BASE_URL}/drawings/${drawing.drawing_id}/features`, {headers: HEADERS});
    http.get(`${BASE_URL}/drawings/${drawing.drawing_id}/material`, {headers: HEADERS});
    sleep(0.5);
}

export default function () {
    if (__ENV.SCENARIO === "reads") {
        readScenario();
    } else {
        uploadScenario();
    }
}
```

---

## 15. Scalability

### 15.1 Poziomy skalowalności

| Poziom | Wolumen (drawings/day) | Strategia | Infrastruktura |
|--------|----------------------|-----------|----------------|
| L1 | ≤ 500 | Single worker, synchronous pipeline | 1 pod, GPU optional |
| L2 | ≤ 5 000 | Async workers, job queue, GPU inference | 3–8 API pods, 2–5 GPU workers |
| L3 | ≤ 50 000 | HPA, Redis job queue, model serving (Triton) | 5–20 API pods, 5–20 GPU workers |
| L4 | > 50 000 | Multi-region, partitioned storage, streaming | Kubernetes cluster per region |

### 15.2 Architektura worker pool

```python
import asyncio
from dataclasses import dataclass
from typing import Optional
import structlog

log = structlog.get_logger()


@dataclass
class WorkerStats:
    worker_id: str
    jobs_processed: int = 0
    jobs_failed: int = 0
    avg_duration_ms: float = 0.0
    current_job_id: Optional[str] = None


class ParseWorkerPool:
    """
    Async worker pool for drawing parse jobs.
    Workers pick from dae.parse_jobs (FOR UPDATE SKIP LOCKED).
    Scales via Kubernetes HPA on dae_parse_queue_depth metric.
    """
    POLL_INTERVAL_S = 1.0
    JOB_TIMEOUT_S   = 300   # 5 min hard limit per job

    def __init__(self, db_pool, pipeline: DrawingParsingPipeline, concurrency: int = 4):
        self.db_pool   = db_pool
        self.pipeline  = pipeline
        self.concurrency = concurrency
        self._semaphore = asyncio.Semaphore(concurrency)
        self._stats: dict[str, WorkerStats] = {}

    async def run(self):
        while True:
            job = await self._claim_job()
            if job:
                asyncio.create_task(self._process(job))
            else:
                await asyncio.sleep(self.POLL_INTERVAL_S)

    async def _claim_job(self) -> Optional[dict]:
        async with self.db_pool.acquire() as conn:
            return await conn.fetchrow(
                """
                UPDATE dae.parse_jobs
                SET status = 'PREPROCESSING',
                    worker_id = $1,
                    started_at = now()
                WHERE job_id = (
                    SELECT job_id FROM dae.parse_jobs
                    WHERE status = 'QUEUED'
                    ORDER BY priority, queued_at
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING job_id, drawing_id, priority
                """,
                f"worker-{id(self)}",
            )

    async def _process(self, job: dict):
        async with self._semaphore:
            job_id = str(job["job_id"])
            drawing_id = str(job["drawing_id"])
            t0 = asyncio.get_event_loop().time()
            try:
                storage_uri = await self._get_storage_uri(drawing_id)
                result = await asyncio.wait_for(
                    self.pipeline.process(drawing_id, Path(storage_uri)),
                    timeout=self.JOB_TIMEOUT_S,
                )
                await self._save_result(drawing_id, job_id, result)
                dae_parse_status_total.labels(status="DONE", format=result.format.value).inc()
            except asyncio.TimeoutError:
                await self._fail_job(job_id, drawing_id, "Parse timeout")
                dae_parse_status_total.labels(status="FAILED", format="UNKNOWN").inc()
            except Exception as e:
                await self._fail_job(job_id, drawing_id, str(e))
                log.error("worker_error", job_id=job_id, error=str(e), exc_info=True)
            finally:
                dae_parse_duration_seconds.labels(
                    format="UNKNOWN", status="DONE"
                ).observe((asyncio.get_event_loop().time() - t0))

    async def _get_storage_uri(self, drawing_id: str) -> str:
        async with self.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT storage_uri FROM dae.drawings WHERE drawing_id = $1",
                drawing_id,
            )
            if not row:
                raise ValueError(f"Drawing {drawing_id} not found")
            return row["storage_uri"]

    async def _save_result(self, drawing_id: str, job_id: str, result: DrawingParseResult):
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE dae.drawings SET
                        parse_status = 'DONE',
                        overall_confidence = $2,
                        processing_time_ms = $3,
                        ocr_engine_used = $4,
                        warnings = $5,
                        errors = $6,
                        parsed_at = now()
                    WHERE drawing_id = $1
                    """,
                    drawing_id,
                    result.overall_confidence,
                    result.processing_time_ms,
                    None,   # populated from OCR stage
                    result.warnings or [],
                    result.errors or [],
                )
                await conn.execute(
                    "UPDATE dae.parse_jobs SET status = 'DONE', finished_at = now() WHERE job_id = $1",
                    job_id,
                )

    async def _fail_job(self, job_id: str, drawing_id: str, error: str):
        async with self.db_pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    "SELECT retry_count, max_retries FROM dae.parse_jobs WHERE job_id = $1",
                    job_id,
                )
                if row and row["retry_count"] < row["max_retries"]:
                    await conn.execute(
                        """
                        UPDATE dae.parse_jobs
                        SET status = 'QUEUED', retry_count = retry_count + 1,
                            error_message = $2, worker_id = NULL, started_at = NULL
                        WHERE job_id = $1
                        """,
                        job_id, error,
                    )
                else:
                    await conn.execute(
                        """
                        UPDATE dae.parse_jobs
                        SET status = 'FAILED', error_message = $2, finished_at = now()
                        WHERE job_id = $1
                        """,
                        job_id, error,
                    )
                    await conn.execute(
                        "UPDATE dae.drawings SET parse_status = 'FAILED' WHERE drawing_id = $1",
                        drawing_id,
                    )
```

### 15.3 GPU Model Serving — NVIDIA Triton

```yaml
# Triton Inference Server configuration for DAE models
# triton/models/feature_detector/config.pbtxt

name: "feature_detector"
platform: "tensorrt_plan"
max_batch_size: 8
input [
  {
    name: "images"
    data_type: TYPE_FP32
    dims: [3, 640, 640]
  }
]
output [
  {
    name: "output0"
    data_type: TYPE_FP32
    dims: [-1, 5]
  }
]
instance_group [
  {
    count: 2
    kind: KIND_GPU
    gpus: [0]
  }
]
dynamic_batching {
  preferred_batch_size: [2, 4, 8]
  max_queue_delay_microseconds: 100
}
```

### 15.4 HPA configuration

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: dae-api-hpa
  namespace: dae
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: dae-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: External
      external:
        metric:
          name: dae_parse_queue_depth
        target:
          type: Value
          value: "100"   # 1 replica per 100 queued jobs
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 3
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: dae-gpu-worker-hpa
  namespace: dae
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: dae-gpu-worker
  minReplicas: 1
  maxReplicas: 10
  metrics:
    - type: External
      external:
        metric:
          name: dae_parse_queue_depth
        target:
          type: Value
          value: "50"
```

### 15.5 File storage — S3-compatible (MinIO / AWS S3)

```python
import aioboto3
from botocore.exceptions import ClientError


class DrawingStorageService:
    """
    S3-compatible object storage for drawing files.
    Local: MinIO. Production: AWS S3 with intelligent tiering.
    """
    BUCKET = "dae-drawings"
    PRESIGN_EXPIRY_S = 3600

    def __init__(self, endpoint_url: str, access_key: str, secret_key: str):
        self.session = aioboto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        self.endpoint_url = endpoint_url

    async def upload(self, drawing_id: str, filename: str, content: bytes) -> str:
        key = f"{drawing_id}/{filename}"
        async with self.session.client("s3", endpoint_url=self.endpoint_url) as s3:
            await s3.put_object(
                Bucket=self.BUCKET,
                Key=key,
                Body=content,
                ServerSideEncryption="AES256",
            )
        return f"s3://{self.BUCKET}/{key}"

    async def download(self, storage_uri: str) -> bytes:
        key = storage_uri.replace(f"s3://{self.BUCKET}/", "")
        async with self.session.client("s3", endpoint_url=self.endpoint_url) as s3:
            response = await s3.get_object(Bucket=self.BUCKET, Key=key)
            return await response["Body"].read()

    async def presign_url(self, storage_uri: str) -> str:
        key = storage_uri.replace(f"s3://{self.BUCKET}/", "")
        async with self.session.client("s3", endpoint_url=self.endpoint_url) as s3:
            return await s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.BUCKET, "Key": key},
                ExpiresIn=self.PRESIGN_EXPIRY_S,
            )
```

---

## 16. Risks

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|-------------------|-------|-----------|
| R01 | **Niska jakość rasterów** — skanowane rysunki poniżej 150 DPI, niedostępne dla OCR | WYSOKI | WYSOKI | Pre-flight DPI check → odrzucenie z komunikatem; wskazówki do re-skanowania; minimalny próg 150 DPI |
| R02 | **Nieznane notacje wymiaru** — proprietary symbole, stare normy (GOST, GB) | ŚREDNI | ŚREDNI | Wielojęzyczny Tesseract + PaddleOCR; rozszerzalny regex; fallback do cloud OCR |
| R03 | **DWG format binarny** — Autodesk nie publikuje specyfikacji | WYSOKI | ŚREDNI | ezdxf obsługuje DWG do 2018; starsza/nowsza wersja = fallback do eksportu DXF przez użytkownika |
| R04 | **STEP/IGES bez 2D projekcji** — rysunek 3D bez widoków i wymiarów | WYSOKI | WYSOKI | STEP pipeline dostarcza tylko topologię 3D (hole/pocket detection); 2D wymiary niedostępne — dokumentuj to ograniczenie |
| R05 | **Latencja AI modeli** — YOLO + DETR + ViT > 10s dla dużych PDF | ŚREDNI | ŚREDNI | GPU inference (Triton, FP16); batching; modele opcjonalne — pipeline działa bez nich (degraded) |
| R06 | **Fałszywe wymiary** — OCR myli "0" z "O", "1" z "I" | ŚREDNI | WYSOKI | Custom Tesseract charlist; confidence threshold ≥0.75; outlier detection (wartość > 10× mediana) |
| R07 | **Brak znormalizowanej nomenklatury materiałów** — "Stahl 42CrMo4", "steel 4140", "1.7225" to ten sam materiał | WYSOKI | ŚREDNI | Alias dictionary w MATERIAL_DATABASE; NLP BERT NER; fallback `material_raw` bez normalizacji |
| R08 | **Prawa autorskie / IP rysunków** — rysunki klientów przechowywane w chmurze | WYSOKI | KRYTYCZNY | Szyfrowanie AES-256 at-rest i in-transit; tenant isolation (osobny bucket per klient); polityka retencji; prawo do usunięcia |
| R09 | **Wielostronicowe PDF** — rysunek na stronie 7 z 20 stron | ŚREDNI | NISKI | Przetwarzanie każdej strony; BOM extraction ze specyficznych stron; konfigurowalny range stron |
| R10 | **GD&T fonty niestandardowe** — symbole jako obrazki (raster insert) zamiast tekstu | WYSOKI | ŚREDNI | YOLO model wykrywa GD&T frame visually; fallback gdy brak tekstu |
| R11 | **Skala rysunku** — wymiary podane w calach, brak adnotacji jednostki | ŚREDNI | WYSOKI | Unit detection z title block; heurystyki (wartości > 1000 = likely mm, < 10 = likely inches); ostrzeżenie w output |
| R12 | **Zniszczone/sfałdowane oryginały** — rysunek papierowy zdegradowany | NISKI | WYSOKI | Jakość score < 0.4 → PARTIAL status; użytkownik informowany; nie blokuje ingestii |
| R13 | **Zmiana modeli AI** — nowa wersja modelu regresuje na edge cases | ŚREDNI | WYSOKI | Golden set 100 rysunków; CI blokuje promotion jeśli F1 spada; A/B testowanie |
| R14 | **Atak file upload** — złośliwy plik (zip bomb, path traversal) | NISKI | KRYTYCZNY | Whitelist rozszerzeń; magic bytes check; max size limit; sandbox parsing (gVisor); path sanitization |
| R15 | **Skalowanie GPU** — brak GPU w środowisku dev/test | ŚREDNI | NISKI | CPU fallback dla wszystkich modeli; Triton mock w testach; GPU optional per model |

---

## 17. Roadmap

### Faza 1: Foundation (S1–S10)

| Sprint | Zakres |
|--------|--------|
| S1 | DB schema `dae`, basic API (upload, list, get), file storage (MinIO) |
| S2 | DrawingPreprocessor (deskew, denoise, DPI normalization) |
| S3 | DXF/DWG parser (ezdxf), GeometryExtractor — lines/circles/arcs |
| S4 | Tesseract OCR integration, TitleBlockExtractor |
| S5 | ToleranceParser (bilateral, ISO 2768, IT grade, surface finish) |
| S6 | FeatureDetector rule-based (regex: threads, holes, fillets, chamfers) |
| S7 | MaterialInferencer v1 (exact match + alias, MATERIAL_DATABASE 30 entries) |
| S8 | PDF parser (PyMuPDF vector + raster), ParseWorkerPool (4 workers) |
| S9 | Outbox publisher, Kafka topics (7 topics), Avro schemas |
| S10 | Prometheus metrics (25), Grafana dashboard (Overview + OCR), Alertmanager (7 rules) |

**Milestone S10:** Pełny pipeline DXF/PDF → DB, P95 parse ≤ 30s dla < 5MB

### Faza 2: AI Models (S11–S20)

| Sprint | Zakres |
|--------|--------|
| S11 | DrawingClassifier ViT-B/16 — training dataset curation (10k rysunków) |
| S12 | DrawingClassifier training + evaluation (mAP50 ≥ 0.80), integration |
| S13 | YOLOv8-seg FeatureDetector — annotation pipeline (5k rysunków, 15 klas) |
| S14 | YOLOv8 training + hyperparameter tuning (mAP50 ≥ 0.75) |
| S15 | DETR DimensionDetector — localization training (8k dimonsion-annotated) |
| S16 | DimNet regressor (CNN OCR for numeric values from crops) |
| S17 | BERT NER MaterialClassifier — multilingual fine-tuning (EN/DE/PL/ZH) |
| S18 | Triton Inference Server — model serving (TRT FP16, dynamic batching) |
| S19 | PaddleOCR integration, cloud OCR fallback (Google DocumentAI) |
| S20 | DAEModelRegistry, promotion thresholds, golden set CI gate (100 rysunków) |

**Milestone S20:** AI-enhanced pipeline, feature detection F1 ≥ 0.78, OCR mean confidence ≥ 0.80

### Faza 3: STEP + Scale (S21–S30)

| Sprint | Zakres |
|--------|--------|
| S21 | STEP/IGES parser (pythonOCC), STEPFeatureDetector (cylindrical faces) |
| S22 | B-Rep face classification — pocket/boss detection from STEP topology |
| S23 | GD&T frame visual detector (CNN+CRF for feature control frames) |
| S24 | HPA Kubernetes — API 2–20 pods, GPU workers 1–10 pods |
| S25 | Redis job queue migration (Celery + Redis) dla L3 wolumenów |
| S26 | S3 multi-tenant isolation (per-client buckets, encryption) |
| S27 | PostgreSQL partitioning `dae.drawings` by `uploaded_at` (monthly) |
| S28 | Search API — full-text + JSONB query (material, feature_type, tolerance) |
| S29 | Analytics dashboards — material distribution, feature frequency, confidence report |
| S30 | SLA hardening: retry logic, circuit breaker, graceful degradation |

**Milestone S30:** L3 scale (50k drawings/day), P95 parse ≤ 60s, API P95 ≤ 500ms

### Faza 4: Intelligence (S31–S40)

| Sprint | Zakres |
|--------|--------|
| S31 | Active learning pipeline — low-confidence samples → annotation queue |
| S32 | Continuous model retraining (weekly, triggered by confidence drift) |
| S33 | BOME integration — auto-link parsed drawing to BOM line (part_number match) |
| S34 | CEE integration — material properties → cost estimate enrichment |
| S35 | Sheet metal feature extraction — bend radius, k-factor, flat pattern area |
| S36 | Casting feature extraction — draft angles, parting line, core prints |
| S37 | Multi-page BOM extraction — assembly BOM from drawing title block |
| S38 | Drawing comparison API (diff two revisions: dimension changes, added features) |
| S39 | Disaster Recovery — cross-region S3 replication, standby DB, RTO ≤ 4h |
| S40 | ITAR/EAR data classification integration; export control annotation on drawings |

**Milestone S40 — Production KPIs:**

| Metryka | Cel |
|---------|-----|
| Title block extraction F1 | ≥ 0.90 |
| Dimension extraction F1 | ≥ 0.80 |
| Feature detection mAP50 | ≥ 0.82 |
| Material inference accuracy | ≥ 0.85 |
| OCR mean confidence | ≥ 0.80 |
| Parse P95 latency (PDF/DXF ≤ 10MB) | ≤ 30s |
| Parse P95 latency (STEP ≤ 100MB) | ≤ 120s |
| API P95 response (GET) | ≤ 500ms |
| Parse pipeline availability | ≥ 99.5% |
| Daily drawing throughput (L3) | ≥ 50 000 |
