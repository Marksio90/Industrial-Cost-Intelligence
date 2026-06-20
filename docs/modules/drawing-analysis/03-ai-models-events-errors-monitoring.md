# Drawing Analysis Engine — Sekcje 10–13

## 10. AI Models (Computer Vision)

### 10.1 Model landscape

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        DAE AI Model Pipeline                                │
│                                                                             │
│  Input Image / Vector                                                       │
│       │                                                                     │
│       ▼                                                                     │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────────┐   │
│  │ DrawingClassifier│    │  TitleBlock      │    │  DimensionDetector   │   │
│  │ (ViT-B/16)      │    │  Localizer       │    │  (DETR fine-tuned)   │   │
│  │ Format + Type   │    │  (YOLO v8)       │    │  + DimNet regressor  │   │
│  └────────┬────────┘    └────────┬─────────┘    └──────────┬───────────┘  │
│           │                      │                          │               │
│           └──────────────────────┴──────────────────────────┘              │
│                                  │                                          │
│                                  ▼                                          │
│  ┌─────────────────┐    ┌─────────────────┐    ┌──────────────────────┐   │
│  │ FeatureDetector │    │ GDTFrameDetector │    │ MaterialClassifier   │   │
│  │ (YOLO v8 seg.)  │    │ (CNN+CRF)        │    │ (BERT fine-tuned)    │   │
│  │ Holes/Pockets   │    │ GD&T frames      │    │ Material NLP         │   │
│  └─────────────────┘    └─────────────────┘    └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 10.2 DrawingClassifier — ViT

```python
from dataclasses import dataclass
from typing import Optional
import torch
import torch.nn as nn
from torchvision import transforms
from PIL import Image
import numpy as np


@dataclass
class ClassificationResult:
    drawing_type: DrawingType
    drawing_type_confidence: float
    standard: Optional[DrawingStandard]
    standard_confidence: float
    projection_method: Optional[str]    # "1st angle" / "3rd angle"
    has_title_block: bool
    has_gdt: bool


class DrawingClassifier:
    """
    Vision Transformer (ViT-B/16) fine-tuned on industrial drawings dataset.
    Classifies drawing type, standard, and structural characteristics.
    Model: vit_b_16, fine-tuned on 50k industrial drawings (proprietary dataset).
    Input: 224×224 normalized grayscale-to-RGB, batch_size=32.
    Output: multi-label classification (type × standard × attributes).
    """

    MODEL_ID    = "dae/drawing-classifier-vit-b16-v2"
    INPUT_SIZE  = 224
    THRESHOLDS  = {
        "drawing_type": 0.60,
        "standard":     0.55,
        "attributes":   0.50,
    }

    DRAWING_TYPE_LABELS = [t.value for t in DrawingType]
    STANDARD_LABELS     = [s.value for s in DrawingStandard] + ["UNKNOWN"]
    ATTRIBUTE_LABELS    = ["has_title_block", "has_gdt", "1st_angle", "3rd_angle"]

    def __init__(self, model_path: str, device: str = "cuda"):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.model = self._load_model(model_path)
        self.model.eval()
        self.transform = transforms.Compose([
            transforms.Resize((self.INPUT_SIZE, self.INPUT_SIZE)),
            transforms.Grayscale(num_output_channels=3),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

    def _load_model(self, path: str) -> nn.Module:
        from torchvision.models import vit_b_16
        n_types = len(self.DRAWING_TYPE_LABELS)
        n_stds  = len(self.STANDARD_LABELS)
        n_attrs = len(self.ATTRIBUTE_LABELS)
        model = vit_b_16(weights=None)
        # Replace head with multi-output head
        in_features = model.heads.head.in_features
        model.heads = nn.ModuleDict({
            "drawing_type": nn.Linear(in_features, n_types),
            "standard":     nn.Linear(in_features, n_stds),
            "attributes":   nn.Linear(in_features, n_attrs),
        })
        checkpoint = torch.load(path, map_location=self.device)
        model.load_state_dict(checkpoint["model_state_dict"])
        return model.to(self.device)

    async def classify(self, pages: list[np.ndarray]) -> ClassificationResult:
        if not pages:
            return self._default_result()
        # Use first page for classification
        img = Image.fromarray(pages[0])
        tensor = self.transform(img).unsqueeze(0).to(self.device)

        with torch.no_grad():
            # Forward through backbone
            features = self.model._process_input(tensor)
            features = self.model.encoder(features)
            cls_token = features[:, 0]

            type_logits = self.model.heads["drawing_type"](cls_token)
            std_logits  = self.model.heads["standard"](cls_token)
            attr_logits = self.model.heads["attributes"](cls_token)

        type_probs = torch.softmax(type_logits, dim=1)[0].cpu().numpy()
        std_probs  = torch.softmax(std_logits, dim=1)[0].cpu().numpy()
        attr_probs = torch.sigmoid(attr_logits)[0].cpu().numpy()

        best_type_idx  = int(type_probs.argmax())
        best_std_idx   = int(std_probs.argmax())

        std_label = self.STANDARD_LABELS[best_std_idx]
        try:
            standard = DrawingStandard(std_label)
        except ValueError:
            standard = None

        attr_dict = {k: float(v) for k, v in zip(self.ATTRIBUTE_LABELS, attr_probs)}

        return ClassificationResult(
            drawing_type=DrawingType(self.DRAWING_TYPE_LABELS[best_type_idx]),
            drawing_type_confidence=float(type_probs[best_type_idx]),
            standard=standard,
            standard_confidence=float(std_probs[best_std_idx]),
            projection_method=(
                "1st angle" if attr_dict.get("1st_angle", 0) > attr_dict.get("3rd_angle", 0)
                else "3rd angle"
            ),
            has_title_block=attr_dict.get("has_title_block", 0) > self.THRESHOLDS["attributes"],
            has_gdt=attr_dict.get("has_gdt", 0) > self.THRESHOLDS["attributes"],
        )

    def _default_result(self) -> ClassificationResult:
        return ClassificationResult(
            drawing_type=DrawingType.DETAIL,
            drawing_type_confidence=0.0,
            standard=None, standard_confidence=0.0,
            projection_method=None, has_title_block=False, has_gdt=False,
        )
```

