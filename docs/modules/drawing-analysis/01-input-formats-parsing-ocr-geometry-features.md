# Drawing Analysis Engine — Sekcje 1–5

## 1. Input Formats (PDF, DXF, STEP)

### 1.1 Obsługiwane formaty wejściowe

```python
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
from pathlib import Path


class DrawingFormat(str, Enum):
    PDF   = "PDF"    # 2D drawings, scanned or vector
    DXF   = "DXF"   # AutoCAD Drawing Exchange Format
    DWG   = "DWG"   # AutoCAD native (binary)
    STEP  = "STEP"  # ISO 10303, 3D geometry
    IGES  = "IGES"  # 3D legacy interchange
    SVG   = "SVG"   # Vector, web-native
    PNG   = "PNG"   # Raster scan
    TIFF  = "TIFF"  # High-res scan (blueprint)
    JPEG  = "JPEG"  # Compressed scan (lower quality)


class DrawingType(str, Enum):
    DETAIL        = "DETAIL"        # Single part drawing
    ASSEMBLY      = "ASSEMBLY"      # Assembly drawing with BOM
    SCHEMATIC     = "SCHEMATIC"     # Electrical / hydraulic
    WELD          = "WELD"          # Welding drawing
    SHEET_METAL   = "SHEET_METAL"   # Unfolded flat patterns
    CASTING       = "CASTING"       # Cast part with draft angles
    MACHINED      = "MACHINED"      # Machined part tolerances
    GENERAL_ARRANGEMENT = "GENERAL_ARRANGEMENT"


class DrawingStandard(str, Enum):
    ISO   = "ISO"    # ISO 128, 286, 1101
    ANSI  = "ANSI"   # ASME Y14.5
    DIN   = "DIN"    # German standard
    JIS   = "JIS"    # Japanese standard
    GB    = "GB"     # Chinese standard
    GOST  = "GOST"   # Russian standard


@dataclass
class DrawingMetadata:
    drawing_id: str
    filename: str
    format: DrawingFormat
    drawing_type: Optional[DrawingType]
    standard: Optional[DrawingStandard]
    page_count: int
    dpi: Optional[int]           # for raster
    has_title_block: bool
    file_size_bytes: int
    checksum_sha256: str
    uploaded_at: str             # ISO 8601
    uploaded_by: str


@dataclass
class TitleBlock:
    """Extracted from drawing title block."""
    part_number: Optional[str]
    part_name: Optional[str]
    revision: Optional[str]
    material: Optional[str]
    surface_finish: Optional[str]
    scale: Optional[str]
    projection_method: Optional[str]   # "1st angle" / "3rd angle"
    drawn_by: Optional[str]
    checked_by: Optional[str]
    approved_by: Optional[str]
    drawing_date: Optional[str]
    company: Optional[str]
    sheet_number: Optional[str]        # "1 OF 3"
    mass_kg: Optional[float]
    unit: str = "mm"
    confidence: float = 0.0
```

### 1.2 Format capabilities matrix

| Format | 2D Geometry | 3D Geometry | Text/OCR | Tolerances | BOM | Metadata |
|--------|------------|-------------|----------|------------|-----|---------|
| PDF (vector) | ✓✓ | — | ✓✓ | ✓✓ | ✓ | ✓ |
| PDF (raster) | ✓ (OCR) | — | ✓ (OCR) | ✓ (OCR) | ✓ (OCR) | ✓ |
| DXF | ✓✓✓ | — | ✓✓ | ✓✓ | ✓ | ✓✓ |
| DWG | ✓✓✓ | — | ✓✓ | ✓✓ | ✓ | ✓✓ |
| STEP | — | ✓✓✓ | — | ✓✓ | — | ✓✓ |
| IGES | — | ✓✓ | — | ✓ | — | ✓ |
| PNG/TIFF/JPEG | ✓ (OCR) | — | ✓ (OCR) | ✓ (OCR) | ✓ (OCR) | — |
| SVG | ✓✓ | — | ✓✓ | ✓ | — | ✓ |

### 1.3 Limity i ograniczenia

```python
FORMAT_LIMITS = {
    DrawingFormat.PDF:  {"max_size_mb": 100, "max_pages": 50},
    DrawingFormat.DXF:  {"max_size_mb": 50,  "max_entities": 500_000},
    DrawingFormat.STEP: {"max_size_mb": 500, "max_faces": 1_000_000},
    DrawingFormat.PNG:  {"max_size_mb": 50,  "min_dpi": 150, "recommended_dpi": 300},
    DrawingFormat.TIFF: {"max_size_mb": 200, "min_dpi": 150, "recommended_dpi": 600},
    DrawingFormat.JPEG: {"max_size_mb": 20,  "min_dpi": 150, "quality_warning": True},
}

RASTER_QUALITY_THRESHOLDS = {
    "min_dpi": 150,        # below this: OCR quality POOR
    "good_dpi": 300,       # OCR quality GOOD
    "excellent_dpi": 600,  # OCR quality EXCELLENT
    "min_contrast": 0.6,   # Michelson contrast ratio
    "max_skew_deg": 5.0,   # auto-deskew above this
    "min_sharpness": 0.4,  # Laplacian variance normalized
}
```

---

## 2. Parsing Pipeline

### 2.1 Architektura pipeline'u

