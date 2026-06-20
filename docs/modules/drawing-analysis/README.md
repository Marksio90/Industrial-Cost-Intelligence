# Drawing Analysis Engine (DAE)

System analizy rysunków technicznych i plików CAD dla platformy
Industrial Cost Intelligence. Przetwarza PDF, DXF, DWG, STEP/IGES i skany rastrowe
(PNG/TIFF/JPEG) — ekstrahuje wymiary, tolerancje, cechy wytwarzania i materiały
przy użyciu kombinacji reguł geometrycznych, OCR oraz modeli Computer Vision.

## Dokumentacja

| Plik | Zawartość |
|------|-----------|
| [01-input-formats-parsing-ocr-geometry-features.md](./01-input-formats-parsing-ocr-geometry-features.md) | Input Formats (PDF/DXF/DWG/STEP/IGES/Raster, format matrix, limity), Parsing Pipeline (DrawingParsingPipeline 8 stages, DrawingPreprocessor deskew+denoise, FormatParserRegistry), OCR Engine (TesseractBackend, PaddleOCRBackend, DocumentAI fallback, TitleBlockExtractor), Geometry Extraction (Line2D/Arc2D/Circle2D, DXF entity parsing, OCR dimension regex), Feature Detection (FeatureDetector rule-based + STEPFeatureDetector 3D) |
| [02-material-inference-tolerance-sql-api.md](./02-material-inference-tolerance-sql-api.md) | Material Inference (MaterialInferencer, MATERIAL_DATABASE 12 materiałów, alias lookup, surface finish hints, tolerance tightness signal), Tolerance Parsing (ToleranceParser, bilateral ±, ISO 2768, IT grade, GD&T frames, Ra/Rz), SQL Schema PostgreSQL 16 (schemat `dae`, 8 ENUMów, 8 tabel, triggery, widok v_drawing_summary), OpenAPI 3.1 (18 endpointów, 4 role RBAC) |
| [03-ai-models-events-errors-monitoring.md](./03-ai-models-events-errors-monitoring.md) | AI Models (DrawingClassifier ViT-B/16, YOLOv8-seg FeatureDetector, DETR+DimNet DimensionDetector, BERT MaterialClassifier NER, DAEModelRegistry), Event System (7 tematów Kafka, 4 schematy Avro, DAEOutboxPublisher), Error Handling (11 klas błędów, ParseErrorHandler, ImageQualityGuard, retry decorator), Monitoring (25 metryk Prometheus, 7 dashboardów Grafana, 7 reguł Alertmanager) |
| [04-testing-scalability-risks-roadmap.md](./04-testing-scalability-risks-roadmap.md) | Testing (8 typów: unit/integration/OCR golden set/CV model/API contract/load k6/data quality/security), Scalability (4 poziomy L1–L4, ParseWorkerPool, Triton GPU serving, HPA Kubernetes, S3 storage), 15 Ryzyk (R01–R15), Roadmap 40 sprintów 4 fazy |

## Architektura

```
Drawing File (PDF / DXF / DWG / STEP / IGES / PNG / TIFF / JPEG)
        │
        ▼
┌───────────────────────┐
│  DrawingPreprocessor  │  deskew, denoise, DPI normalize, quality check
└──────────┬────────────┘
           │
           ▼
┌───────────────────────┐
│  FormatParserRegistry │  PDF (PyMuPDF) / DXF (ezdxf) / STEP (pythonOCC) / Raster
└──────────┬────────────┘
           │
    ┌──────┴────────────────────────────────────────────┐
    │                                                   │
    ▼                                                   ▼
┌──────────────┐                              ┌──────────────────┐
│  OCR Engine  │  Tesseract → PaddleOCR →    │ Geometry Extract │  Lines / Arcs /
│  (3-engine   │  DocumentAI (cloud fallback) │ (DXF exact /     │  Circles / Dims
│   cascade)   │                              │  Vector / OCR)   │
└──────┬───────┘                              └────────┬─────────┘
       │                                               │
       ▼                                               ▼
┌──────────────────┐        ┌──────────────────────────────────────┐
│ TitleBlockExtract│        │         AI Model Pipeline            │
│ (regex + spatial │        │  ViT Classifier + YOLOv8 Features +  │
│  heuristics)     │        │  DETR Dimensions + BERT Material NER │
└──────┬───────────┘        └─────────────────┬────────────────────┘
       │                                      │
       └──────────────────┬───────────────────┘
                          │
                          ▼
              ┌──────────────────────┐
              │   ToleranceParser    │  ± bilateral / GD&T frames / Ra Rz / ISO 2768
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  MaterialInferencer  │  MATERIAL_DATABASE + alias + NLP + feature hints
              └──────────┬───────────┘
                         │
                         ▼
              ┌──────────────────────┐
              │  DrawingParseResult  │  confidence score, DB write, Kafka events
              └──────────────────────┘
```

## Obsługiwane formaty