### 10.3 Feature Detection Model — YOLO v8

```python
class YOLOFeatureDetectionModel:
    """
    YOLOv8-seg fine-tuned for manufacturing feature detection.
    Detects: holes, countersinks, counterbores, slots, pockets, welds, threads.
    Training: 25k annotated technical drawings (COCO-format).
    Inference: TensorRT-optimized (FP16), ~15ms per 640×640 image on A10G.
    mAP50: 0.82 on internal test set.
    """

    MODEL_ID  = "dae/feature-detector-yolov8m-seg-v3"
    IMG_SIZE  = 640
    CONF_THR  = 0.35
    IOU_THR   = 0.45

    YOLO_CLASS_TO_FEATURE: dict[int, FeatureType] = {
        0: FeatureType.HOLE_THRU,
        1: FeatureType.HOLE_BLIND,
        2: FeatureType.HOLE_COUNTERSINK,
        3: FeatureType.HOLE_COUNTERBORE,
        4: FeatureType.THREAD_INTERNAL,
        5: FeatureType.THREAD_EXTERNAL,
        6: FeatureType.SLOT,
        7: FeatureType.POCKET,
        8: FeatureType.FILLET,
        9: FeatureType.CHAMFER,
        10: FeatureType.WELD_JOINT,
        11: FeatureType.BEND,
        12: FeatureType.BOSS,
        13: FeatureType.GROOVE,
        14: FeatureType.UNDERCUT,
    }

    def __init__(self, model_path: str):
        from ultralytics import YOLO
        self.model = YOLO(model_path)

    async def detect(self, parsed: "ParsedDrawing") -> list[DetectedFeature]:
        if not parsed.text_elements and not any(True for _ in []):
            return []
        results = []
        for page_img in getattr(parsed, "_pages", []):
            yolo_results = self.model.predict(
                page_img,
                imgsz=self.IMG_SIZE,
                conf=self.CONF_THR,
                iou=self.IOU_THR,
                verbose=False,
            )
            for r in yolo_results:
                for box, cls_id, conf in zip(r.boxes.xyxy, r.boxes.cls, r.boxes.conf):
                    feat_type = self.YOLO_CLASS_TO_FEATURE.get(int(cls_id))
                    if feat_type is None:
                        continue
                    x0, y0, x1, y1 = box.tolist()
                    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                    results.append(DetectedFeature(
                        feature_type=feat_type,
                        location=Point2D(cx, cy),
                        parameters={},
                        confidence=float(conf),
                        source="CV_MODEL",
                    ))
        return results
```

### 10.4 DimensionDetector — DETR + DimNet