```python
import asyncio
from dataclasses import dataclass, field
from typing import Any
import structlog

log = structlog.get_logger()


@dataclass
class PipelineStage:
    name: str
    duration_ms: float = 0.0
    status: str = "PENDING"    # PENDING / RUNNING / DONE / FAILED
    error: Optional[str] = None
    output_keys: list[str] = field(default_factory=list)


@dataclass
class DrawingParseResult:
    drawing_id: str
    format: DrawingFormat
    stages: list[PipelineStage] = field(default_factory=list)
    title_block: Optional[TitleBlock] = None
    geometry: Optional["GeometryResult"] = None
    features: list["DetectedFeature"] = field(default_factory=list)
    dimensions: list["ExtractedDimension"] = field(default_factory=list)
    tolerances: list["Tolerance"] = field(default_factory=list)
    material_inference: Optional["MaterialInferenceResult"] = None
    overall_confidence: float = 0.0
    processing_time_ms: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class DrawingParsingPipeline:
    """
    Orchestrates the full parsing pipeline for a technical drawing.
    Stages run sequentially; each stage receives the accumulated context.
    """

    def __init__(
        self,
        preprocessor: "DrawingPreprocessor",
        format_parser: "FormatParserRegistry",
        ocr_engine: "OCREngine",
        geometry_extractor: "GeometryExtractor",
        feature_detector: "FeatureDetector",
        tolerance_parser: "ToleranceParser",
        material_inferencer: "MaterialInferencer",
    ):
        self.preprocessor = preprocessor
        self.format_parser = format_parser
        self.ocr_engine = ocr_engine
        self.geometry_extractor = geometry_extractor
        self.feature_detector = feature_detector
        self.tolerance_parser = tolerance_parser
        self.material_inferencer = material_inferencer

    async def process(self, drawing_id: str, file_path: Path) -> DrawingParseResult:
        result = DrawingParseResult(drawing_id=drawing_id, format=self._detect_format(file_path))
        import time
        t0 = time.monotonic()

        pipeline_steps = [
            ("preprocess",         self._run_preprocess),
            ("format_parse",       self._run_format_parse),
            ("ocr",                self._run_ocr),
            ("geometry_extract",   self._run_geometry),
            ("feature_detect",     self._run_features),
            ("tolerance_parse",    self._run_tolerances),
            ("material_infer",     self._run_material),
            ("confidence_score",   self._run_confidence),
        ]

        context: dict[str, Any] = {"file_path": file_path, "result": result}

        for stage_name, stage_fn in pipeline_steps:
            stage = PipelineStage(name=stage_name)
            result.stages.append(stage)
            t_stage = time.monotonic()
            try:
                stage.status = "RUNNING"
                await stage_fn(context, result)
                stage.status = "DONE"
            except RecoverableParseError as e:
                stage.status = "FAILED"
                stage.error = str(e)
                result.warnings.append(f"[{stage_name}] {e}")
                log.warning("pipeline_stage_failed", stage=stage_name, error=str(e), drawing_id=drawing_id)
            except Exception as e:
                stage.status = "FAILED"
                stage.error = str(e)
                result.errors.append(f"[{stage_name}] {e}")
                log.error("pipeline_stage_error", stage=stage_name, error=str(e), drawing_id=drawing_id)
                break  # critical failure — abort pipeline
            finally:
                stage.duration_ms = (time.monotonic() - t_stage) * 1000

        result.processing_time_ms = (time.monotonic() - t0) * 1000
        return result

    def _detect_format(self, path: Path) -> DrawingFormat:
        suffix = path.suffix.lower().lstrip(".")
        mapping = {
            "pdf": DrawingFormat.PDF,
            "dxf": DrawingFormat.DXF,
            "dwg": DrawingFormat.DWG,
            "step": DrawingFormat.STEP,
            "stp": DrawingFormat.STEP,
            "iges": DrawingFormat.IGES,
            "igs": DrawingFormat.IGES,
            "svg": DrawingFormat.SVG,
            "png": DrawingFormat.PNG,
            "tiff": DrawingFormat.TIFF,
            "tif": DrawingFormat.TIFF,
            "jpg": DrawingFormat.JPEG,
            "jpeg": DrawingFormat.JPEG,
        }
        return mapping.get(suffix, DrawingFormat.PDF)

    async def _run_preprocess(self, ctx: dict, result: DrawingParseResult) -> None:
        ctx["preprocessed"] = await self.preprocessor.process(ctx["file_path"], result.format)

    async def _run_format_parse(self, ctx: dict, result: DrawingParseResult) -> None:
        parser = self.format_parser.get(result.format)
        ctx["parsed"] = await parser.parse(ctx["preprocessed"])
        result.title_block = ctx["parsed"].title_block

    async def _run_ocr(self, ctx: dict, result: DrawingParseResult) -> None:
        if result.format in (DrawingFormat.PNG, DrawingFormat.TIFF, DrawingFormat.JPEG):
            ctx["ocr_result"] = await self.ocr_engine.run(ctx["preprocessed"])
        elif result.format == DrawingFormat.PDF and ctx["parsed"].is_raster:
            ctx["ocr_result"] = await self.ocr_engine.run(ctx["preprocessed"])
        else:
            ctx["ocr_result"] = ctx["parsed"].text_elements  # already extracted

    async def _run_geometry(self, ctx: dict, result: DrawingParseResult) -> None:
        ctx["geometry"] = await self.geometry_extractor.extract(ctx["parsed"], ctx.get("ocr_result"))
        result.geometry = ctx["geometry"]
        result.dimensions = ctx["geometry"].dimensions

    async def _run_features(self, ctx: dict, result: DrawingParseResult) -> None:
        result.features = await self.feature_detector.detect(ctx["geometry"], ctx["parsed"])

    async def _run_tolerances(self, ctx: dict, result: DrawingParseResult) -> None:
        result.tolerances = await self.tolerance_parser.parse(
            ctx.get("ocr_result", []), ctx["geometry"]
        )

    async def _run_material(self, ctx: dict, result: DrawingParseResult) -> None:
        result.material_inference = await self.material_inferencer.infer(
            result.title_block, result.features, result.tolerances, result.dimensions
        )

    async def _run_confidence(self, ctx: dict, result: DrawingParseResult) -> None:
        weights = {
            "title_block": 0.20,
            "geometry":    0.25,
            "dimensions":  0.25,
            "tolerances":  0.15,
            "material":    0.15,
        }
        scores = {
            "title_block": result.title_block.confidence if result.title_block else 0.0,
            "geometry":    result.geometry.confidence if result.geometry else 0.0,
            "dimensions":  _avg_confidence(result.dimensions),
            "tolerances":  _avg_confidence(result.tolerances),
            "material":    result.material_inference.confidence if result.material_inference else 0.0,
        }
        result.overall_confidence = sum(weights[k] * scores[k] for k in weights)


def _avg_confidence(items: list) -> float:
    if not items:
        return 0.0
    return sum(getattr(i, "confidence", 0.0) for i in items) / len(items)


class RecoverableParseError(Exception):
    """Non-fatal error — pipeline continues, stage marked FAILED."""
```

