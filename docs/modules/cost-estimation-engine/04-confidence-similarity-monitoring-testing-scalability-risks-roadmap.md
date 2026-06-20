# Cost Estimation Engine — Confidence, Similarity Injection, Monitoring, Testing, Scalability, Risks, Roadmap

## 16. Confidence Scoring

### 16.1 Model pewności — definicja

Confidence Score CEE ocenia wiarygodność obliczonej wyceny na skali 0–1.
Składa się z 5 komponentów ważonych addytywnie.

| Komponent | Waga | Opis |
|-----------|------|------|
| `formula_coverage` | 0.30 | Czy wszystkie parametry formuły są dostępne i kompletne |
| `ml_confidence` | 0.25 | Jak daleko punkt leży od zbioru treningowego (OOD detection) |
| `data_freshness` | 0.20 | Aktualność cen materiałów i stawek maszynowych |
| `similarity_support` | 0.15 | Liczba i jakość podobnych wycen z SCSE |
| `process_coverage` | 0.10 | Czy wszystkie procesy mają skalibrowane parametry |

**Progi:**
- `HIGH` (≥ 0.85): MAPE < 5% historycznie → dokładna wycena
- `MEDIUM` (0.70–0.84): MAPE 5–15% → wycena standardowa
- `LOW` (0.55–0.69): MAPE 15–30% → wycena orientacyjna
- `INDICATIVE` (< 0.55): MAPE > 30% → tylko wskaźnik

### 16.2 Implementacja

```python
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from enum import Enum
import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .domain import EstimationInput, CostEstimate

class ConfidenceLevel(Enum):
    HIGH       = "HIGH"
    MEDIUM     = "MEDIUM"
    LOW        = "LOW"
    INDICATIVE = "INDICATIVE"

@dataclass
class ConfidenceScore:
    overall:             float         # 0–1
    level:               ConfidenceLevel
    formula_coverage:    float
    ml_confidence:       float
    data_freshness:      float
    similarity_support:  float
    process_coverage:    float
    reasons:             list[str] = field(default_factory=list)
    warnings:            list[str] = field(default_factory=list)

    WEIGHTS = {
        "formula_coverage":   0.30,
        "ml_confidence":      0.25,
        "data_freshness":     0.20,
        "similarity_support": 0.15,
        "process_coverage":   0.10,
    }

    def to_dict(self) -> dict:
        return {
            "overall":            round(self.overall, 4),
            "level":              self.level.value,
            "components": {
                "formula_coverage":   round(self.formula_coverage, 4),
                "ml_confidence":      round(self.ml_confidence, 4),
                "data_freshness":     round(self.data_freshness, 4),
                "similarity_support": round(self.similarity_support, 4),
                "process_coverage":   round(self.process_coverage, 4),
            },
            "reasons":  self.reasons,
            "warnings": self.warnings,
        }

class CEEConfidenceCalculator:

    MACHINE_RATE_DB = {
        "CNC_LATHE", "CNC_MILL_3AXIS", "CNC_MILL_5AXIS", "PRESS_BRAKE",
        "LASER_CUT", "WATERJET", "EDM_WIRE", "INJECTION_MOULD",
        "FORGING_PRESS", "SURFACE_GRINDER", "CYLINDRICAL_GRINDER",
        "MIG_WELDER", "TIG_WELDER",
    }

    def calculate(
        self,
        inp: "EstimationInput",
        estimate: "CostEstimate",
        ml_ood_score: float,
        n_similar: int,
        similar_confidence: float,
        material_price_age_hours: float,
    ) -> ConfidenceScore:
        reasons:  list[str] = []
        warnings: list[str] = []

        fc = self._formula_coverage(inp, reasons, warnings)
        mc = self._ml_confidence(ml_ood_score, reasons, warnings)
        df = self._data_freshness(material_price_age_hours, reasons, warnings)
        ss = self._similarity_support(n_similar, similar_confidence, reasons)
        pc = self._process_coverage(inp, reasons, warnings)

        w  = ConfidenceScore.WEIGHTS
        overall = (
            fc * w["formula_coverage"]   +
            mc * w["ml_confidence"]      +
            df * w["data_freshness"]     +
            ss * w["similarity_support"] +
            pc * w["process_coverage"]
        )
        overall = float(np.clip(overall, 0.0, 1.0))
        level   = self._classify(overall)

        return ConfidenceScore(
            overall=overall, level=level,
            formula_coverage=fc, ml_confidence=mc, data_freshness=df,
            similarity_support=ss, process_coverage=pc,
            reasons=reasons, warnings=warnings,
        )

    def _formula_coverage(
        self, inp: "EstimationInput",
        reasons: list, warnings: list,
    ) -> float:
        score = 1.0
        g = inp.geometry

        if not g.volume_cm3 or g.volume_cm3 <= 0:
            score -= 0.30; warnings.append("volume_cm3 missing")
        if not inp.material.price_eur_per_kg:
            score -= 0.25; warnings.append("material price not available — using fallback")
        if not inp.material.density_g_cm3:
            score -= 0.10; warnings.append("density not set — using default")
        if not inp.process_steps:
            score -= 0.25; warnings.append("no process steps defined")
        if g.complexity_class == "VERY_COMPLEX" and g.feature_count == 0:
            score -= 0.10; warnings.append("VERY_COMPLEX part with feature_count=0")
        if score >= 0.90:
            reasons.append("all formula inputs present")
        return float(np.clip(score, 0.0, 1.0))

    def _ml_confidence(
        self, ood_score: float, reasons: list, warnings: list
    ) -> float:
        # ood_score: 0 = in-distribution, 1 = fully out-of-distribution
        score = 1.0 - float(np.clip(ood_score, 0.0, 1.0))
        if ood_score > 0.70:
            warnings.append(f"ML OOD score={ood_score:.2f}: part outside training distribution")
        elif ood_score > 0.40:
            warnings.append(f"ML OOD score={ood_score:.2f}: borderline distribution")
        else:
            reasons.append("input within ML training distribution")
        return score

    def _data_freshness(
        self, price_age_hours: float, reasons: list, warnings: list
    ) -> float:
        if price_age_hours <= 4:
            reasons.append("material price fresh (<4h)")
            return 1.0
        elif price_age_hours <= 24:
            warnings.append(f"material price {price_age_hours:.0f}h old")
            return 0.80
        elif price_age_hours <= 72:
            warnings.append(f"material price {price_age_hours:.0f}h old — using stale cache")
            return 0.60
        else:
            warnings.append(f"material price >72h old — historical fallback")
            return 0.35

    def _similarity_support(
        self, n_similar: int, sim_confidence: float,
        reasons: list,
    ) -> float:
        if n_similar == 0:
            return 0.50
        elif n_similar < 3:
            reasons.append(f"{n_similar} similar quote(s) found (weak support)")
            return 0.60 + sim_confidence * 0.15
        elif n_similar < 10:
            reasons.append(f"{n_similar} similar quotes found")
            return 0.75 + sim_confidence * 0.20
        else:
            reasons.append(f"{n_similar} similar quotes found (strong support)")
            return min(0.85 + sim_confidence * 0.15, 1.0)

    def _process_coverage(
        self, inp: "EstimationInput", reasons: list, warnings: list
    ) -> float:
        if not inp.process_steps:
            return 0.50
        known = sum(
            1 for s in inp.process_steps
            if s.machine_type in self.MACHINE_RATE_DB
        )
        ratio = known / max(len(inp.process_steps), 1)
        if ratio < 0.50:
            warnings.append(f"only {known}/{len(inp.process_steps)} processes have calibrated rates")
        elif ratio < 1.0:
            warnings.append(f"{len(inp.process_steps) - known} process(es) using estimated rates")
        else:
            reasons.append("all process steps have calibrated machine rates")
        return round(0.50 + ratio * 0.50, 4)

    @staticmethod
    def _classify(score: float) -> ConfidenceLevel:
        if score >= 0.85:  return ConfidenceLevel.HIGH
        if score >= 0.70:  return ConfidenceLevel.MEDIUM
        if score >= 0.55:  return ConfidenceLevel.LOW
        return ConfidenceLevel.INDICATIVE
```