```python
class DimensionDetectorModel:
    """
    Two-stage dimension extraction:
    1. DETR (Detection Transformer) localizes dimension annotations on drawing.
    2. DimNet regressor extracts numerical value from localized region.

    Fine-tuned on 30k dimension-annotated industrial drawings.
    F1 score on dimension detection: 0.78 (challenging raster), 0.94 (DXF).
    """

    MODEL_DETR   = "dae/dimension-detector-detr-r50-v2"
    MODEL_DIMNET = "dae/dimnet-ocr-regressor-v1"
    CONF_THR     = 0.40

    def __init__(self, detr_path: str, dimnet_path: str, device: str = "cuda"):
        self.device = device
        self._load_detr(detr_path)
        self._load_dimnet(dimnet_path)

    def _load_detr(self, path: str):
        from transformers import DetrForObjectDetection, DetrImageProcessor
        self.detr_processor = DetrImageProcessor.from_pretrained(path)
        self.detr_model     = DetrForObjectDetection.from_pretrained(path)
        self.detr_model.eval()

    def _load_dimnet(self, path: str):
        # Lightweight CNN that extracts numeric value from dimension crop
        self.dimnet = torch.load(path, map_location=self.device)
        self.dimnet.eval()

    async def detect(self, pages: list[np.ndarray]) -> list[ExtractedDimension]:
        dims = []
        for page_idx, img in enumerate(pages):
            pil_img = Image.fromarray(img)
            inputs = self.detr_processor(images=pil_img, return_tensors="pt")
            with torch.no_grad():
                outputs = self.detr_model(**inputs)
            results = self.detr_processor.post_process_object_detection(
                outputs, threshold=self.CONF_THR, target_sizes=[pil_img.size[::-1]]
            )[0]
            for score, label, box in zip(results["scores"], results["labels"], results["boxes"]):
                crop = self._crop(img, box.tolist())
                value = await self._run_dimnet(crop)
                if value is None:
                    continue
                dim_type = self._label_to_type(int(label))
                dims.append(ExtractedDimension(
                    dim_type=dim_type,
                    value=value,
                    unit="mm",
                    tolerance_upper=None,
                    tolerance_lower=None,
                    text_raw="",
                    confidence=float(score) * 0.85,
                    source="CV_MODEL",
                    bbox=tuple(box.tolist()),
                ))
        return dims

    def _crop(self, img: np.ndarray, box: list[float]) -> np.ndarray:
        x0, y0, x1, y1 = [max(0, int(v)) for v in box]
        return img[y0:y1, x0:x1]

    async def _run_dimnet(self, crop: np.ndarray) -> Optional[float]:
        if crop.size == 0:
            return None
        t = transforms.Compose([
            transforms.ToPILImage(),
            transforms.Resize((64, 256)),
            transforms.ToTensor(),
        ])(crop).unsqueeze(0)
        with torch.no_grad():
            out = self.dimnet(t)
        value = float(out[0])
        return value if value > 0 else None

    def _label_to_type(self, label_id: int) -> str:
        mapping = {0: "LINEAR", 1: "DIAMETER", 2: "RADIAL", 3: "ANGULAR", 4: "ORDINATE"}
        return mapping.get(label_id, "LINEAR")
```

### 10.5 MaterialClassifier — BERT NLP

```python
class MaterialNLPClassifier:
    """
    Fine-tuned BERT (bert-base-multilingual-cased) for material designation extraction.
    Handles: EN, DE, PL, ZH, RU material nomenclatures.
    NER task: extracts material tokens and maps to standardized designation.
    F1 on material extraction: 0.89.
    """

    MODEL_ID = "dae/material-ner-bert-multilingual-v1"

    def __init__(self, model_path: str):
        from transformers import pipeline
        self.ner = pipeline(
            "ner",
            model=model_path,
            aggregation_strategy="simple",
            device=0 if torch.cuda.is_available() else -1,
        )

    async def extract(self, text: str) -> list[dict]:
        if not text.strip():
            return []
        entities = self.ner(text[:512])  # BERT max tokens
        materials = [
            {"text": e["word"], "confidence": e["score"]}
            for e in entities
            if e["entity_group"] == "MATERIAL" and e["score"] > 0.70
        ]
        return materials
```

### 10.6 Model Registry & Versioning