### 2.2 Preprocessor — deskew, denoise, page split

```python
import numpy as np
import cv2
from PIL import Image


class DrawingPreprocessor:
    """
    Normalizes input to a canonical form before further parsing.
    Raster: deskew, denoise, contrast enhancement, DPI normalization.
    Vector: pass-through with metadata extraction.
    """

    TARGET_DPI = 300
    MAX_SKEW_DEG = 45.0

    async def process(self, path: Path, fmt: DrawingFormat) -> "PreprocessedDrawing":
        if fmt in (DrawingFormat.PNG, DrawingFormat.TIFF, DrawingFormat.JPEG):
            return await self._process_raster(path)
        elif fmt == DrawingFormat.PDF:
            return await self._process_pdf(path)
        else:
            return PreprocessedDrawing(path=path, format=fmt, pages=[])

    async def _process_raster(self, path: Path) -> "PreprocessedDrawing":
        img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        dpi = self._detect_dpi(path)

        # 1. Deskew
        skew_angle = self._detect_skew(img)
        if abs(skew_angle) > 0.5:
            img = self._rotate(img, skew_angle)

        # 2. Denoise (non-local means for technical drawings)
        img = cv2.fastNlMeansDenoising(img, h=10, templateWindowSize=7, searchWindowSize=21)

        # 3. Adaptive thresholding (binarization)
        img_bin = cv2.adaptiveThreshold(
            img, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )

        # 4. DPI normalization
        if dpi and dpi != self.TARGET_DPI:
            scale = self.TARGET_DPI / dpi
            w = int(img_bin.shape[1] * scale)
            h = int(img_bin.shape[0] * scale)
            img_bin = cv2.resize(img_bin, (w, h), interpolation=cv2.INTER_CUBIC)

        quality_score = self._assess_quality(img, dpi)
        return PreprocessedDrawing(
            path=path,
            format=DrawingFormat.PNG,
            pages=[img_bin],
            dpi=self.TARGET_DPI,
            quality_score=quality_score,
            skew_corrected_deg=skew_angle,
        )

    def _detect_skew(self, img: np.ndarray) -> float:
        """Hough line transform based skew detection."""
        edges = cv2.Canny(img, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, threshold=100)
        if lines is None:
            return 0.0
        angles = []
        for rho, theta in lines[:, 0]:
            angle = np.degrees(theta) - 90
            if abs(angle) < self.MAX_SKEW_DEG:
                angles.append(angle)
        return float(np.median(angles)) if angles else 0.0

    def _rotate(self, img: np.ndarray, angle_deg: float) -> np.ndarray:
        h, w = img.shape[:2]
        M = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
        return cv2.warpAffine(img, M, (w, h), flags=cv2.INTER_CUBIC,
                               borderMode=cv2.BORDER_REPLICATE)

    def _detect_dpi(self, path: Path) -> Optional[int]:
        try:
            with Image.open(path) as img:
                dpi_info = img.info.get("dpi")
                if dpi_info:
                    return int(dpi_info[0])
        except Exception:
            pass
        return None

    def _assess_quality(self, img: np.ndarray, dpi: Optional[int]) -> float:
        """Returns 0.0–1.0 quality score."""
        # Sharpness via Laplacian variance
        lap_var = cv2.Laplacian(img, cv2.CV_64F).var()
        sharpness = min(lap_var / 500.0, 1.0)

        # Contrast
        contrast = (img.max() - img.min()) / 255.0

        # DPI score
        dpi_score = min((dpi or 72) / 300.0, 1.0)

        return 0.4 * sharpness + 0.3 * contrast + 0.3 * dpi_score

    async def _process_pdf(self, path: Path) -> "PreprocessedDrawing":
        import fitz  # PyMuPDF
        doc = fitz.open(str(path))
        pages = []
        is_raster = True
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            # Check if page has vector content
            if len(page.get_drawings()) > 0:
                is_raster = False
            mat = fitz.Matrix(300 / 72, 300 / 72)  # render at 300 DPI
            pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
            img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w)
            pages.append(img)
        return PreprocessedDrawing(
            path=path, format=DrawingFormat.PDF, pages=pages,
            dpi=300, is_raster=is_raster,
        )


@dataclass
class PreprocessedDrawing:
    path: Path
    format: DrawingFormat
    pages: list[np.ndarray]
    dpi: int = 300
    is_raster: bool = False
    quality_score: float = 1.0
    skew_corrected_deg: float = 0.0
    text_elements: list[dict] = field(default_factory=list)
```

### 2.3 Format parsers