### 16.3 OOD Detection

```python
from sklearn.neighbors import LocalOutlierFactor
import joblib

class OODDetector:
    """
    Local Outlier Factor trained on historical feature vectors.
    Returns anomaly score 0 (normal) → 1 (extreme outlier).
    """

    def __init__(self, n_neighbors: int = 20, contamination: float = 0.05):
        self._lof = LocalOutlierFactor(
            n_neighbors=n_neighbors,
            contamination=contamination,
            novelty=True,
        )
        self._fitted = False

    def fit(self, X_train: "np.ndarray") -> None:
        self._lof.fit(X_train)
        self._fitted = True

    def score(self, x: "np.ndarray") -> float:
        if not self._fitted:
            return 0.5
        raw = self._lof.score_samples(x.reshape(1, -1))[0]
        # LOF returns negative scores; more negative = more anomalous
        # Normalize to [0,1]: 0=normal, 1=outlier
        clipped = float(np.clip(-raw, 1.0, 5.0))
        return round((clipped - 1.0) / 4.0, 4)

    def save(self, path: str) -> None:
        joblib.dump(self._lof, path)

    @classmethod
    def load(cls, path: str) -> "OODDetector":
        detector = cls()
        detector._lof   = joblib.load(path)
        detector._fitted = True
        return detector
```

---

## 17. Similarity Injection Layer

### 17.1 Cel

Similarity Injection Layer (SIL) integruje wyniki wyszukiwania SCSE (Similarity Cost Search Engine)
jako zewnętrzne kotwice bayesowskie do kalibracji wyceny formułowej i ML.

**Mechanizm:**
1. Wywołaj SCSE `/search/quotes` z wektorem cechy bieżącego produktu
2. Pobierz top-K cytatów (`HIGHLY_SIMILAR` i `SIMILAR`, threshold ≥ 0.70)
3. Bayesowska aktualizacja rozkładu kosztu: prior (formuła) × likelihood (podobne ceny)
4. Weighted blend: `final = α × formula_ml_cost + (1-α) × similarity_anchor`
5. Walidacja: odrzuć kotwice odstające (IQR filtering)

### 17.2 Implementacja