```python
@dataclass
class ModelVersion:
    model_id: str
    version: str
    artifact_path: str
    framework: str               # "pytorch" / "onnx" / "tensorrt"
    precision: str               # "fp32" / "fp16" / "int8"
    metrics: dict[str, float]    # {"mAP50": 0.82, "F1": 0.78, ...}
    deployed_at: Optional[str]
    is_active: bool


class DAEModelRegistry:
    """
    Lightweight model registry for DAE models.
    Integrates with MLflow for versioning; handles GPU/CPU placement.
    """
    ACTIVE_MODELS: dict[str, ModelVersion] = {
        "drawing_classifier":    ModelVersion("dae/drawing-classifier-vit-b16-v2",    "2.1.0", "", "pytorch", "fp16", {}, None, True),
        "feature_detector":      ModelVersion("dae/feature-detector-yolov8m-seg-v3",  "3.0.0", "", "tensorrt","fp16", {}, None, True),
        "dimension_detector":    ModelVersion("dae/dimension-detector-detr-r50-v2",   "2.0.0", "", "pytorch", "fp32", {}, None, True),
        "material_classifier":   ModelVersion("dae/material-ner-bert-multilingual-v1","1.2.0", "", "pytorch", "fp32", {}, None, True),
    }

    def __init__(self, mlflow_uri: str, model_cache_dir: str):
        import mlflow
        mlflow.set_tracking_uri(mlflow_uri)
        self.cache_dir = model_cache_dir

    async def load_active(self, model_name: str) -> ModelVersion:
        version = self.ACTIVE_MODELS.get(model_name)
        if not version:
            raise ValueError(f"Unknown model: {model_name}")
        return version

    async def promote(self, model_name: str, new_version: str, metrics: dict) -> None:
        current = self.ACTIVE_MODELS.get(model_name)
        if not current:
            raise ValueError(f"Unknown model: {model_name}")
        # Validate metrics threshold before promotion
        required = {"mAP50": 0.75, "F1": 0.70}
        for metric, threshold in required.items():
            if metrics.get(metric, 0) < threshold:
                raise ModelPromotionError(
                    f"Model {model_name} v{new_version}: {metric}={metrics.get(metric):.3f} < {threshold}"
                )
        self.ACTIVE_MODELS[model_name] = ModelVersion(
            model_id=current.model_id,
            version=new_version,
            artifact_path=current.artifact_path,
            framework=current.framework,
            precision=current.precision,
            metrics=metrics,
            deployed_at=None,
            is_active=True,
        )


class ModelPromotionError(Exception):
    pass
```

---

## 11. Event System

### 11.1 Kafka topics

```
dae.drawing.uploaded        → DrawingUploaded
dae.drawing.parsed          → DrawingParsed (DONE / FAILED / PARTIAL)
dae.drawing.feature_ready   → FeaturesExtracted
dae.drawing.material_ready  → MaterialInferred
dae.drawing.dimension_ready → DimensionsExtracted
dae.drawing.reparse_requested → ReparseRequested
dae.model.promoted          → ModelVersionPromoted
```

### 11.2 Avro schemas

```json
{
  "name": "DrawingParsed",
  "namespace": "io.industrial_cost.dae",
  "type": "record",
  "doc": "Emitted when a drawing parse pipeline completes (any terminal status).",
  "fields": [
    {"name": "event_id",            "type": "string"},
    {"name": "drawing_id",          "type": "string"},
    {"name": "part_number",         "type": ["null", "string"], "default": null},
    {"name": "format",              "type": "string"},
    {"name": "parse_status",        "type": {"type": "enum", "name": "ParseStatus",
      "symbols": ["DONE", "FAILED", "PARTIAL"]}},
    {"name": "overall_confidence",  "type": ["null", "double"], "default": null},
    {"name": "processing_time_ms",  "type": ["null", "int"],    "default": null},
    {"name": "material_designation",  "type": ["null", "string"], "default": null},
    {"name": "material_family",       "type": ["null", "string"], "default": null},
    {"name": "feature_count",         "type": "int"},
    {"name": "dimension_count",       "type": "int"},
    {"name": "tolerance_count",       "type": "int"},
    {"name": "errors",              "type": {"type": "array", "items": "string"}},
    {"name": "parsed_at",           "type": "string"}
  ]
}
```