```python
from abc import ABC, abstractmethod


class FormatParser(ABC):
    @abstractmethod
    async def parse(self, preprocessed: PreprocessedDrawing) -> "ParsedDrawing":
        ...


class PDFParser(FormatParser):
    """Extracts vector paths, text, and annotations from PDF."""
    async def parse(self, preprocessed: PreprocessedDrawing) -> "ParsedDrawing":
        import fitz
        doc = fitz.open(str(preprocessed.path))
        text_elements = []
        vector_paths = []
        for page in doc:
            for block in page.get_text("dict")["blocks"]:
                if block["type"] == 0:  # text
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text_elements.append({
                                "text": span["text"],
                                "bbox": span["bbox"],
                                "size": span["size"],
                                "font": span["font"],
                            })
            for drawing in page.get_drawings():
                vector_paths.append(drawing)
        title_block = TitleBlockExtractor().extract(text_elements)
        return ParsedDrawing(
            text_elements=text_elements,
            vector_paths=vector_paths,
            title_block=title_block,
            is_raster=preprocessed.is_raster,
        )


class DXFParser(FormatParser):
    """Parses DXF/DWG using ezdxf."""
    async def parse(self, preprocessed: PreprocessedDrawing) -> "ParsedDrawing":
        import ezdxf
        doc = ezdxf.readfile(str(preprocessed.path))
        msp = doc.modelspace()
        entities = []
        text_elements = []
        for entity in msp:
            entities.append(entity)
            if entity.dxftype() in ("TEXT", "MTEXT"):
                text_elements.append({
                    "text": entity.dxf.text if hasattr(entity.dxf, "text") else entity.text,
                    "bbox": None,  # computed from insert + height
                    "layer": entity.dxf.layer,
                })
        title_block = TitleBlockExtractor().extract(text_elements)
        return ParsedDrawing(
            text_elements=text_elements,
            dxf_entities=entities,
            title_block=title_block,
            is_raster=False,
            units=str(doc.header.get("$INSUNITS", 4)),
        )


class STEPParser(FormatParser):
    """Parses STEP/IGES using pythonOCC or cadquery."""
    async def parse(self, preprocessed: PreprocessedDrawing) -> "ParsedDrawing":
        try:
            from OCC.Core.STEPControl import STEPControl_Reader
            from OCC.Core.IFSelect import IFSelect_RetDone
            reader = STEPControl_Reader()
            status = reader.ReadFile(str(preprocessed.path))
            if status != IFSelect_RetDone:
                raise RecoverableParseError(f"STEP read failed: {status}")
            reader.TransferRoots()
            shape = reader.OneShape()
            return ParsedDrawing(
                step_shape=shape,
                text_elements=[],
                is_raster=False,
            )
        except ImportError:
            raise RecoverableParseError("pythonOCC not available — STEP parsing disabled")


class FormatParserRegistry:
    def __init__(self):
        self._parsers: dict[DrawingFormat, FormatParser] = {
            DrawingFormat.PDF:  PDFParser(),
            DrawingFormat.DXF:  DXFParser(),
            DrawingFormat.DWG:  DXFParser(),   # ezdxf handles both
            DrawingFormat.STEP: STEPParser(),
            DrawingFormat.IGES: STEPParser(),  # OCC handles IGES too
        }

    def get(self, fmt: DrawingFormat) -> FormatParser:
        parser = self._parsers.get(fmt)
        if not parser:
            raise UnsupportedFormatError(f"No parser registered for format: {fmt}")
        return parser


@dataclass
class ParsedDrawing:
    text_elements: list[dict]
    title_block: Optional[TitleBlock] = None
    vector_paths: list[dict] = field(default_factory=list)
    dxf_entities: list = field(default_factory=list)
    step_shape: Any = None
    is_raster: bool = False
    units: str = "mm"
```

---

## 3. OCR Engine

### 3.1 Multi-engine OCR z fallback

```python
from dataclasses import dataclass
from typing import Protocol
import asyncio


@dataclass
class OCRWord:
    text: str
    confidence: float        # 0.0–1.0
    bbox: tuple[float, float, float, float]   # x0, y0, x1, y1 (pixels)
    page: int
    line_id: int


@dataclass
class OCRResult:
    words: list[OCRWord]
    raw_text: str
    language_detected: str
    engine_used: str
    mean_confidence: float
    low_confidence_ratio: float   # fraction of words below 0.7


class OCRBackend(Protocol):
    async def recognize(self, pages: list[np.ndarray]) -> list[OCRWord]:
        ...


class TesseractBackend:
    """
    Primary OCR engine — Tesseract 5.x with LSTM.
    Best for clean, high-contrast technical drawings.
    """
    TESSERACT_CONFIG = (
        "--oem 1 "                  # LSTM only
        "--psm 11 "                 # Sparse text
        "-c tessedit_char_whitelist='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
        "0123456789±×°Φ⌀√∅ .,:;/\\-+()[]{}=<>~_@#$%^&*'"
    )
    LANGS = "eng+deu+pol+chi_sim"

    async def recognize(self, pages: list[np.ndarray]) -> list[OCRWord]:
        import pytesseract
        words = []
        for page_idx, img in enumerate(pages):
            data = pytesseract.image_to_data(
                img,
                lang=self.LANGS,
                config=self.TESSERACT_CONFIG,
                output_type=pytesseract.Output.DICT,
            )
            for i, word_text in enumerate(data["text"]):
                if not word_text.strip():
                    continue
                conf = int(data["conf"][i]) / 100.0
                if conf < 0:
                    continue
                x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
                words.append(OCRWord(
                    text=word_text,
                    confidence=conf,
                    bbox=(x, y, x + w, y + h),
                    page=page_idx,
                    line_id=data["line_num"][i],
                ))
        return words


class PaddleOCRBackend:
    """
    Fallback OCR — PaddleOCR 2.x.
    Better for rotated text, stamps, non-standard fonts.
    """
    def __init__(self):
        from paddleocr import PaddleOCR
        self._ocr = PaddleOCR(use_angle_cls=True, lang="en", use_gpu=True, show_log=False)

    async def recognize(self, pages: list[np.ndarray]) -> list[OCRWord]:
        words = []
        for page_idx, img in enumerate(pages):
            result = self._ocr.ocr(img, cls=True)
            if not result or not result[0]:
                continue
            for line in result[0]:
                box, (text, conf) = line
                x0 = min(p[0] for p in box)
                y0 = min(p[1] for p in box)
                x1 = max(p[0] for p in box)
                y1 = max(p[1] for p in box)
                words.append(OCRWord(
                    text=text, confidence=conf,
                    bbox=(x0, y0, x1, y1),
                    page=page_idx, line_id=0,
                ))
        return words


class DocumentAIBackend:
    """
    Premium fallback — Google Document AI or Azure Form Recognizer.
    Used when local OCR confidence is below threshold.
    """
    CONFIDENCE_THRESHOLD = 0.65

    def __init__(self, provider: str = "google", api_key: str = ""):
        self.provider = provider
        self.api_key = api_key

    async def recognize(self, pages: list[np.ndarray]) -> list[OCRWord]:
        if self.provider == "google":
            return await self._google_document_ai(pages)
        return await self._azure_form_recognizer(pages)

    async def _google_document_ai(self, pages: list[np.ndarray]) -> list[OCRWord]:
        # Placeholder — real implementation calls Document AI REST
        raise NotImplementedError("Google Document AI backend")

    async def _azure_form_recognizer(self, pages: list[np.ndarray]) -> list[OCRWord]:
        raise NotImplementedError("Azure Form Recognizer backend")


class OCREngine:
    """
    Multi-engine OCR with automatic quality-based fallback.
    Strategy: Tesseract → PaddleOCR (if low confidence) → DocumentAI (if still low).
    """
    FALLBACK_THRESHOLD = 0.70    # mean confidence below this triggers fallback
    CLOUD_THRESHOLD    = 0.55    # below this escalates to cloud OCR

    def __init__(self, use_cloud: bool = False, cloud_api_key: str = ""):
        self.primary    = TesseractBackend()
        self.secondary  = PaddleOCRBackend()
        self.cloud      = DocumentAIBackend(api_key=cloud_api_key) if use_cloud else None

    async def run(self, preprocessed: PreprocessedDrawing) -> OCRResult:
        words = await self.primary.recognize(preprocessed.pages)
        engine = "tesseract"
        mean_conf = _mean_confidence(words)

        if mean_conf < self.FALLBACK_THRESHOLD:
            words_paddle = await self.secondary.recognize(preprocessed.pages)
            if _mean_confidence(words_paddle) > mean_conf:
                words = words_paddle
                engine = "paddleocr"
                mean_conf = _mean_confidence(words)

        if mean_conf < self.CLOUD_THRESHOLD and self.cloud:
            words_cloud = await self.cloud.recognize(preprocessed.pages)
            if _mean_confidence(words_cloud) > mean_conf:
                words = words_cloud
                engine = "documentai"
                mean_conf = _mean_confidence(words)

        low_conf_ratio = sum(1 for w in words if w.confidence < 0.7) / max(len(words), 1)
        return OCRResult(
            words=words,
            raw_text=" ".join(w.text for w in words),
            language_detected=self._detect_language(words),
            engine_used=engine,
            mean_confidence=mean_conf,
            low_confidence_ratio=low_conf_ratio,
        )

    def _detect_language(self, words: list[OCRWord]) -> str:
        # Simple heuristic — extend with langdetect if needed
        return "en"


def _mean_confidence(words: list[OCRWord]) -> float:
    if not words:
        return 0.0
    return sum(w.confidence for w in words) / len(words)
```