```python
import httpx
import numpy as np
from dataclasses import dataclass
from scipy import stats as scipy_stats

@dataclass
class SimilarityAnchor:
    quote_id:           str
    unit_cost_eur:      float
    similarity_score:   float
    volume:             int
    production_location: str
    confidence_level:   str
    date_quoted:        str | None

class SimilarityCostInjector:
    """
    Calls SCSE to find similar historical quotes, then Bayesian-blends
    their costs into the current estimate.
    """

    SCSE_SEARCH_URL = "http://scse.internal/v1/search/quotes"
    SIMILARITY_THRESHOLD = 0.70
    MAX_ANCHORS = 20
    MIN_ANCHORS_FOR_BLEND = 3

    def __init__(self, http_client: httpx.AsyncClient, jwt_token: str):
        self._http   = http_client
        self._token  = jwt_token

    async def fetch_anchors(
        self,
        feature_vector: list[float],
        production_location: str,
        annual_volume: int,
    ) -> list[SimilarityAnchor]:
        payload = {
            "query_vector":    feature_vector,
            "entity_type":     "QUOTE",
            "top_k":           self.MAX_ANCHORS,
            "min_similarity":  self.SIMILARITY_THRESHOLD,
            "filters": {
                "production_location": production_location,
                "volume_range": self._volume_band(annual_volume),
            },
        }
        try:
            resp = await self._http.post(
                self.SCSE_SEARCH_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=1.5,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []          # Graceful degradation

        return [
            SimilarityAnchor(
                quote_id          = r["entity_id"],
                unit_cost_eur     = r["payload"]["unit_cost_eur"],
                similarity_score  = r["similarity_score"],
                volume            = r["payload"].get("annual_volume", annual_volume),
                production_location = r["payload"].get("production_location", production_location),
                confidence_level  = r.get("confidence_level", "MEDIUM"),
                date_quoted       = r["payload"].get("quote_date"),
            )
            for r in data.get("results", [])
            if r["payload"].get("unit_cost_eur")
        ]

    def blend_cost(
        self,
        formula_ml_cost: float,
        anchors:         list[SimilarityAnchor],
        formula_confidence: float = 0.70,
    ) -> tuple[float, float]:
        """
        Returns (blended_cost, blend_weight_used).
        blend_weight = similarity contribution fraction [0, 0.40].
        """
        if len(anchors) < self.MIN_ANCHORS_FOR_BLEND:
            return formula_ml_cost, 0.0

        # Remove outliers (IQR)
        costs     = np.array([a.unit_cost_eur for a in anchors])
        q1, q3    = np.percentile(costs, [25, 75])
        iqr       = q3 - q1
        mask      = (costs >= q1 - 1.5 * iqr) & (costs <= q3 + 1.5 * iqr)
        clean     = costs[mask]

        if len(clean) == 0:
            return formula_ml_cost, 0.0

        # Weighted median (by similarity score)
        weights   = np.array([a.similarity_score for a in anchors])[mask]
        weights  /= weights.sum()
        sorted_idx = np.argsort(clean)
        cumw       = np.cumsum(weights[sorted_idx])
        med_idx    = np.searchsorted(cumw, 0.5)
        anchor_cost = float(clean[sorted_idx[min(med_idx, len(clean)-1)]])

        # Blend weight: more anchors + higher similarity → more weight
        n_clean   = len(clean)
        avg_sim   = float(np.mean(weights * weights.sum()))  # unnormalized
        blend_w   = min(0.40, 0.10 * np.log1p(n_clean) * avg_sim)

        blended = (1 - blend_w) * formula_ml_cost + blend_w * anchor_cost
        return round(blended, 4), round(blend_w, 4)

    def _volume_band(self, volume: int) -> str:
        if volume < 50:    return "LOW"
        if volume < 5000:  return "MEDIUM"
        return "HIGH"

    def bayesian_update(
        self,
        prior_mean:  float,
        prior_std:   float,
        anchors:     list[SimilarityAnchor],
    ) -> tuple[float, float]:
        """
        Conjugate Gaussian update: posterior mean and std.
        Prior: formula cost ~ N(prior_mean, prior_std²)
        Likelihood: similar costs ~ N(μ_obs, σ_obs²)
        """
        if not anchors:
            return prior_mean, prior_std

        obs   = np.array([a.unit_cost_eur for a in anchors])
        w     = np.array([a.similarity_score for a in anchors])
        mu_l  = float(np.average(obs, weights=w))
        sigma_l = float(np.sqrt(np.average((obs - mu_l)**2, weights=w)) + 1e-6)

        # Bayesian update
        tau_prior = 1 / (prior_std**2)
        tau_like  = 1 / (sigma_l**2)
        tau_post  = tau_prior + tau_like
        mu_post   = (tau_prior * prior_mean + tau_like * mu_l) / tau_post
        sigma_post = float(np.sqrt(1 / tau_post))

        return round(mu_post, 4), round(sigma_post, 4)
```

---

## 18. Monitoring

### 18.1 Metryki Prometheus