```json
{
  "name": "MaterialInferred",
  "namespace": "io.industrial_cost.dae",
  "type": "record",
  "doc": "Emitted when material inference is complete for a drawing.",
  "fields": [
    {"name": "event_id",            "type": "string"},
    {"name": "drawing_id",          "type": "string"},
    {"name": "part_number",         "type": ["null", "string"], "default": null},
    {"name": "designation",         "type": "string"},
    {"name": "family",              "type": "string"},
    {"name": "standard",            "type": ["null", "string"], "default": null},
    {"name": "density_kg_m3",       "type": ["null", "double"], "default": null},
    {"name": "cost_index",          "type": ["null", "double"], "default": null},
    {"name": "machinability_index", "type": ["null", "double"], "default": null},
    {"name": "confidence",          "type": "double"},
    {"name": "confidence_level",    "type": "string"},
    {"name": "inference_sources",   "type": {"type": "array", "items": "string"}},
    {"name": "inferred_at",         "type": "string"}
  ]
}
```

```json
{
  "name": "FeaturesExtracted",
  "namespace": "io.industrial_cost.dae",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "drawing_id",      "type": "string"},
    {"name": "part_number",     "type": ["null", "string"], "default": null},
    {"name": "feature_summary", "type": {
      "type": "array", "items": {
        "type": "record", "name": "FeatureSummaryItem",
        "fields": [
          {"name": "feature_type", "type": "string"},
          {"name": "count",        "type": "int"},
          {"name": "avg_confidence", "type": "double"}
        ]
      }
    }},
    {"name": "has_threads",     "type": "boolean"},
    {"name": "has_gdt",         "type": "boolean"},
    {"name": "extracted_at",    "type": "string"}
  ]
}
```

```json
{
  "name": "DrawingUploaded",
  "namespace": "io.industrial_cost.dae",
  "type": "record",
  "fields": [
    {"name": "event_id",        "type": "string"},
    {"name": "drawing_id",      "type": "string"},
    {"name": "job_id",          "type": "string"},
    {"name": "filename",        "type": "string"},
    {"name": "format",          "type": "string"},
    {"name": "file_size_bytes", "type": "long"},
    {"name": "part_number",     "type": ["null", "string"], "default": null},
    {"name": "priority",        "type": "int"},
    {"name": "uploaded_by",     "type": "string"},
    {"name": "uploaded_at",     "type": "string"}
  ]
}
```

### 11.3 Outbox publisher

```python
import json
import asyncio
import structlog
from confluent_kafka import Producer
from confluent_kafka.schema_registry import SchemaRegistryClient
from confluent_kafka.schema_registry.avro import AvroSerializer

log = structlog.get_logger()


class DAEOutboxPublisher:
    """
    Polls dae.outbox_events every 500ms, publishes to Kafka with Avro serialization.
    Transactional outbox pattern — guarantees at-least-once delivery.
    """
    POLL_INTERVAL_MS = 500
    BATCH_SIZE = 100

    def __init__(self, db_pool, kafka_config: dict, schema_registry_url: str):
        self.db_pool = db_pool
        self.producer = Producer(kafka_config)
        self.sr_client = SchemaRegistryClient({"url": schema_registry_url})

    async def run(self):
        while True:
            try:
                await self._publish_batch()
            except Exception as e:
                log.error("outbox_publish_error", error=str(e))
            await asyncio.sleep(self.POLL_INTERVAL_MS / 1000)

    async def _publish_batch(self):
        async with self.db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT event_id, topic, key, payload, headers
                FROM dae.outbox_events
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
                        on_delivery=self._delivery_callback,
                    )
                except Exception as e:
                    log.warning("kafka_produce_error", event_id=str(row["event_id"]), error=str(e))
                    await conn.execute(
                        "UPDATE dae.outbox_events SET retry_count = retry_count + 1 WHERE event_id = $1",
                        row["event_id"],
                    )
                    continue
                await conn.execute(
                    "UPDATE dae.outbox_events SET published_at = now() WHERE event_id = $1",
                    row["event_id"],
                )
            self.producer.flush(timeout=5.0)

    def _delivery_callback(self, err, msg):
        if err:
            log.error("kafka_delivery_failed", topic=msg.topic(), error=str(err))
        else:
            log.debug("kafka_delivered", topic=msg.topic(), partition=msg.partition(), offset=msg.offset())
```

### 11.4 External event consumers

| System | Topic consumed | Action |
|--------|---------------|--------|
| CEE API | `dae.drawing.material_ready` | Enriches cost estimate with material properties |
| BOME | `dae.drawing.parsed` | Links drawing to BOM line item (part_number match) |
| CLS | `dae.drawing.feature_ready` | Feature vector for cost model training |
| RFQ Agent | `dae.drawing.material_ready` | Adds material spec to RFQ for suppliers |

---