### 3.2 Title Block Extractor

```python
import re


class TitleBlockExtractor:
    """
    Locates and parses the title block from OCR text elements or vector text.
    Uses spatial heuristics: title blocks are typically bottom-right quadrant.
    """

    FIELD_PATTERNS = {
        "part_number": [
            r"(?:part\s*(?:no|number|nr)[:\s.]+)([A-Z0-9\-_.]+)",
            r"(?:DRG[:\s.]+)([A-Z0-9\-_.]+)",
            r"(?:PART[:\s]+)([A-Z0-9\-_.]+)",
        ],
        "revision": [
            r"(?:rev(?:ision)?[:\s.]+)([A-Z0-9]+)",
            r"(?:^|\s)REV[:\s.]+([A-Z0-9]+)",
        ],
        "material": [
            r"(?:material[:\s.]+)([A-Z0-9\s\-_.]+?)(?:\n|$)",
            r"(?:WERKSTOFF[:\s.]+)([A-Z0-9\s\-_.]+?)(?:\n|$)",
            r"(?:MAT[:\s.]+)([A-Z0-9\s\-_.]+?)(?:\n|$)",
        ],
        "surface_finish": [
            r"(?:surface\s+finish[:\s.]+)([^\n]+)",
            r"(?:Ra\s*[=:]\s*)([\d.,]+)",
            r"(?:Rz\s*[=:]\s*)([\d.,]+)",
        ],
        "scale": [
            r"(?:scale[:\s.]+)([\d:./]+)",
            r"(?:MASSTAb[:\s.]+)([\d:./]+)",
        ],
        "mass_kg": [
            r"(?:weight|mass|gewicht)[:\s.]+([0-9.,]+)\s*(?:kg|g)",
        ],
        "unit": [
            r"(?:dimensions\s+in|all\s+dims?\s+in|units?[:\s.]+)(mm|cm|inch|in\b)",
        ],
    }

    def extract(self, text_elements: list[dict]) -> TitleBlock:
        full_text = "\n".join(e.get("text", "") for e in text_elements)
        fields: dict = {}
        confidence_sum = 0.0
        matched = 0

        for field_name, patterns in self.FIELD_PATTERNS.items():
            for pattern in patterns:
                m = re.search(pattern, full_text, re.IGNORECASE)
                if m:
                    fields[field_name] = m.group(1).strip()
                    confidence_sum += 1.0
                    matched += 1
                    break

        confidence = confidence_sum / len(self.FIELD_PATTERNS) if self.FIELD_PATTERNS else 0.0

        mass_raw = fields.get("mass_kg")
        mass_kg = None
        if mass_raw:
            try:
                mass_kg = float(mass_raw.replace(",", "."))
            except ValueError:
                pass

        return TitleBlock(
            part_number=fields.get("part_number"),
            part_name=self._extract_part_name(text_elements),
            revision=fields.get("revision"),
            material=fields.get("material"),
            surface_finish=fields.get("surface_finish"),
            scale=fields.get("scale"),
            projection_method=self._detect_projection(full_text),
            drawn_by=None,
            checked_by=None,
            approved_by=None,
            drawing_date=None,
            company=None,
            sheet_number=None,
            mass_kg=mass_kg,
            unit=fields.get("unit", "mm"),
            confidence=confidence,
        )

    def _extract_part_name(self, elements: list[dict]) -> Optional[str]:
        # Largest font size text element is typically the title
        sized = [(e.get("size", 0), e.get("text", "")) for e in elements if e.get("text")]
        if not sized:
            return None
        return max(sized, key=lambda x: x[0])[1].strip() or None

    def _detect_projection(self, text: str) -> Optional[str]:
        if re.search(r"third.angle|3rd.angle|ANSI", text, re.I):
            return "3rd angle"
        if re.search(r"first.angle|1st.angle|ISO", text, re.I):
            return "1st angle"
        return None
```

---

## 4. Geometry Extraction

### 4.1 2D Geometry Extractor