```python
from prometheus_client import Counter, Histogram, Gauge, Summary

# --- Estimation throughput ---
CEE_ESTIMATIONS_TOTAL = Counter(
    "cee_estimations_total",
    "Total cost estimations completed",
    ["location", "confidence_level", "status"],
)
CEE_ESTIMATION_DURATION_SECONDS = Histogram(
    "cee_estimation_duration_seconds",
    "End-to-end estimation latency",
    ["mode"],                                   # sync | async | batch
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

# --- Stage latencies ---
CEE_STAGE_DURATION_SECONDS = Histogram(
    "cee_stage_duration_seconds",
    "Per-stage latency of CalculationOrchestrator",
    ["stage"],                                  # formula | ml | similarity | monte_carlo | confidence
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0],
)

# --- ML model ---
CEE_ML_PREDICTION_LATENCY = Histogram(
    "cee_ml_prediction_latency_seconds",
    "ML model inference latency",
    ["model_name"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5],
)
CEE_ML_OOD_SCORE = Histogram(
    "cee_ml_ood_score",
    "Distribution of OOD scores (0=in-dist, 1=outlier)",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)

# --- Accuracy (from actuals feedback) ---
CEE_MAPE_GAUGE = Gauge(
    "cee_mape_7d_pct",
    "Rolling 7-day MAPE percentage",
    ["location"],
)
CEE_WITHIN_CI_GAUGE = Gauge(
    "cee_within_confidence_interval_pct",
    "% estimates where actual falls within predicted CI",
    ["confidence_level"],
)

# --- Similarity injection ---
CEE_SIMILAR_ANCHORS_USED = Histogram(
    "cee_similar_anchors_used",
    "Number of SCSE anchors used per estimation",
    buckets=[0, 1, 2, 3, 5, 10, 15, 20],
)
CEE_SIMILARITY_BLEND_WEIGHT = Histogram(
    "cee_similarity_blend_weight",
    "Blend weight of similarity component",
    buckets=[0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35, 0.40],
)

# --- Material price cache ---
CEE_MATERIAL_PRICE_CACHE_HIT = Counter(
    "cee_material_price_cache_hits_total",
    "Material price cache hits",
    ["source"],                                 # redis | pg | mie_live | historical
)
CEE_MATERIAL_PRICE_AGE_HOURS = Histogram(
    "cee_material_price_age_hours",
    "Age of material price used in estimation",
    buckets=[0, 1, 4, 8, 24, 48, 72, 168],
)

# --- Job queue ---
CEE_JOB_QUEUE_DEPTH = Gauge(
    "cee_job_queue_depth",
    "Pending estimation jobs in queue",
    ["priority_band"],
)
CEE_JOB_FAILURES_TOTAL = Counter(
    "cee_job_failures_total",
    "Estimation job failures",
    ["reason"],
)

# --- Business ---
CEE_APPROVALS_TOTAL = Counter(
    "cee_approvals_total",
    "Estimates approved by cost engineers",
    ["location"],
)
CEE_UNIT_COST_EUR = Histogram(
    "cee_unit_cost_eur",
    "Distribution of unit costs estimated",
    buckets=[1, 5, 10, 25, 50, 100, 250, 500, 1000, 5000, 10000],
)
```

### 18.2 Reguły Alertmanager

```yaml
groups:
  - name: cee_sla
    rules:
      - alert: CEEHighEstimationLatency
        expr: |
          histogram_quantile(0.95, rate(cee_estimation_duration_seconds_bucket{mode="sync"}[5m])) > 3.0
        for: 5m
        labels:
          severity: warning
          team: cee
        annotations:
          summary: "CEE P95 sync latency > 3s"
          runbook: "https://wiki.ici.internal/runbooks/cee-latency"

      - alert: CEEHighMAPE
        expr: cee_mape_7d_pct > 15
        for: 30m
        labels:
          severity: warning
          team: cee
        annotations:
          summary: "CEE MAPE > 15% ({{ $labels.location }})"

      - alert: CEECriticalMAPE
        expr: cee_mape_7d_pct > 30
        for: 15m
        labels:
          severity: critical
          team: cee
        annotations:
          summary: "CEE MAPE > 30% — model retraining required"

      - alert: CEEJobQueueBacklog
        expr: cee_job_queue_depth{priority_band="high"} > 50
        for: 10m
        labels:
          severity: warning
        annotations:
          summary: "CEE job queue backlog: {{ $value }} high-priority jobs pending"

      - alert: CEEMLOODHigh
        expr: |
          histogram_quantile(0.90, rate(cee_ml_ood_score_bucket[10m])) > 0.70
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "CEE P90 OOD score > 0.70 — many estimates outside training distribution"

      - alert: CEEStaleMaterialPrices
        expr: |
          histogram_quantile(0.50, rate(cee_material_price_age_hours_bucket[30m])) > 24
        for: 15m
        labels:
          severity: warning
        annotations:
          summary: "Median material price age > 24h — MIE feed may be down"

      - alert: CEEConfidenceIntervalLow
        expr: cee_within_confidence_interval_pct{confidence_level="HIGH"} < 80
        for: 30m
        labels:
          severity: warning
        annotations:
          summary: "CEE HIGH confidence interval covers < 80% of actuals"

      - alert: CEEOutboxReplicationLag
        expr: |
          (time() - timestamp(cee_outbox_last_relay_timestamp > 0)) > 300
        for: 5m
        labels:
          severity: critical
        annotations:
          summary: "CEE outbox relay not running for >5 minutes"
```

### 18.3 Dashboardy Grafana (6)

| Dashboard | Panele | Opis |
|-----------|--------|------|
| **CEE Overview** | estimation rate, P50/P95/P99 latency, confidence distribution, error rate | Główny widok operacyjny |
| **Cost Accuracy** | MAPE by location/complexity, within_CI%, accuracy trend, bias chart | Śledzenie trafności modelu |
| **ML Models** | OOD score, inference latency, feature importance, model version | Zdrowie modeli ML |
| **Similarity Injection** | SCSE anchors/estimation, blend weight dist, similar hit rate, anchor quality | Efektywność SIL |
| **Job Queue** | queue depth, job throughput, failure rate, worker utilization | Monitorowanie przetwarzania async |
| **Business KPIs** | approvals/day, cost by location, volume distribution, benchmark coverage | Wskaźniki biznesowe |