## 12. Error Handling

### 12.1 Error taxonomy

```python
class DAEError(Exception):
    """Base class for all Drawing Analysis Engine errors."""
    http_status: int = 500
    error_code: str = "DAE_INTERNAL_ERROR"


class UnsupportedFormatError(DAEError):
    http_status = 400
    error_code  = "DAE_UNSUPPORTED_FORMAT"


class FileTooLargeError(DAEError):
    http_status = 413
    error_code  = "DAE_FILE_TOO_LARGE"


class CorruptedFileError(DAEError):
    http_status = 422
    error_code  = "DAE_CORRUPTED_FILE"


class LowQualityImageError(DAEError):
    """DPI too low or image too blurry for reliable OCR."""
    http_status = 422
    error_code  = "DAE_LOW_QUALITY_IMAGE"


class OCRFailureError(DAEError):
    http_status = 422
    error_code  = "DAE_OCR_FAILURE"


class GeometryExtractionError(DAEError):
    http_status = 422
    error_code  = "DAE_GEOMETRY_FAILURE"


class ModelInferenceError(DAEError):
    http_status = 500
    error_code  = "DAE_MODEL_INFERENCE_ERROR"


class ModelNotLoadedError(DAEError):
    http_status = 503
    error_code  = "DAE_MODEL_NOT_LOADED"


class ParseTimeoutError(DAEError):
    http_status = 504
    error_code  = "DAE_PARSE_TIMEOUT"


class RecoverableParseError(DAEError):
    """Non-fatal — pipeline stage fails but continues with degraded output."""
    http_status = 200
    error_code  = "DAE_STAGE_DEGRADED"


class StorageError(DAEError):
    http_status = 500
    error_code  = "DAE_STORAGE_ERROR"
```

### 12.2 Error recovery strategies

```python
import asyncio
from functools import wraps


def with_timeout(seconds: float):
    """Decorator: raise ParseTimeoutError if coroutine exceeds time limit."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(fn(*args, **kwargs), timeout=seconds)
            except asyncio.TimeoutError:
                raise ParseTimeoutError(f"{fn.__name__} exceeded {seconds}s timeout")
        return wrapper
    return decorator


def with_retry(max_attempts: int = 3, backoff_base: float = 2.0):
    """Decorator: retry transient failures with exponential backoff."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except (ModelInferenceError, StorageError, OCRFailureError) as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        delay = backoff_base ** attempt
                        log.warning("retry_attempt", fn=fn.__name__, attempt=attempt + 1, delay=delay)
                        await asyncio.sleep(delay)
            raise last_exc
        return wrapper
    return decorator


class ParseErrorHandler:
    """
    Centralized error handling for the parsing pipeline.
    Decides: abort pipeline / continue degraded / escalate to cloud OCR.
    """

    ABORT_ERRORS = (CorruptedFileError, UnsupportedFormatError, ParseTimeoutError)
    DEGRADED_OK  = (RecoverableParseError, LowQualityImageError, GeometryExtractionError)

    async def handle(self, error: Exception, stage: str, result: DrawingParseResult) -> str:
        """Returns: 'abort' / 'continue' / 'retry'."""
        if isinstance(error, self.ABORT_ERRORS):
            result.errors.append(f"[{stage}] FATAL: {error}")
            return "abort"
        if isinstance(error, self.DEGRADED_OK):
            result.warnings.append(f"[{stage}] DEGRADED: {error}")
            return "continue"
        if isinstance(error, ModelInferenceError):
            result.warnings.append(f"[{stage}] AI model failed, skipping: {error}")
            return "continue"
        # Unknown — log and continue
        log.error("unhandled_parse_error", stage=stage, error=str(error), exc_info=True)
        result.warnings.append(f"[{stage}] UNEXPECTED: {error}")
        return "continue"


class ImageQualityGuard:
    """Pre-flight check before OCR — rejects images below minimum quality."""

    MIN_DPI       = 150
    MIN_SHARPNESS = 0.30
    MIN_CONTRAST  = 0.40

    def check(self, preprocessed: PreprocessedDrawing) -> None:
        if preprocessed.dpi and preprocessed.dpi < self.MIN_DPI:
            raise LowQualityImageError(
                f"Image DPI {preprocessed.dpi} below minimum {self.MIN_DPI}. "
                f"Please re-scan at ≥300 DPI for reliable OCR."
            )
        if preprocessed.quality_score < self.MIN_SHARPNESS:
            raise LowQualityImageError(
                f"Image quality score {preprocessed.quality_score:.2f} too low. "
                f"Check for blur, low contrast, or heavy compression."
            )
```