| Format | 2D Geometry | 3D Geometry | OCR | Tolerancje | Kompleksowość |
|--------|:-----------:|:-----------:|:---:|:---------:|:------------:|
| PDF (vector) | ✓✓ | — | ✓✓ | ✓✓ | Średnia |
| PDF (raster) | ✓ | — | ✓ (OCR) | ✓ (OCR) | Wysoka |
| DXF / DWG | ✓✓✓ | — | ✓✓ | ✓✓ | Niska |
| STEP / IGES | — | ✓✓✓ | — | ✓ | Wysoka |
| PNG / TIFF | ✓ (OCR) | — | ✓ (OCR) | ✓ (OCR) | Wysoka |
| JPEG | ✓ (OCR) | — | ✓ (OCR) | ✓ (OCR) | Bardzo wysoka |

## Pipeline stages (8 kroków)

| Stage | Czas typowy | Degradable |
|-------|------------|-----------|
| `preprocess` | 0.5–5s | Nie (fatal dla rasterów) |
| `format_parse` | 0.1–3s | Nie (fatal) |
| `ocr` | 1–30s | Tak (vector: bypass) |
| `geometry_extract` | 0.1–2s | Tak |
| `feature_detect` | 0.5–15s (GPU) | Tak |
| `tolerance_parse` | 0.1–1s | Tak |
| `material_infer` | 0.05–0.5s | Tak |
| `confidence_score` | < 50ms | Nie |

## OCR Engine cascade

```
TesseractBackend (primary)
    │ mean_confidence ≥ 0.70 → DONE
    │ mean_confidence < 0.70 →
    ▼
PaddleOCRBackend (secondary)
    │ better? → use Paddle
    │ mean_confidence < 0.55 →
    ▼
DocumentAI / Azure Form Recognizer (cloud, optional)
```

Języki Tesseract: `eng+deu+pol+chi_sim`

## Wykrywane cechy wytwarzania (20 typów)

| Kategoria | Cechy |
|-----------|-------|
| Otwory | HOLE_THRU, HOLE_BLIND, HOLE_COUNTERSINK, HOLE_COUNTERBORE |
| Gwinty | THREAD_INTERNAL (M-metryczne), THREAD_EXTERNAL |
| Frezy | POCKET, SLOT, GROOVE, UNDERCUT |
| Wykończenie | FILLET, CHAMFER, KNURL |
| Konstrukcja | BOSS, RIB |
| Spawanie | WELD_JOINT |
| Blacha | BEND |
| Odlew | DRAFT_ANGLE, PARTING_LINE |
| Inne | EMBOSS |

## Parsowanie tolerancji

| Typ | Przykład | Norma |
|-----|---------|-------|
| Symetryczna | 25.0 ±0.05 | — |
| Asymetryczna | 100.0 +0.10/-0.05 | — |
| Pasowanie ISO | H7, h6, H7/h6 | ISO 286 |
| Klasa ogólna | ISO 2768-m | ISO 2768 |
| GD&T forma | ⏥ 0.02 \| A | ISO 1101 / ASME Y14.5 |
| GD&T orientacja | ⊥ 0.05 \| A-B | ISO 1101 |
| Chropowatość | Ra 1.6 μm, Rz 6.3 μm | ISO 4287 |
| Klasa IT | IT7 | ISO 286 |

## Inferencja materiałów

Priorytet sygnałów (malejący):

1. **Title block** — bezpośrednie oznaczenie (np. `S235JR`, `1.4301`) → confidence 0.92
2. **Surface finish** — `ANODIZE` → aluminium; `PASSIVATE` → stal nierdzewna → confidence 0.55
3. **Heat treatment** — `NITRIDED`, `HRC 58`, `CARBURIZED` → odpowiedni gatunek → confidence 0.65
4. **Tolerance tightness** — IT ≤ 7 → likely alloy steel lub stainless → confidence 0.55

| Rodzina | Przykłady | Machinability | Cost index |
|---------|----------|:------------:|:---------:|
| STEEL_CARBON | S235JR, S355J2 | 0.60–0.65 | 1.0–1.3 |
| STEEL_ALLOY | 42CrMo4, 16MnCr5 | 0.55–0.60 | 1.7–2.1 |
| STEEL_STAINLESS | 1.4301, 1.4404 | 0.40–0.45 | 4.5–5.5 |
| CAST_IRON | GG-25, GGG-40 | 0.65–0.70 | 0.8–0.9 |
| ALUMINUM_WROUGHT | 6061-T6, 7075-T6 | 1.8–2.0 | 3.2–5.8 |
| TITANIUM | Ti-6Al-4V | 0.20 | 18.0 |

## Role RBAC

| Rola | Uprawnienia |
|------|-------------|
| `DAE_VIEWER` | GET drawings, features, dimensions, tolerances, material, jobs |
| `DAE_OPERATOR` | DAE_VIEWER + upload, reparse |
| `DAE_ANALYST` | DAE_VIEWER + analytics (material distribution, feature frequency, confidence report) |
| `DAE_ADMIN` | Wszystko + DELETE, queue-stats, admin operations |

## AI Models