---

## 19. Testing

### 19.1 Macierz testów

| Typ | Narzędzie | Zakres | Cel |
|-----|-----------|--------|-----|
| Unit | pytest | Formuły, extractory cech, confidence calc | Logika deterministyczna |
| Integration | pytest + Testcontainers | CEE API → PG 16 + Redis | Kontrakt bazy danych |
| ML validation | pytest + MLflow | XGBoost MAPE na holdout | Regresja modelu |
| Contract | Pact | CEE → SCSE (REST) | Kompatybilność kontraktu |
| Load | k6 | POST /estimate × 200 VU | P95 < 3s, P99 < 10s |
| Accuracy | RecallHarness | 500 historycznych wyceń | MAPE monitoring |
| Chaos | Toxiproxy | MIE/SCSE niedostępne | Graceful degradation |
| Security | OWASP ZAP | API scan | Brak luk OWASP Top 10 |

### 19.2 Testy jednostkowe — formuły

```python
import pytest
import numpy as np
from cee.domain import (
    EstimationInput, ProductGeometry, MaterialSpec, ProcessStep
)
from cee.engines import MaterialCostEngine, ProcessCostEngine, ScrapModel

@pytest.fixture
def simple_turning_input() -> EstimationInput:
    return EstimationInput(
        product_name="Test Shaft",
        geometry=ProductGeometry(
            volume_cm3=50.0,
            net_volume_cm3=30.0,
            surface_area_cm2=120.0,
            length_mm=200.0,
            width_mm=30.0,
            height_mm=30.0,
            complexity_class="SIMPLE",
            feature_count=2,
            buy_to_fly_ratio=50.0 / 30.0,
        ),
        material=MaterialSpec(
            material_code="1.0503",         # C45 steel
            material_group="CARBON_STEEL",
            density_g_cm3=7.85,
            price_eur_per_kg=1.20,
            machinability_index=0.75,
            hardness_hrc=0,
        ),
        process_steps=[
            ProcessStep(
                process_type="CNC_TURNING",
                process_class="MAC",
                machine_type="CNC_LATHE",
                cost_per_hour_eur=65.0,
                setup_time_min=30.0,
                cycle_time_sec=None,        # Will be estimated
                tolerance_it=8,
                surface_ra_um=1.6,
                operator_count=1,
                automation_level=1,
                energy_kw=12.0,
            )
        ],
        annual_volume=1000,
        batch_size=100,
        production_location="PL",
        target_currency="EUR",
    )

class TestMaterialCostEngine:
    def test_gross_weight_carbon_steel(self, simple_turning_input):
        engine = MaterialCostEngine()
        result = engine.calculate(simple_turning_input)
        # 50 cm3 × 7.85 g/cm3 / 1000 = 0.3925 kg
        assert abs(result.gross_weight_kg - 0.3925) < 0.001

    def test_btf_scrap_credit(self, simple_turning_input):
        engine = MaterialCostEngine()
        result = engine.calculate(simple_turning_input)
        # buy_to_fly = 50/30 ≈ 1.667 → chips should be credited
        assert result.scrap_credit_eur >= 0.0
        assert result.material_cost_eur > 0.0

    def test_material_cost_increases_with_volume(self, simple_turning_input):
        engine = MaterialCostEngine()
        inp_low  = simple_turning_input
        inp_high = dataclasses.replace(simple_turning_input, annual_volume=10000)
        r_low    = engine.calculate(inp_low)
        r_high   = engine.calculate(inp_high)
        # Unit material cost should decrease due to volume discount
        assert r_high.unit_cost_eur <= r_low.unit_cost_eur

class TestCycleTimeEstimation:
    def test_turning_mrr_based(self, simple_turning_input):
        from cee.engines import CycleTimeEstimator
        est = CycleTimeEstimator()
        # C45 steel: D=30mm, L=200mm, machinability=0.75
        t = est.estimate_turning(
            diameter_mm=30.0, length_mm=200.0, machinability_index=0.75
        )
        assert 30 <= t <= 300, f"Turning cycle {t}s out of expected range"

    def test_milling_mrr(self, simple_turning_input):
        from cee.engines import CycleTimeEstimator
        est = CycleTimeEstimator()
        t = est.estimate_milling(
            length_mm=100.0, width_mm=50.0, depth_mm=5.0,
            machinability_index=0.75
        )
        assert t > 0

class TestScrapModel:
    @pytest.mark.parametrize("cpk,expected_pct", [
        (1.67, 0.00057),   # IT4: 0.00057% defects
        (1.33, 0.064),     # IT6: 0.064%
        (1.00, 0.27),      # IT9: 0.27%
        (0.67, 4.55),      # IT12: 4.55%
    ])
    def test_cpk_to_scrap_rate(self, cpk, expected_pct):
        model = ScrapModel()
        rate  = model.cpk_to_scrap_rate(cpk) * 100
        assert abs(rate - expected_pct) / max(expected_pct, 0.001) < 0.10, (
            f"Cpk={cpk}: expected {expected_pct}%, got {rate:.4f}%"
        )

    def test_pieces_needed_above_one(self):
        model = ScrapModel()
        pieces = model.pieces_needed_per_good(cpk=1.00)
        assert pieces > 1.0

class TestConfidenceCalculator:
    def test_high_confidence_complete_input(self, simple_turning_input):
        from cee.confidence import CEEConfidenceCalculator
        from cee.domain import CostEstimate
        calc   = CEEConfidenceCalculator()
        dummy_estimate = CostEstimate(unit_cost_eur=10.0)
        score  = calc.calculate(
            inp=simple_turning_input,
            estimate=dummy_estimate,
            ml_ood_score=0.10,
            n_similar=8,
            similar_confidence=0.85,
            material_price_age_hours=1.0,
        )
        assert score.level.value in ("HIGH", "MEDIUM")
        assert score.overall >= 0.70

    def test_indicative_when_no_data(self):
        from cee.confidence import CEEConfidenceCalculator
        from cee.domain import CostEstimate, EstimationInput, ProductGeometry, MaterialSpec
        calc  = CEEConfidenceCalculator()
        empty = EstimationInput(
            product_name="Empty",
            geometry=ProductGeometry(volume_cm3=0, net_volume_cm3=0),
            material=MaterialSpec(material_code="UNKNOWN"),
            process_steps=[],
            annual_volume=1, batch_size=1, production_location="DE",
        )
        score = calc.calculate(empty, CostEstimate(), 0.9, 0, 0.0, 96.0)
        assert score.level.value == "INDICATIVE"
```