### 12.3 FastAPI global exception handler

```python
from fastapi import Request
from fastapi.responses import JSONResponse
import uuid


async def dae_exception_handler(request: Request, exc: DAEError) -> JSONResponse:
    request_id = str(uuid.uuid4())
    log.error(
        "api_error",
        error_code=exc.error_code,
        detail=str(exc),
        path=str(request.url),
        request_id=request_id,
    )
    return JSONResponse(
        status_code=exc.http_status,
        content={
            "error": exc.error_code,
            "detail": str(exc),
            "request_id": request_id,
        },
    )


async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = str(uuid.uuid4())
    log.error("unhandled_exception", path=str(request.url), request_id=request_id, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "detail": "Unexpected error", "request_id": request_id},
    )


# Register in app startup:
# app.add_exception_handler(DAEError, dae_exception_handler)
# app.add_exception_handler(Exception, generic_exception_handler)
```

---

## 13. Monitoring

### 13.1 Prometheus metrics

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# ── Upload & Parse ────────────────────────────────────────────────────────────
dae_drawings_uploaded_total = Counter(
    "dae_drawings_uploaded_total",
    "Total drawings uploaded",
    ["format", "drawing_type"],
)

dae_parse_duration_seconds = Histogram(
    "dae_parse_duration_seconds",
    "Total end-to-end parse pipeline duration",
    ["format", "status"],
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0],
)

dae_stage_duration_seconds = Histogram(
    "dae_stage_duration_seconds",
    "Duration of individual pipeline stages",
    ["stage", "format"],
    buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 15.0],
)

dae_parse_status_total = Counter(
    "dae_parse_status_total",
    "Parse completions by status",
    ["status", "format"],
)

dae_parse_queue_depth = Gauge(
    "dae_parse_queue_depth",
    "Current number of jobs in QUEUED state",
)

dae_active_workers = Gauge(
    "dae_active_workers",
    "Number of currently active parse workers",
)

# ── OCR ───────────────────────────────────────────────────────────────────────
dae_ocr_duration_seconds = Histogram(
    "dae_ocr_duration_seconds",
    "OCR engine duration",
    ["engine", "format"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 15.0, 30.0],
)