| Model | Architektura | Task | mAP/F1 target |
|-------|-------------|------|:-------------:|
| DrawingClassifier | ViT-B/16 | Typ rysunku, norma | F1 ≥ 0.85 |
| FeatureDetector | YOLOv8m-seg | 15 typów cech (instance segmentation) | mAP50 ≥ 0.82 |
| DimensionDetector | DETR-R50 + DimNet | Lokalizacja + wartość wymiaru | F1 ≥ 0.78 |
| MaterialClassifier | BERT multilingual | NER — oznaczenie materiału | F1 ≥ 0.89 |

Serving: NVIDIA Triton Inference Server (TensorRT FP16, dynamic batching).
Modele opcjonalne — pipeline działa w trybie DEGRADED bez GPU.

## Monitoring — kluczowe metryki

| Metryka | Cel |
|---------|-----|
| `dae_parse_duration_seconds` p95 | ≤ 30s (PDF/DXF ≤ 10MB) |
| `dae_ocr_confidence` median | ≥ 0.80 |
| `dae_parse_status_total{status=FAILED}` rate | < 5% |
| `dae_parse_queue_depth` | < 500 (alert) |
| `dae_model_inference_duration_seconds` p95 | ≤ 2s per model |
| `dae_overall_confidence` median | ≥ 0.75 |

## SLA i KPIs (cel po S40)

| Metryka | Cel |
|---------|-----|
| Title block extraction F1 | ≥ 0.90 |
| Dimension extraction F1 | ≥ 0.80 |
| Feature detection mAP50 | ≥ 0.82 |
| Material inference accuracy | ≥ 0.85 |
| Parse P95 (PDF/DXF ≤ 10MB) | ≤ 30s |
| Parse P95 (STEP ≤ 100MB) | ≤ 120s |
| API P95 GET | ≤ 500ms |
| Availability | ≥ 99.5% |
| Throughput (L3) | ≥ 50 000 drawings/day |

## Skalowalność

| Poziom | Wolumen | Infrastruktura |
|--------|---------|----------------|
| L1 | ≤ 500/day | 1 API pod, 1 worker, CPU OCR |
| L2 | ≤ 5 000/day | 3–8 API pods, 2–5 GPU workers |
| L3 | ≤ 50 000/day | HPA 5–20 API + 5–20 GPU, Triton, Redis queue |
| L4 | > 50 000/day | Multi-region, streaming, partitioned DB |

HPA: `dae-api` 2–20 pods (CPU 70% + queue depth), `dae-gpu-worker` 1–10 pods.

## Stack techniczny

- **Backend:** Python 3.12 + FastAPI + asyncpg + asyncio
- **Baza danych:** PostgreSQL 16 (schemat `dae`, 8 tabel, GIN index na JSONB)
- **File Storage:** S3-compatible (MinIO local / AWS S3 production), AES-256 encryption
- **OCR:** Tesseract 5.x (LSTM) + PaddleOCR 2.x + Google DocumentAI (fallback)
- **PDF:** PyMuPDF (fitz) — vector paths + raster rendering
- **DXF/DWG:** ezdxf 1.x — entity parsing, dimension extraction
- **STEP/IGES:** pythonOCC 7.x — B-Rep topology analysis
- **Computer Vision:** OpenCV 4.x, PyTorch 2.x, ultralytics YOLOv8, transformers (DETR, ViT, BERT)
- **Model Serving:** NVIDIA Triton Inference Server (TensorRT FP16)
- **Messaging:** Apache Kafka 3+ (7 tematów, Avro + Schema Registry, Transactional Outbox)
- **Monitoring:** Prometheus (25 metryk) + Grafana (7 dashboardów) + Alertmanager (7 reguł)
- **Security:** JWT RS256, RBAC 4 role, AES-256 at-rest, TLS in-transit
- **Kubernetes:** HPA dae-api 2–20 pods, dae-gpu-worker 1–10 pods

## Integracje zewnętrzne

| System | Kierunek | Protokół | Dane |
|--------|----------|---------|------|
| CEE API | → | Kafka | Material properties → enriched cost estimate |
| BOME | → | Kafka | Drawing linked to BOM line (part_number match) |
| CLS | → | Kafka | Feature vectors for cost model training |
| RFQ Agent | → | Kafka | Material spec → supplier RFQ enrichment |
| AWS S3 / MinIO | ↔ | S3 API | Drawing file storage |
| Google DocumentAI | → | REST | Cloud OCR fallback |
| Grafana / Prometheus | ← | Pull | Metrics scraping |

## Roadmap — fazy

| Faza | Sprinty | Cel |
|------|---------|-----|
| Foundation | S1–S10 | DB, PDF/DXF parser, Tesseract OCR, rule-based features, Kafka, monitoring |
| AI Models | S11–S20 | ViT + YOLOv8 + DETR + BERT, Triton serving, golden set CI gate |
| STEP + Scale | S21–S30 | STEP topology, HPA, Redis queue, S3 multi-tenant, search API |
| Intelligence | S31–S40 | Active learning, BOME/CEE integration, drawing diff, ITAR classification, DR |