### 19.3 Testy integracyjne (Testcontainers)

```python
import pytest
import asyncio
import asyncpg
from testcontainers.postgres import PostgresContainer
from testcontainers.redis import RedisContainer
from httpx import AsyncClient

@pytest.fixture(scope="module")
def pg_container():
    with PostgresContainer("postgres:16") as pg:
        yield pg

@pytest.fixture(scope="module")
async def db_pool(pg_container):
    pool = await asyncpg.create_pool(pg_container.get_connection_url())
    # Apply migrations
    async with pool.acquire() as conn:
        with open("migrations/001_cee_schema.sql") as f:
            await conn.execute(f.read())
    yield pool
    await pool.close()

@pytest.fixture(scope="module")
async def api_client(db_pool):
    from cee.app import create_app
    app = create_app(db_pool=db_pool)
    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

@pytest.mark.asyncio
async def test_estimate_e2e(api_client, simple_turning_payload):
    resp = await api_client.post(
        "/v1/cee/estimate",
        json=simple_turning_payload,
        headers={"Authorization": "Bearer test-cee-user-token"},
    )
    assert resp.status_code in (200, 202)
    data = resp.json()
    if resp.status_code == 200:
        assert data["unit_cost_eur"] > 0
        assert data["uncertainty"]["confidence_level"] in ("HIGH","MEDIUM","LOW","INDICATIVE")

@pytest.mark.asyncio
async def test_estimate_stored_in_db(api_client, db_pool, simple_turning_payload):
    resp = await api_client.post(
        "/v1/cee/estimate",
        json=simple_turning_payload,
        headers={"Authorization": "Bearer test-cee-user-token"},
    )
    assert resp.status_code == 200
    estimate_id = resp.json()["estimate_id"]
    row = await db_pool.fetchrow(
        "SELECT * FROM cee.cost_estimates WHERE estimate_id = $1", estimate_id
    )
    assert row is not None
    assert float(row["unit_cost_eur"]) > 0

@pytest.mark.asyncio
async def test_material_price_cache_fallback(api_client, monkeypatch):
    """Test graceful fallback when MIE is unavailable."""
    import cee.services
    monkeypatch.setattr(cee.services, "MIE_URL", "http://nonexistent:9999")
    resp = await api_client.post(
        "/v1/cee/estimate",
        json=simple_turning_payload,
        headers={"Authorization": "Bearer test-cee-user-token"},
    )
    # Should still return result using cached/historical price
    assert resp.status_code in (200, 202)
    data = resp.json()
    assert "STALE" in str(data.get("uncertainty", {}).get("warnings", []))
```

### 19.4 Load test (k6)