```python
from decimal import Decimal
from typing import Union
import math


@dataclass
class Point2D:
    x: float
    y: float


@dataclass
class Line2D:
    start: Point2D
    end: Point2D
    layer: str = ""
    length: float = 0.0

    def __post_init__(self):
        self.length = math.dist((self.start.x, self.start.y), (self.end.x, self.end.y))


@dataclass
class Arc2D:
    center: Point2D
    radius: float
    start_angle: float   # degrees
    end_angle: float     # degrees
    layer: str = ""


@dataclass
class Circle2D:
    center: Point2D
    radius: float
    layer: str = ""


@dataclass
class Polyline2D:
    points: list[Point2D]
    closed: bool = False
    layer: str = ""


@dataclass
class ExtractedDimension:
    dim_type: str         # LINEAR / ANGULAR / RADIAL / DIAMETER / ORDINATE / LEADER
    value: float
    unit: str             # mm / inch / deg
    tolerance_upper: Optional[float]
    tolerance_lower: Optional[float]
    text_raw: str
    confidence: float
    source: str           # DXF_ENTITY / OCR_REGEX / CV_MODEL
    bbox: Optional[tuple[float, float, float, float]]


@dataclass
class GeometryResult:
    lines: list[Line2D]
    arcs: list[Arc2D]
    circles: list[Circle2D]
    polylines: list[Polyline2D]
    dimensions: list[ExtractedDimension]
    bounding_box: tuple[float, float, float, float]  # xmin, ymin, xmax, ymax
    unit: str
    confidence: float


class GeometryExtractor:
    """
    Extracts 2D geometric primitives and dimensions from parsed drawings.
    Supports DXF entities (exact) and PDF/raster (OCR + CV-assisted).
    """

    DIMENSION_PATTERN = re.compile(
        r"([⌀Φ∅R])?\s*"
        r"([\d]+(?:[.,][\d]+)?)"
        r"(?:\s*[±+\-]\s*([\d]+(?:[.,][\d]+)?))?"
        r"(?:\s*/\s*([\d]+(?:[.,][\d]+)?))?"
        r"\s*(?:(mm|in|inch|°|deg))?"
    )

    async def extract(self, parsed: "ParsedDrawing", ocr: Optional[OCRResult] = None) -> GeometryResult:
        if parsed.dxf_entities:
            return await self._from_dxf(parsed)
        elif parsed.vector_paths:
            return await self._from_vector(parsed, ocr)
        else:
            return await self._from_ocr_only(ocr)

    async def _from_dxf(self, parsed: "ParsedDrawing") -> GeometryResult:
        import ezdxf
        lines, arcs, circles, polylines, dims = [], [], [], [], []

        for entity in parsed.dxf_entities:
            t = entity.dxftype()
            if t == "LINE":
                lines.append(Line2D(
                    start=Point2D(entity.dxf.start.x, entity.dxf.start.y),
                    end=Point2D(entity.dxf.end.x, entity.dxf.end.y),
                    layer=entity.dxf.layer,
                ))
            elif t == "ARC":
                arcs.append(Arc2D(
                    center=Point2D(entity.dxf.center.x, entity.dxf.center.y),
                    radius=entity.dxf.radius,
                    start_angle=entity.dxf.start_angle,
                    end_angle=entity.dxf.end_angle,
                    layer=entity.dxf.layer,
                ))
            elif t == "CIRCLE":
                circles.append(Circle2D(
                    center=Point2D(entity.dxf.center.x, entity.dxf.center.y),
                    radius=entity.dxf.radius,
                    layer=entity.dxf.layer,
                ))
            elif t == "LWPOLYLINE":
                pts = [Point2D(v[0], v[1]) for v in entity.get_points()]
                polylines.append(Polyline2D(pts, closed=entity.closed, layer=entity.dxf.layer))
            elif t in ("DIMENSION", "QDIM"):
                dims.append(self._parse_dxf_dimension(entity))

        bbox = self._compute_bbox(lines, arcs, circles, polylines)
        return GeometryResult(
            lines=lines, arcs=arcs, circles=circles, polylines=polylines,
            dimensions=[d for d in dims if d is not None],
            bounding_box=bbox,
            unit=parsed.units or "mm",
            confidence=0.95,
        )

    def _parse_dxf_dimension(self, entity) -> Optional[ExtractedDimension]:
        try:
            text = entity.dxf.text or entity.get_measurement()
            value = entity.get_measurement()
            dim_type = self._classify_dxf_dim_type(entity.dimtype)
            return ExtractedDimension(
                dim_type=dim_type,
                value=float(value),
                unit="mm",
                tolerance_upper=None,
                tolerance_lower=None,
                text_raw=str(text),
                confidence=0.90,
                source="DXF_ENTITY",
                bbox=None,
            )
        except Exception:
            return None

    def _classify_dxf_dim_type(self, dimtype: int) -> str:
        mapping = {0: "LINEAR", 1: "ALIGNED", 2: "ANGULAR", 3: "DIAMETER",
                   4: "RADIAL", 5: "ANGULAR_3POINT", 6: "ORDINATE"}
        return mapping.get(dimtype & 7, "LINEAR")

    async def _from_ocr_only(self, ocr: Optional[OCRResult]) -> GeometryResult:
        dims = []
        if ocr:
            dims = self._extract_dims_from_ocr(ocr.words)
        return GeometryResult(
            lines=[], arcs=[], circles=[], polylines=[],
            dimensions=dims,
            bounding_box=(0, 0, 0, 0),
            unit="mm",
            confidence=0.50,
        )

    def _extract_dims_from_ocr(self, words: list[OCRWord]) -> list[ExtractedDimension]:
        dims = []
        for word in words:
            m = self.DIMENSION_PATTERN.search(word.text)
            if not m:
                continue
            prefix = m.group(1) or ""
            value_str = m.group(2).replace(",", ".")
            tol_str = m.group(3)
            unit_str = m.group(5) or "mm"
            try:
                value = float(value_str)
            except ValueError:
                continue
            dim_type = "DIAMETER" if prefix in ("⌀", "Φ", "∅") else \
                       "RADIAL" if prefix == "R" else \
                       "ANGULAR" if "°" in unit_str else "LINEAR"
            dims.append(ExtractedDimension(
                dim_type=dim_type,
                value=value,
                unit=unit_str,
                tolerance_upper=float(tol_str.replace(",", ".")) if tol_str else None,
                tolerance_lower=(-float(tol_str.replace(",", ".")) if tol_str else None),
                text_raw=word.text,
                confidence=word.confidence * 0.85,  # OCR uncertainty discount
                source="OCR_REGEX",
                bbox=word.bbox,
            ))
        return dims

    async def _from_vector(self, parsed: "ParsedDrawing", ocr: Optional[OCRResult]) -> GeometryResult:
        # PDF vector paths -> line/arc heuristics
        lines, circles, arcs = [], [], []
        for path in parsed.vector_paths:
            for item in path.get("items", []):
                if item[0] == "l":  # line
                    p1, p2 = item[1], item[2]
                    lines.append(Line2D(Point2D(*p1), Point2D(*p2)))
        dims = []
        if ocr:
            dims = self._extract_dims_from_ocr(ocr.words)
        return GeometryResult(
            lines=lines, arcs=arcs, circles=circles, polylines=[],
            dimensions=dims,
            bounding_box=self._compute_bbox(lines, arcs, circles, []),
            unit="mm",
            confidence=0.70,
        )

    def _compute_bbox(self, lines, arcs, circles, polylines) -> tuple:
        xs, ys = [], []
        for l in lines:
            xs += [l.start.x, l.end.x]; ys += [l.start.y, l.end.y]
        for c in circles:
            xs += [c.center.x - c.radius, c.center.x + c.radius]
            ys += [c.center.y - c.radius, c.center.y + c.radius]
        for a in arcs:
            xs += [a.center.x]; ys += [a.center.y]
        for p in polylines:
            xs += [pt.x for pt in p.points]; ys += [pt.y for pt in p.points]
        if not xs:
            return (0, 0, 0, 0)
        return (min(xs), min(ys), max(xs), max(ys))
```