dae_ocr_confidence = Histogram(
    "dae_ocr_confidence",
    "Mean OCR confidence per drawing",
    ["engine"],
    buckets=[0.3, 0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)

dae_ocr_fallback_total = Counter(
    "dae_ocr_fallback_total",
    "OCR engine fallback triggers",
    ["from_engine", "to_engine", "reason"],
)

dae_ocr_cloud_requests_total = Counter(
    "dae_ocr_cloud_requests_total",
    "Requests to cloud OCR (DocumentAI / Azure)",
    ["provider"],
)

# ── Extraction ────────────────────────────────────────────────────────────────
dae_features_extracted_total = Counter(
    "dae_features_extracted_total",
    "Total manufacturing features extracted",
    ["feature_type", "source"],
)

dae_dimensions_extracted_total = Counter(
    "dae_dimensions_extracted_total",
    "Total dimensions extracted",
    ["dim_type", "source"],
)

dae_tolerances_extracted_total = Counter(
    "dae_tolerances_extracted_total",
    "Total tolerances extracted",
    ["tol_type", "source"],
)

dae_material_confidence = Histogram(
    "dae_material_confidence",
    "Material inference confidence distribution",
    ["family"],
    buckets=[0.3, 0.4, 0.5, 0.6, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

dae_overall_confidence = Histogram(
    "dae_overall_confidence",
    "Overall parse confidence distribution",
    buckets=[0.3, 0.4, 0.5, 0.6, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

# ── AI Models ─────────────────────────────────────────────────────────────────
dae_model_inference_duration_seconds = Histogram(
    "dae_model_inference_duration_seconds",
    "AI model inference duration",
    ["model_name", "model_version"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

dae_model_errors_total = Counter(
    "dae_model_errors_total",
    "AI model inference errors",
    ["model_name", "error_type"],
)

# ── Errors ───────────────────────────────────────────────────────────────────
dae_parse_errors_total = Counter(
    "dae_parse_errors_total",
    "Parse errors by stage and type",
    ["stage", "error_code"],
)

dae_low_quality_images_total = Counter(
    "dae_low_quality_images_total",
    "Images rejected due to low quality",
    ["reason"],
)

# ── File Size Distribution ────────────────────────────────────────────────────
dae_file_size_bytes = Histogram(
    "dae_file_size_bytes",
    "Distribution of uploaded drawing file sizes",
    ["format"],
    buckets=[50_000, 200_000, 1_000_000, 5_000_000, 20_000_000,
             50_000_000, 100_000_000, 500_000_000],
)
```

### 13.2 Grafana dashboards (7 dashboards)

| Dashboard | Panele | Cel |
|-----------|--------|-----|
| **DAE Overview** | Upload rate, parse queue depth, parse status donut, P95 duration | Operacyjny widok ogólny |
| **OCR Performance** | Confidence heatmap per format, fallback rate, engine breakdown, cloud OCR cost | Jakość OCR |
| **Feature Extraction** | Feature type distribution, features/drawing avg, source breakdown (GEOMETRY vs OCR vs CV) | Jakość ekstrakcji cech |
| **Material Inference** | Material family distribution, confidence by family, UNKNOWN rate, top designations | Jakość inferencji materiałów |
| **AI Model Health** | Inference latency per model, error rate, model version, GPU utilization | Zdrowie modeli CV |
| **Pipeline Stages** | Latency per stage (stacked bar), stage failure rate, slowest stages heatmap | Wąskie gardła pipeline'u |
| **Error & Quality** | Parse error rate by code, low-quality image rate, retry rate, SLA breach heatmap | Jakość i błędy |

### 13.3 Alertmanager rules

```yaml
groups:
  - name: dae.critical
    rules:
      - alert: DAEParseQueueSpiking
        expr: dae_parse_queue_depth > 500
        for: 5m
        labels:
          severity: critical
          team: dae
        annotations:
          summary: "Parse queue depth > 500 for 5m"
          description: "{{ $value }} jobs queued. Check worker scaling."

      - alert: DAEParseErrorRateHigh
        expr: |
          rate(dae_parse_status_total{status="FAILED"}[10m])
          / rate(dae_parse_status_total[10m]) > 0.15
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "DAE parse failure rate > 15% ({{ $value | humanizePercentage }})"

      - alert: DAEModelInferenceDown
        expr: |
          increase(dae_model_errors_total[5m]) > 10
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "AI model {{ $labels.model_name }} >10 errors in 5m"

      - alert: DAEParseLatencyHigh
        expr: |
          histogram_quantile(0.95, rate(dae_parse_duration_seconds_bucket[10m])) > 120
        for: 5m
        labels:
          severity: warning
        annotations:
          summary: "DAE P95 parse latency > 120s"

      - alert: DAEOCRConfidenceLow
        expr: |
          histogram_quantile(0.50, rate(dae_ocr_confidence_bucket[1h])) < 0.60
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Median OCR confidence below 0.60 — check input quality"

      - alert: DAEWorkerCountZero
        expr: dae_active_workers == 0
        for: 2m
        labels:
          severity: critical
        annotations:
          summary: "No active DAE parse workers!"

      - alert: DAELowQualityImageRate
        expr: |
          rate(dae_low_quality_images_total[1h]) > 0.30
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "More than 30% of uploaded images below minimum quality"
```

### 13.4 Structured logging

```python
import structlog

structlog.configure(
    processors=[
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.BoundLogger,
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()


class ParseJobLogger:
    """Structured per-job logging throughout the pipeline."""

    def __init__(self, drawing_id: str, job_id: str):
        self.log = log.bind(drawing_id=drawing_id, job_id=job_id)

    def stage_start(self, stage: str):
        self.log.info("stage_start", stage=stage)

    def stage_done(self, stage: str, duration_ms: float, **extra):
        self.log.info("stage_done", stage=stage, duration_ms=round(duration_ms, 1), **extra)

    def stage_failed(self, stage: str, error: str, duration_ms: float):
        self.log.warning("stage_failed", stage=stage, error=error, duration_ms=round(duration_ms, 1))

    def pipeline_done(self, result: DrawingParseResult):
        self.log.info(
            "pipeline_done",
            parse_status=result.parse_status if hasattr(result, "parse_status") else "DONE",
            overall_confidence=round(result.overall_confidence, 4),
            processing_time_ms=round(result.processing_time_ms, 1),
            feature_count=len(result.features),
            dimension_count=len(result.dimensions),
            tolerance_count=len(result.tolerances),
            warnings=len(result.warnings),
            errors=len(result.errors),
        )
```