```javascript
// k6/cee_load.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Counter, Rate } from "k6/metrics";

const estimationLatency = new Trend("estimation_latency");
const estimationErrors  = new Counter("estimation_errors");
const successRate       = new Rate("success_rate");

export const options = {
  scenarios: {
    steady_load: {
      executor:     "constant-arrival-rate",
      rate:         100,               // 100 RPS
      timeUnit:     "1s",
      duration:     "5m",
      preAllocatedVUs: 50,
      maxVUs:       200,
    },
    spike: {
      executor:     "ramping-arrival-rate",
      startRate:    10,
      timeUnit:     "1s",
      stages: [
        { duration: "30s", target: 200 },
        { duration: "1m",  target: 200 },
        { duration: "30s", target: 10  },
      ],
      preAllocatedVUs: 100,
      maxVUs:       400,
      startTime:    "5m",
    },
  },
  thresholds: {
    "estimation_latency{scenario:steady_load}": ["p(95)<3000", "p(99)<10000"],
    "success_rate":                              ["rate>0.99"],
  },
};

const BASE_URL = __ENV.CEE_URL || "http://cee.internal/v1/cee";
const TOKEN    = __ENV.JWT_TOKEN || "load-test-token";

const PAYLOAD = JSON.stringify({
  product_name: "Test Shaft",
  geometry: {
    volume_cm3: 50.0, net_volume_cm3: 30.0,
    complexity_class: "MODERATE",
    feature_count: 5,
  },
  material: { material_code: "1.0503" },
  process_steps: [
    { process_type: "CNC_TURNING", process_class: "MAC",
      machine_type: "CNC_LATHE", cycle_time_sec: 90 }
  ],
  annual_volume: 1000, batch_size: 100,
  production_location: "PL",
  options: { include_monte_carlo: false, include_ml: true },
});

export default function () {
  const res = http.post(`${BASE_URL}/estimate`, PAYLOAD, {
    headers: {
      "Content-Type": "application/json",
      "Authorization": `Bearer ${TOKEN}`,
    },
    timeout: "15s",
  });

  const ok = check(res, {
    "status is 200 or 202": (r) => r.status === 200 || r.status === 202,
    "has estimate_id":       (r) => r.json("estimate_id") !== undefined,
    "has unit_cost_eur":     (r) => r.json("unit_cost_eur") > 0 || r.status === 202,
  });

  estimationLatency.add(res.timings.duration);
  successRate.add(ok);
  if (!ok) estimationErrors.add(1);

  sleep(0.1);
}
```

---

## 20. Scalability

### 20.1 Poziomy skalowalności

| Poziom | Wolumen | Konfiguracja |
|--------|---------|-------------|
| L1 Dev | < 100 est/dzień | 1 instancja, PG single-node, Redis single |
| L2 Small | < 5K est/dzień | 2 instancje, PG + replika read, Redis Sentinel |
| L3 Medium | < 100K est/dzień | 4-8 instancji HPA, PG primary + 2 repliki, Redis Cluster, Kafka 3 brokerów |
| L4 Enterprise | > 100K est/dzień | Kubernetes HPA (4-20 pods), PG partitioning, Redis Cluster 6 nodes, GPU ML inference |

### 20.2 Partycjonowanie PostgreSQL

```sql
-- Partycjonowanie cost_estimates po dacie (miesięczne)
CREATE TABLE cee.cost_estimates_2024_01 PARTITION OF cee.cost_estimates
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE cee.cost_estimates_2024_02 PARTITION OF cee.cost_estimates
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');
-- ... tworzone automatycznie przez pg_partman

-- accuracy_log — partycjonowanie kwartalne
CREATE TABLE cee.accuracy_log_2024_q1 PARTITION OF cee.accuracy_log
    FOR VALUES FROM ('2024-01-01') TO ('2024-04-01');
```

### 20.3 Redis caching patterns

```python
from enum import Enum
from dataclasses import dataclass

class CachePattern(Enum):
    MATERIAL_PRICE   = "cee:mat_price:{material_code}"          # TTL 4h
    ESTIMATE_RESULT  = "cee:estimate:{estimate_id}"             # TTL 24h
    BENCHMARK_DATA   = "cee:bench:{type}:{key}:{location}"      # TTL 6h
    ML_FEATURES      = "cee:features:{input_hash}"              # TTL 1h
    MACHINE_RATES    = "cee:rates:{location}"                   # TTL 12h
    OOD_SCORE        = "cee:ood:{input_hash}"                   # TTL 30m

CACHE_TTL_SECONDS = {
    "material_price":  4   * 3600,
    "estimate_result": 24  * 3600,
    "benchmark_data":  6   * 3600,
    "ml_features":     1   * 3600,
    "machine_rates":   12  * 3600,
    "ood_score":       30  * 60,
}
```

### 20.4 Kubernetes HPA

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: cee-api-hpa
  namespace: cee
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: cee-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 65
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 75
    - type: External
      external:
        metric:
          name: cee_job_queue_depth
          selector:
            matchLabels:
              priority_band: high
        target:
          type: AverageValue
          averageValue: "20"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 2
          periodSeconds: 30
    scaleDown:
      stabilizationWindowSeconds: 300

---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cee-api
  namespace: cee
spec:
  replicas: 2
  template:
    spec:
      containers:
        - name: cee-api
          image: ici/cee-api:latest
          resources:
            requests:
              cpu: "500m"
              memory: "512Mi"
            limits:
              cpu: "2000m"
              memory: "2Gi"
          env:
            - name: CEE_WORKERS
              value: "4"
            - name: CEE_ASYNC_POOL_SIZE
              value: "20"
```

### 20.5 ML inference scaling

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

class MLInferencePool:
    """Thread pool for CPU-bound ML inference — keeps event loop unblocked."""

    def __init__(self, max_workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    async def predict_async(
        self, model, X: "pd.DataFrame"
    ) -> "np.ndarray":
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._pool, model.predict, X)
```

---