---

## 5. Feature Detection

### 5.1 Manufacturing Feature Detector

```python
from dataclasses import dataclass
import math


class FeatureType(str, Enum):
    HOLE_THRU         = "HOLE_THRU"
    HOLE_BLIND        = "HOLE_BLIND"
    HOLE_COUNTERSINK  = "HOLE_COUNTERSINK"
    HOLE_COUNTERBORE  = "HOLE_COUNTERBORE"
    THREAD_INTERNAL   = "THREAD_INTERNAL"
    THREAD_EXTERNAL   = "THREAD_EXTERNAL"
    POCKET            = "POCKET"
    SLOT              = "SLOT"
    FILLET            = "FILLET"
    CHAMFER           = "CHAMFER"
    BOSS              = "BOSS"
    RIB               = "RIB"
    UNDERCUT          = "UNDERCUT"
    KNURL             = "KNURL"
    GROOVE            = "GROOVE"
    WELD_JOINT        = "WELD_JOINT"
    BEND              = "BEND"           # sheet metal
    EMBOSS            = "EMBOSS"
    DRAFT_ANGLE       = "DRAFT_ANGLE"   # casting
    PARTING_LINE      = "PARTING_LINE"


@dataclass
class DetectedFeature:
    feature_type: FeatureType
    location: Optional[Point2D]
    parameters: dict[str, float]     # diameter, depth, angle, pitch, etc.
    count: int = 1
    confidence: float = 0.0
    source: str = ""                  # GEOMETRY / OCR / CV_MODEL
    notes: list[str] = field(default_factory=list)


class FeatureDetector:
    """
    Detects manufacturing features from 2D geometry + OCR annotations.
    Combines rule-based geometry matching with CV model predictions.
    """

    THREAD_PATTERN    = re.compile(r"M(\d+(?:\.\d+)?)\s*(?:x\s*([\d.]+))?(?:\s*-\s*(6[Hgh]|4[Hh]))?\s*(?:THRU|THR)?", re.I)
    COUNTERSINK_PAT   = re.compile(r"(?:CSK|⌵|CSINK)\s*([\d.]+)?°?", re.I)
    COUNTERBORE_PAT   = re.compile(r"(?:CBORE|C'BORE|⌴)\s*⌀([\d.]+)\s*DEEP\s*([\d.]+)", re.I)
    HOLE_PATTERN      = re.compile(r"(?:⌀|Φ)([\d.]+)\s*(?:THRU|THR|BLIND\s*([\d.]+))?", re.I)
    FILLET_PATTERN    = re.compile(r"R\s*([\d.]+)\s*(?:ALL|UNLESS\s+NOTED)?", re.I)
    CHAMFER_PATTERN   = re.compile(r"([\d.]+)\s*[×xX]\s*([\d.]+)?°?\s*CHAM", re.I)
    WELD_PATTERN      = re.compile(r"(?:WELD|⌒|△|▽|\bFW\b|\bBW\b|\bFP\b)", re.I)
    BEND_PATTERN      = re.compile(r"BEND\s*R\s*([\d.]+)\s*@\s*([\d.]+)°", re.I)

    def __init__(self, cv_model: Optional["FeatureDetectionModel"] = None):
        self.cv_model = cv_model

    async def detect(self, geometry: GeometryResult, parsed: "ParsedDrawing") -> list[DetectedFeature]:
        features = []

        # Rule-based from geometry
        features += self._detect_holes_from_circles(geometry.circles)
        features += self._detect_slots(geometry.lines, geometry.arcs)

        # Rule-based from OCR/text annotations
        all_text = " ".join(e.get("text", "") for e in parsed.text_elements)
        features += self._detect_from_text(all_text)

        # CV model (optional, high-latency)
        if self.cv_model:
            cv_features = await self.cv_model.detect(parsed)
            features = self._merge_features(features, cv_features)

        return self._deduplicate(features)

    def _detect_holes_from_circles(self, circles: list[Circle2D]) -> list[DetectedFeature]:
        features = []
        for circle in circles:
            diameter = circle.radius * 2
            features.append(DetectedFeature(
                feature_type=FeatureType.HOLE_THRU,
                location=circle.center,
                parameters={"diameter": diameter},
                confidence=0.80,
                source="GEOMETRY",
            ))
        return features

    def _detect_slots(self, lines: list[Line2D], arcs: list[Arc2D]) -> list[DetectedFeature]:
        features = []
        # Slot = parallel lines connected by semicircular arcs at ends
        # Simplified: look for arcs with radius ~= parallel line spacing / 2
        # Full implementation requires geometric clustering
        for arc in arcs:
            if abs(arc.end_angle - arc.start_angle) >= 160:  # near-semicircle
                features.append(DetectedFeature(
                    feature_type=FeatureType.SLOT,
                    location=arc.center,
                    parameters={"radius": arc.radius},
                    confidence=0.60,
                    source="GEOMETRY",
                ))
        return features

    def _detect_from_text(self, text: str) -> list[DetectedFeature]:
        features = []

        for m in self.THREAD_PATTERN.finditer(text):
            diameter = float(m.group(1))
            pitch = float(m.group(2)) if m.group(2) else self._iso_coarse_pitch(diameter)
            tolerance = m.group(3) or "6H"
            features.append(DetectedFeature(
                feature_type=FeatureType.THREAD_INTERNAL,
                location=None,
                parameters={"nominal_diameter": diameter, "pitch": pitch},
                confidence=0.85,
                source="OCR_REGEX",
                notes=[f"tolerance class: {tolerance}"],
            ))

        for m in self.HOLE_PATTERN.finditer(text):
            diameter = float(m.group(1))
            depth_str = m.group(2)
            ftype = FeatureType.HOLE_BLIND if depth_str else FeatureType.HOLE_THRU
            params = {"diameter": diameter}
            if depth_str:
                params["depth"] = float(depth_str)
            features.append(DetectedFeature(
                feature_type=ftype,
                location=None,
                parameters=params,
                confidence=0.82,
                source="OCR_REGEX",
            ))

        for m in self.COUNTERSINK_PAT.finditer(text):
            angle = float(m.group(1)) if m.group(1) else 90.0
            features.append(DetectedFeature(
                feature_type=FeatureType.HOLE_COUNTERSINK,
                location=None,
                parameters={"angle_deg": angle},
                confidence=0.80,
                source="OCR_REGEX",
            ))

        for m in self.COUNTERBORE_PAT.finditer(text):
            features.append(DetectedFeature(
                feature_type=FeatureType.HOLE_COUNTERBORE,
                location=None,
                parameters={"diameter": float(m.group(1)), "depth": float(m.group(2))},
                confidence=0.82,
                source="OCR_REGEX",
            ))

        for m in self.FILLET_PATTERN.finditer(text):
            features.append(DetectedFeature(
                feature_type=FeatureType.FILLET,
                location=None,
                parameters={"radius": float(m.group(1))},
                confidence=0.75,
                source="OCR_REGEX",
            ))

        for m in self.CHAMFER_PATTERN.finditer(text):
            features.append(DetectedFeature(
                feature_type=FeatureType.CHAMFER,
                location=None,
                parameters={"size": float(m.group(1))},
                confidence=0.75,
                source="OCR_REGEX",
            ))

        if self.WELD_PATTERN.search(text):
            features.append(DetectedFeature(
                feature_type=FeatureType.WELD_JOINT,
                location=None,
                parameters={},
                confidence=0.70,
                source="OCR_REGEX",
            ))

        for m in self.BEND_PATTERN.finditer(text):
            features.append(DetectedFeature(
                feature_type=FeatureType.BEND,
                location=None,
                parameters={"radius": float(m.group(1)), "angle_deg": float(m.group(2))},
                confidence=0.80,
                source="OCR_REGEX",
            ))

        return features

    def _iso_coarse_pitch(self, diameter: float) -> float:
        """ISO metric coarse pitch lookup."""
        pitches = {1: 0.25, 1.2: 0.25, 1.4: 0.3, 1.6: 0.35, 2: 0.4,
                   2.5: 0.45, 3: 0.5, 3.5: 0.6, 4: 0.7, 5: 0.8,
                   6: 1.0, 8: 1.25, 10: 1.5, 12: 1.75, 16: 2.0,
                   20: 2.5, 24: 3.0, 30: 3.5, 36: 4.0, 42: 4.5,
                   48: 5.0, 56: 5.5, 64: 6.0}
        diam = min(pitches, key=lambda d: abs(d - diameter))
        return pitches[diam]

    def _merge_features(self, rule_based: list, cv: list) -> list:
        """Merge rule-based and CV-model features, boosting confidence when both agree."""
        merged = list(rule_based)
        for cv_feat in cv:
            found = False
            for rb_feat in merged:
                if rb_feat.feature_type == cv_feat.feature_type:
                    rb_feat.confidence = min(0.99, rb_feat.confidence + 0.10)
                    found = True
                    break
            if not found:
                merged.append(cv_feat)
        return merged

    def _deduplicate(self, features: list[DetectedFeature]) -> list[DetectedFeature]:
        """Group identical features and count them."""
        grouped: dict[tuple, DetectedFeature] = {}
        for feat in features:
            key = (feat.feature_type, round(feat.parameters.get("diameter", 0), 2),
                   round(feat.parameters.get("depth", 0), 2))
            if key in grouped:
                grouped[key].count += 1
            else:
                grouped[key] = feat
        return list(grouped.values())
```

### 5.2 3D Feature Detection from STEP

```python
class STEPFeatureDetector:
    """
    Detects manufacturing features from STEP topology using pythonOCC.
    Identifies holes, pockets, bosses from B-Rep face analysis.
    """

    async def detect(self, shape) -> list[DetectedFeature]:
        try:
            from OCC.Core.BRepAdaptor import BRepAdaptor_Surface
            from OCC.Core.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
            from OCC.Core.TopExp import TopExp_Explorer
            from OCC.Core.TopAbs import TopAbs_FACE
        except ImportError:
            return []

        features = []
        explorer = TopExp_Explorer(shape, TopAbs_FACE)
        while explorer.More():
            face = explorer.Current()
            adaptor = BRepAdaptor_Surface(face)
            surf_type = adaptor.GetType()

            if surf_type == GeomAbs_Cylinder:
                radius = adaptor.Cylinder().Radius()
                features.append(DetectedFeature(
                    feature_type=FeatureType.HOLE_THRU,
                    location=None,
                    parameters={"diameter": radius * 2},
                    confidence=0.90,
                    source="STEP_TOPOLOGY",
                ))

            explorer.Next()

        return self._deduplicate_3d(features)

    def _deduplicate_3d(self, features: list) -> list:
        return features
```