## 21. Ryzyka

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|--------------------|-------|-----------|
| R01 | Model ML trenuje na zbyt małych danych → przeuczenie | MEDIUM | HIGH | CV 5-fold + holdout 15%, minimalna liczba próbek: 500 |
| R02 | Ceny materiałów przestarzałe (MIE niedostępne) | MEDIUM | HIGH | Redis cache 4h + historyczny fallback 30d + confidence penalty |
| R03 | SCSE niedostępny → brak kotwic podobieństwa | MEDIUM | MEDIUM | Timeout 1.5s + graceful degradation (similarity_weight=0) |
| R04 | Drift rozkładu kosztów (inflacja, energia) | HIGH | HIGH | PSI monitoring cotygodniowo + automatyczny retrain powyżej 0.2 |
| R05 | Złożone części wieloprocesowe: błąd integracji sekwencji | LOW | HIGH | Sekwencyjna walidacja kroków procesu + testy integracyjne |
| R06 | Monte Carlo: 10K sampli blokuje API sync | MEDIUM | MEDIUM | Async mode obowiązkowy dla N_SAMPLES > 1000 |
| R07 | XGBoost OOM przy feature vector 100d × batch 1000 | LOW | MEDIUM | Batch chunking 100 szt. + memory limit 2Gi per pod |
| R08 | PII w nazwie produktu → wyciąg w logach | LOW | HIGH | PII scrubbing middleware + log masking |
| R09 | Stronniczość modelu ML wobec lokalizacji z małą liczbą danych | HIGH | MEDIUM | Stratified sampling po lokalizacji + location-aware eval |
| R10 | Zatrucie danych treningowych (fałszywe actuals) | LOW | HIGH | RBAC CEE_COST_ENGINEER dla POST /actuals + outlier detection |
| R11 | Tooling cost amortyzacja: błędna liczba strzałów | MEDIUM | MEDIUM | Tooling life kalibrowany kwartalnie z CHE actuals |
| R12 | Waluta: kurs wymiany nie jest aktualizowany | MEDIUM | LOW | FX rates cache 1h z ECB API + fallback static rates |
| R13 | PostgreSQL single-point-of-failure estimation_jobs | LOW | HIGH | PG HA (Patroni), jobs idempotentne z retry |
| R14 | Skokowe zmiany stawek maszynowych → stare benchmarki | MEDIUM | HIGH | Miesięczny przegląd MACHINE_RATES + alert gdy delta > 15% |

---

## 22. Roadmap

### Faza 1: Foundation (S1–S8) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S1–S2 | Schema PostgreSQL 16, migracje, model domenowy, enumeracje, domain events |
| S3–S4 | MaterialCostEngine + ProcessCostEngine + CycleTimeEstimator (10 modeli analitycznych) |
| S5–S6 | OverheadModel + SetupCostEngine + ToolingCostEngine + VolumeDiscountModel (Wright's Law) |
| S7–S8 | ScrapModel (Cpk→scrap) + MonteCarloUncertaintyEngine (10K sampli) + CalculationOrchestrator |

**Deliverables:** Deterministyczna wycena formuły dla 5 typów procesów, REST API (sync/async), outbox Kafka

### Faza 2: ML & Intelligence (S9–S16) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S9–S10 | Feature Engineering (100 cech) + CEEFeatureAssembler + historyczne dane treningowe z CHE |
| S11–S12 | XGBoost unit cost model + LightGBM component models + MLflow registry |
| S13–S14 | CEEEnsemblePredictor + OOD detection (LOF) + SHAP explainability |
| S15–S16 | SimilarityInjectionLayer + SCSE integration + Bayesian blend + confidence scoring |

**Deliverables:** MAPE < 15% na zbiorze walidacyjnym, SIL operacyjny, 5-komponentowy ConfidenceScore

### Faza 3: Production Hardening (S17–S24) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S17–S18 | Accuracy feedback loop (POST /actuals) + accuracy_log + model drift PSI monitoring |
| S19–S20 | Airflow DAG retrain (tygodniowy) + champion-challenger A/B testing + MLflow promotion |
| S21–S22 | Kubernetes HPA + Redis Cluster + PG read replicas + job queue SKIP LOCKED |
| S23–S24 | Security hardening (RBAC, PII scrubbing, OWASP ZAP) + k6 load test (100 RPS P95<3s) |

**Deliverables:** MAPE < 10% produkcja, auto-retrain, SLA enforcement

### Faza 4: Scale & Optimization (S25–S32) — 16 tygodni

| Sprint | Zadania |
|--------|---------|
| S25–S26 | GPU ML inference (ONNX Runtime) + batch estimation optimisations |
| S27–S28 | Multi-region deployment + FX hedging integration + 20-country location model |
| S29–S30 | Activity-Based Costing (ABC) pełna implementacja + ERP (SAP CO) integration |
| S31–S32 | Advanced benchmarking (P10-P90 per process/material/region) + Procurement Portal UX |

**Deliverables:** MAPE < 8% przy P95 latency < 2s, 20 lokalizacji, integracja ERP

### Docelowe SLA (po fazie 3)

| Metryka | Cel | Ostrzeżenie | Krytyczny |
|---------|-----|-------------|-----------|
| P95 latency (sync) | < 3s | 3–5s | > 5s |
| P99 latency (sync) | < 10s | 10–20s | > 20s |
| Async job p95 | < 60s | 60–120s | > 120s |
| MAPE (HIGH conf) | < 5% | 5–10% | > 15% |
| MAPE (MEDIUM conf) | < 10% | 10–20% | > 30% |
| Within CI (HIGH) | > 90% | 85–90% | < 85% |
| Material price freshness | < 4h | 4–24h | > 24h |
| ML model MAPE drift | PSI < 0.1 | PSI 0.1–0.2 | PSI > 0.2 |
| Outbox relay lag | < 30s | 30–300s | > 300s |
| Uptime | > 99.5% | 99–99.5% | < 99% |
