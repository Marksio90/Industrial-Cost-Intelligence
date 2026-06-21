# ICI Platform — Data Lake, Vector DB & ML Pipeline

## 5. Data Lake

### 5.1 Architecture (AWS S3 + Glue + Athena)

```
┌────────────────────────────────────────────────────────────────┐
│                      ICI Data Lake                             │
│                                                                │
│  ┌──────────────┐   Kafka       ┌──────────────────────────┐  │
│  │  Services    │  Connect S3   │   S3 Raw Zone            │  │
│  │  (11 svc)    │ ──────────── ►│   s3://ici-lake/raw/     │  │
│  └──────────────┘   Sink        │   Avro, partitioned by   │  │
│                                 │   year/month/day/hour     │  │
│                                 └──────────┬───────────────┘  │
│                                            │                   │
│                                     AWS Glue ETL               │
│                                     (PySpark, daily)           │
│                                            │                   │
│                                 ┌──────────▼───────────┐       │
│                                 │  S3 Curated Zone      │       │
│                                 │  s3://ici-lake/curated│       │
│                                 │  Parquet + Snappy     │       │
│                                 │  Delta Lake format    │       │
│                                 └──────────┬────────────┘       │
│                                            │                   │
│                          ┌─────────────────┼──────────────┐   │
│                          │                 │              │   │
│                          ▼                 ▼              ▼   │
│                    Athena SQL         Glue Catalog    SageMaker│
│                    (ad-hoc)           (Hive meta)    (ML train)│
│                                                               │
│  ML Feature Store (S3 + offline serving):                     │
│  s3://ici-lake/features/                                      │
│     price_series_features/  ← PFE FeatureEngineeringPipeline │
│     risk_factor_features/   ← CRAE historical                │
│     cost_feedback_features/ ← CBE + LE                       │
└────────────────────────────────────────────────────────────────┘
```

### 5.2 S3 Bucket Structure

```
s3://ici-datalake-{env}/
├── raw/
│   ├── events/
│   │   ├── topic=cbe.breakdown.approved/
│   │   │   └── year=2025/month=06/day=21/hour=14/
│   │   │       └── part-00001.avro
│   │   ├── topic=pfe.forecast.ready/
│   │   ├── topic=crae.risk.critical/
│   │   └── topic=rfq.awarded/
│   └── external/
│       ├── lme/              # LME daily settlements
│       ├── eurostat/         # PPI, HICP SDMX-JSON
│       └── supplier-docs/    # Uploaded PDFs, XLS offers
│
├── curated/
│   ├── cost_breakdowns/      # Parquet, partitioned by location/commodity
│   ├── price_history/        # PFE training data
│   ├── risk_timeseries/      # CRAE portfolio snapshots
│   ├── supplier_performance/ # SOP + SE merged
│   └── rfq_outcomes/         # RFQ awarded + pricing delta
│
├── features/
│   ├── price_features/       # FeatureEngineeringPipeline output
│   ├── risk_features/        # CRAE + CRAE historicals
│   └── cost_features/        # CBE cost component vectors
│
├── models/
│   ├── pfe/                  # SARIMA, Prophet, LSTM artifacts
│   ├── crae/                 # XGBoost risk classifier
│   └── le/                   # Active learning models
│
└── exports/
    ├── reports/              # PDF/XLSX reports
    └── bi-connector/         # Power BI / Tableau direct query
```

### 5.3 Kafka Connect → S3 Sink

```json
// data/schemas/json/kafka-connect-s3-sink.json
{
  "name": "ici-s3-sink",
  "config": {
    "connector.class": "io.confluent.connect.s3.S3SinkConnector",
    "tasks.max": "4",
    "topics.regex": "cbe\\..*|pfe\\..*|crae\\..*|rfq\\..*|sop\\..*",
    "s3.region": "eu-central-1",
    "s3.bucket.name": "ici-datalake-prod",
    "s3.part.size": "67108864",
    "topics.dir": "raw/events",
    "flush.size": "10000",
    "rotate.interval.ms": "3600000",
    "rotate.schedule.interval.ms": "3600000",
    "storage.class": "io.confluent.connect.s3.storage.S3Storage",
    "format.class": "io.confluent.connect.s3.format.avro.AvroFormat",
    "schema.compatibility": "BACKWARD",
    "partitioner.class": "io.confluent.connect.storage.partitioner.TimeBasedPartitioner",
    "path.format": "'year'=YYYY/'month'=MM/'day'=dd/'hour'=HH",
    "timestamp.extractor": "RecordField",
    "timestamp.field": "occurred_at",
    "locale": "en_US",
    "timezone": "UTC"
  }
}
```

### 5.4 Glue ETL — Raw to Curated

```python
# infra/glue/etl_raw_to_curated.py
import sys
from awsglue.transforms import *
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from awsglue.context import GlueContext
from awsglue.job import Job
from pyspark.sql import functions as F
from delta.tables import DeltaTable

args = getResolvedOptions(sys.argv, ["JOB_NAME", "SOURCE_TOPIC", "TARGET_TABLE"])
sc = SparkContext()
glueContext = GlueContext(sc)
spark = glueContext.spark_session
job = Job(glueContext)
job.init(args["JOB_NAME"], args)

S3_RAW     = f"s3://ici-datalake-prod/raw/events/topic={args['SOURCE_TOPIC']}/"
S3_CURATED = f"s3://ici-datalake-prod/curated/{args['TARGET_TABLE']}/"

# Read Avro from raw zone
df = spark.read.format("avro").load(S3_RAW)

# Deduplicate by event_id (idempotent reprocessing)
df = df.dropDuplicates(["event_id"])

# Parse timestamps
df = df.withColumn("occurred_dt", F.to_timestamp("occurred_at"))
df = df.withColumn("year",  F.year("occurred_dt"))
df = df.withColumn("month", F.month("occurred_dt"))
df = df.withColumn("day",   F.dayofmonth("occurred_dt"))

# Cast decimal columns
if "unit_cost_eur" in df.columns:
    df = df.withColumn("unit_cost_eur", F.col("unit_cost_eur").cast("decimal(18,4)"))

# Upsert into Delta table
if DeltaTable.isDeltaTable(spark, S3_CURATED):
    delta = DeltaTable.forPath(spark, S3_CURATED)
    delta.alias("target").merge(
        df.alias("source"), "target.event_id = source.event_id"
    ).whenNotMatchedInsertAll().execute()
else:
    df.write.format("delta").partitionBy("year", "month", "day").save(S3_CURATED)

job.commit()
```

### 5.5 Athena Views — Business Intelligence

```sql
-- Cost efficiency view across locations
CREATE OR REPLACE VIEW ici_analytics.v_cost_by_location AS
SELECT
    location,
    commodity_group,
    DATE_TRUNC('month', occurred_dt)     AS month,
    AVG(unit_cost_eur)                   AS avg_unit_cost,
    PERCENTILE_APPROX(unit_cost_eur, 0.5) AS median_unit_cost,
    STDDEV(unit_cost_eur)                AS stddev_unit_cost,
    COUNT(*)                             AS breakdown_count
FROM ici_datalake.cost_breakdowns
WHERE year >= YEAR(CURRENT_DATE) - 2
GROUP BY 1, 2, 3;

-- Forecast accuracy tracking
CREATE OR REPLACE VIEW ici_analytics.v_forecast_accuracy AS
SELECT
    commodity,
    model_type,
    DATE_TRUNC('month', generated_at)    AS month,
    AVG(mape_30d)                        AS avg_mape_30d,
    MIN(mape_30d)                        AS best_mape,
    MAX(mape_30d)                        AS worst_mape,
    COUNT(*)                             AS forecast_count
FROM ici_datalake.price_history
GROUP BY 1, 2, 3;
```

---

## 6. Vector DB Layer

### 6.1 Architecture

```
┌────────────────────────────────────────────────────────────┐
│                   Vector DB Layer                          │
│                                                            │
│  Sources:                                                  │
│  CAD features → embedding (ResNet-50 + custom head)       │
│  Material specs → text embedding (multilingual-e5-large)  │
│  Process descriptions → text embedding                     │
│  SOP offer text → text embedding                          │
│  CBE cost contexts → embedding                            │
│                                                            │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              Qdrant Cluster (3 nodes)               │  │
│  │                                                     │  │
│  │  Collections:                                       │  │
│  │  • material_specs        (dim=1024, HNSW m=16)     │  │
│  │  • process_descriptions  (dim=1024, HNSW m=16)     │  │
│  │  • cad_features          (dim=512,  HNSW m=32)     │  │
│  │  • supplier_offers       (dim=1024, HNSW m=16)     │  │
│  │  • cost_contexts         (dim=768,  HNSW m=16)     │  │
│  └─────────────────────────────────────────────────────┘  │
│                        │                                   │
│            ┌───────────┼──────────────┐                   │
│            │           │              │                   │
│            ▼           ▼              ▼                   │
│      ME (similarity  CAD (similar   CBE (cost lookup     │
│       material)       geometry)      from history)        │
└────────────────────────────────────────────────────────────┘
```

### 6.2 Qdrant Collections Schema

```python
# libs/shared-vector/src/shared_vector/collections.py
from qdrant_client import QdrantClient
from qdrant_client.models import (
    VectorParams, Distance, HnswConfigDiff, OptimizersConfigDiff,
    PayloadSchemaType, CreateCollection, ScalarQuantization, ScalarType
)


COLLECTIONS: dict[str, dict] = {
    "material_specs": {
        "size": 1024,
        "distance": Distance.COSINE,
        "hnsw_config": HnswConfigDiff(m=16, ef_construct=200),
        "payload_schema": {
            "material_id":     PayloadSchemaType.KEYWORD,
            "tenant_id":       PayloadSchemaType.KEYWORD,
            "material_class":  PayloadSchemaType.KEYWORD,
            "alloy":           PayloadSchemaType.KEYWORD,
            "created_at":      PayloadSchemaType.DATETIME,
        },
        "quantization": ScalarQuantization(
            scalar={"type": ScalarType.INT8, "quantile": 0.99, "always_ram": True}
        ),
    },
    "cad_features": {
        "size": 512,
        "distance": Distance.COSINE,
        "hnsw_config": HnswConfigDiff(m=32, ef_construct=400),
        "payload_schema": {
            "cad_file_id":     PayloadSchemaType.KEYWORD,
            "tenant_id":       PayloadSchemaType.KEYWORD,
            "process_type":    PayloadSchemaType.KEYWORD,
            "complexity_score": PayloadSchemaType.FLOAT,
        },
    },
    "supplier_offers": {
        "size": 1024,
        "distance": Distance.COSINE,
        "hnsw_config": HnswConfigDiff(m=16, ef_construct=200),
        "payload_schema": {
            "offer_id":        PayloadSchemaType.KEYWORD,
            "supplier_id":     PayloadSchemaType.KEYWORD,
            "tenant_id":       PayloadSchemaType.KEYWORD,
            "commodity":       PayloadSchemaType.KEYWORD,
            "unit_price_eur":  PayloadSchemaType.FLOAT,
            "parsed_at":       PayloadSchemaType.DATETIME,
        },
    },
    "cost_contexts": {
        "size": 768,
        "distance": Distance.COSINE,
        "hnsw_config": HnswConfigDiff(m=16, ef_construct=200),
        "payload_schema": {
            "breakdown_id":    PayloadSchemaType.KEYWORD,
            "tenant_id":       PayloadSchemaType.KEYWORD,
            "location":        PayloadSchemaType.KEYWORD,
            "unit_cost_eur":   PayloadSchemaType.FLOAT,
            "confidence":      PayloadSchemaType.KEYWORD,
        },
    },
}


async def ensure_collections(client: QdrantClient) -> None:
    existing = {c.name for c in client.get_collections().collections}
    for name, cfg in COLLECTIONS.items():
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=cfg["size"], distance=cfg["distance"]),
                hnsw_config=cfg.get("hnsw_config"),
                optimizers_config=OptimizersConfigDiff(indexing_threshold=20_000),
            )
            for field, schema in cfg.get("payload_schema", {}).items():
                client.create_payload_index(name, field, schema)
```

### 6.3 Embedding Service

```python
# services/learning-engine/src/learning_engine/embeddings.py
from __future__ import annotations
import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from functools import lru_cache

MODEL_TEXT = "intfloat/multilingual-e5-large"   # 560M params, 1024-dim
MODEL_CAD  = "resnet50"                          # fine-tuned on manufacturing CAD


class TextEmbedder:
    """Embed material specs, process descriptions, offer text."""

    def __init__(self, model_name: str = MODEL_TEXT, device: str = "cpu") -> None:
        self._model = SentenceTransformer(model_name, device=device)
        self._device = device

    def embed(self, texts: list[str]) -> np.ndarray:
        # E5 requires "query: " or "passage: " prefix
        passages = [f"passage: {t}" for t in texts]
        return self._model.encode(
            passages,
            normalize_embeddings=True,
            batch_size=32,
            show_progress_bar=False,
        )

    def embed_query(self, query: str) -> np.ndarray:
        return self._model.encode(
            f"query: {query}",
            normalize_embeddings=True,
        )


class CADEmbedder:
    """Extract geometric feature vectors from CAD thumbnails / STEP files."""

    def __init__(self, model_path: str) -> None:
        self._model = torch.load(model_path, weights_only=True)
        self._model.eval()

    @torch.no_grad()
    def embed(self, tensor_batch: torch.Tensor) -> np.ndarray:
        features = self._model(tensor_batch)
        return features.cpu().numpy()
```

### 6.4 Similarity Search Use Cases

```python
# libs/shared-vector/src/shared_vector/search.py
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, SearchRequest
import numpy as np


class VectorSearchService:
    def __init__(self, client: QdrantClient, text_embedder, cad_embedder) -> None:
        self._client = client
        self._text = text_embedder
        self._cad = cad_embedder

    async def find_similar_materials(
        self, query: str, tenant_id: str, top_k: int = 10
    ) -> list[dict]:
        """Used by Material Engine — find materials with similar specs."""
        vector = self._text.embed_query(query)
        results = self._client.search(
            collection_name="material_specs",
            query_vector=vector.tolist(),
            query_filter=Filter(must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id))
            ]),
            limit=top_k,
            with_payload=True,
            score_threshold=0.75,
        )
        return [{"id": r.id, "score": r.score, **r.payload} for r in results]

    async def find_similar_offers(
        self, offer_text: str, commodity: str, tenant_id: str, top_k: int = 20
    ) -> list[dict]:
        """Used by SOP — find historical offers for price benchmarking."""
        vector = self._text.embed_query(offer_text)
        results = self._client.search(
            collection_name="supplier_offers",
            query_vector=vector.tolist(),
            query_filter=Filter(must=[
                FieldCondition(key="tenant_id",  match=MatchValue(value=tenant_id)),
                FieldCondition(key="commodity",  match=MatchValue(value=commodity)),
            ]),
            limit=top_k,
            with_payload=True,
        )
        return [{"id": r.id, "score": r.score, **r.payload} for r in results]

    async def find_similar_cost_context(
        self, breakdown_context: str, location: str, tenant_id: str, top_k: int = 5
    ) -> list[dict]:
        """Used by CBE — retrieve nearest historical cost breakdowns."""
        vector = self._text.embed_query(breakdown_context)
        results = self._client.search(
            collection_name="cost_contexts",
            query_vector=vector.tolist(),
            query_filter=Filter(must=[
                FieldCondition(key="tenant_id", match=MatchValue(value=tenant_id)),
                FieldCondition(key="location",  match=MatchValue(value=location)),
                FieldCondition(key="confidence", match=MatchValue(value="HIGH")),
            ]),
            limit=top_k,
            with_payload=True,
            score_threshold=0.80,
        )
        return [{"id": r.id, "score": r.score, **r.payload} for r in results]
```

---

## 7. ML Pipeline

### 7.1 ML Platform Stack

```
┌─────────────────────────────────────────────────────────────┐
│                    ICI ML Platform                          │
│                                                             │
│  ┌────────────────┐    ┌────────────────┐                  │
│  │  Feature Store │    │  MLflow Server │                  │
│  │  (S3 offline + │    │  (experiments, │                  │
│  │   Redis online)│    │   model reg.)  │                  │
│  └───────┬────────┘    └──────┬─────────┘                  │
│          │                   │                             │
│          └─────────┬─────────┘                             │
│                    │                                       │
│          ┌─────────▼────────────────────────┐             │
│          │       Training Pipeline           │             │
│          │  Argo Workflows / AWS SageMaker  │             │
│          │                                  │             │
│          │  PFE: SARIMA → Prophet → LSTM    │             │
│          │  CRAE: XGBoost risk classifier   │             │
│          │  LE:   Active learning loop      │             │
│          │  CAD:  ResNet-50 fine-tune       │             │
│          └─────────┬────────────────────────┘             │
│                    │                                       │
│          ┌─────────▼────────────────────────┐             │
│          │      Model Registry (MLflow)      │             │
│          │  Stage: Staging → Production      │             │
│          │  Artifact: S3 + metadata          │             │
│          │  Lineage: data version + params  │             │
│          └─────────┬────────────────────────┘             │
│                    │                                       │
│          ┌─────────▼────────────────────────┐             │
│          │      Serving                     │             │
│          │  PFE worker: LSTM inference GPU  │             │
│          │  CRAE: XGBoost CPU batch         │             │
│          │  LE: online model HTTP           │             │
│          └──────────────────────────────────┘             │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 Feature Store

```python
# libs/shared-ml/src/shared_ml/feature_store.py
from __future__ import annotations
import pandas as pd
import numpy as np
import redis.asyncio as aioredis
import boto3
from datetime import datetime


class FeatureStore:
    """Offline (S3/Parquet) + Online (Redis) dual-mode feature store."""

    ONLINE_TTL = 3600   # seconds

    def __init__(self, s3_bucket: str, s3_prefix: str, redis_client: aioredis.Redis) -> None:
        self._s3 = boto3.client("s3")
        self._bucket = s3_bucket
        self._prefix = s3_prefix
        self._redis = redis_client

    # --- Offline ---

    def write_offline(
        self, feature_group: str, df: pd.DataFrame, partition_date: datetime
    ) -> None:
        key = (f"{self._prefix}/{feature_group}/"
               f"year={partition_date.year}/month={partition_date.month:02d}/"
               f"day={partition_date.day:02d}/features.parquet")
        self._s3.put_object(
            Bucket=self._bucket,
            Key=key,
            Body=df.to_parquet(index=False),
        )

    def read_offline(
        self, feature_group: str, start: datetime, end: datetime
    ) -> pd.DataFrame:
        # List partitions in range
        paginator = self._s3.get_paginator("list_objects_v2")
        dfs: list[pd.DataFrame] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{self._prefix}/{feature_group}/"):
            for obj in page.get("Contents", []):
                dfs.append(pd.read_parquet(f"s3://{self._bucket}/{obj['Key']}"))
        return pd.concat(dfs).sort_values("date") if dfs else pd.DataFrame()

    # --- Online ---

    async def write_online(
        self, feature_group: str, entity_id: str, features: dict[str, float]
    ) -> None:
        key = f"fs:{feature_group}:{entity_id}"
        await self._redis.hset(key, mapping={k: str(v) for k, v in features.items()})
        await self._redis.expire(key, self.ONLINE_TTL)

    async def read_online(
        self, feature_group: str, entity_id: str
    ) -> dict[str, float] | None:
        key = f"fs:{feature_group}:{entity_id}"
        raw = await self._redis.hgetall(key)
        if not raw:
            return None
        return {k.decode(): float(v) for k, v in raw.items()}
```

### 7.3 MLflow Integration

```python
# services/forecast-engine/src/forecast_engine/mlflow_registry.py
import mlflow
import mlflow.pytorch
import mlflow.sklearn
from pathlib import Path


class ModelRegistry:
    TRACKING_URI = "http://mlflow:5000"
    EXPERIMENT_NAME = "ici-pfe"

    def __init__(self) -> None:
        mlflow.set_tracking_uri(self.TRACKING_URI)
        mlflow.set_experiment(self.EXPERIMENT_NAME)

    def log_sarima_run(
        self, commodity: str, params: dict, metrics: dict, model_path: Path
    ) -> str:
        with mlflow.start_run(run_name=f"sarima-{commodity}") as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)   # mape_30d, rmse, coverage_95
            mlflow.log_artifact(str(model_path))
            mlflow.set_tags({
                "commodity": commodity,
                "model_type": "SARIMA",
                "framework": "statsmodels",
            })
            return run.info.run_id

    def log_lstm_run(
        self, commodity: str, params: dict, metrics: dict, model
    ) -> str:
        with mlflow.start_run(run_name=f"lstm-{commodity}") as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)
            mlflow.pytorch.log_model(
                model,
                artifact_path="model",
                registered_model_name=f"ici-pfe-lstm-{commodity}",
            )
            return run.info.run_id

    def promote_to_production(self, model_name: str, version: int) -> None:
        client = mlflow.tracking.MlflowClient(self.TRACKING_URI)
        client.transition_model_version_stage(
            name=model_name, version=version, stage="Production"
        )

    def get_production_model(self, model_name: str):
        return mlflow.pytorch.load_model(f"models:/{model_name}/Production")
```

### 7.4 Argo Workflow — PFE Training Pipeline

```yaml
# infra/k8s/base/argo/pfe-training-workflow.yaml
apiVersion: argoproj.io/v1alpha1
kind: WorkflowTemplate
metadata:
  name: pfe-training-pipeline
  namespace: ici-ml
spec:
  entrypoint: training-dag
  arguments:
    parameters:
      - name: commodity
        value: "STEEL_HRC"
      - name: lookback_days
        value: "730"

  templates:
    - name: training-dag
      dag:
        tasks:
          - name: fetch-features
            template: feature-extraction
          - name: train-sarima
            template: train-model
            arguments:
              parameters:
                - name: model_type
                  value: SARIMA
            dependencies: [fetch-features]
          - name: train-prophet
            template: train-model
            arguments:
              parameters:
                - name: model_type
                  value: PROPHET
            dependencies: [fetch-features]
          - name: train-lstm
            template: train-model-gpu
            arguments:
              parameters:
                - name: model_type
                  value: LSTM
            dependencies: [fetch-features]
          - name: build-ensemble
            template: ensemble-step
            dependencies: [train-sarima, train-prophet, train-lstm]
          - name: evaluate
            template: evaluation-step
            dependencies: [build-ensemble]
          - name: promote
            template: promotion-step
            dependencies: [evaluate]

    - name: feature-extraction
      container:
        image: ici-platform/pfe-worker:latest
        command: [python, -m, forecast_engine.cli, extract-features]
        args: ["--commodity", "{{workflow.parameters.commodity}}",
               "--lookback", "{{workflow.parameters.lookback_days}}"]
        resources:
          requests: {memory: "4Gi", cpu: "2"}
          limits:   {memory: "8Gi", cpu: "4"}

    - name: train-model
      inputs:
        parameters:
          - name: model_type
      container:
        image: ici-platform/pfe-worker:latest
        command: [python, -m, forecast_engine.cli, train]
        args: ["--commodity", "{{workflow.parameters.commodity}}",
               "--model-type", "{{inputs.parameters.model_type}}"]
        resources:
          requests: {memory: "4Gi", cpu: "4"}
          limits:   {memory: "8Gi", cpu: "8"}

    - name: train-model-gpu
      inputs:
        parameters:
          - name: model_type
      container:
        image: ici-platform/pfe-worker-gpu:latest
        command: [python, -m, forecast_engine.cli, train]
        args: ["--commodity", "{{workflow.parameters.commodity}}",
               "--model-type", "LSTM", "--device", "cuda"]
        resources:
          requests:
            memory: "8Gi"
            cpu: "4"
            nvidia.com/gpu: "1"
          limits:
            memory: "16Gi"
            cpu: "8"
            nvidia.com/gpu: "1"
        nodeSelector:
          cloud.google.com/gke-accelerator: nvidia-tesla-t4

    - name: evaluation-step
      container:
        image: ici-platform/pfe-worker:latest
        command: [python, -m, forecast_engine.cli, evaluate]
        args: ["--commodity", "{{workflow.parameters.commodity}}",
               "--mape-gate", "0.06"]

    - name: promotion-step
      container:
        image: ici-platform/pfe-worker:latest
        command: [python, -m, forecast_engine.cli, promote-if-better]
        args: ["--commodity", "{{workflow.parameters.commodity}}"]
```

### 7.5 Learning Engine — Active Learning Loop

```python
# services/learning-engine/src/learning_engine/active_learning.py
"""
Active Learning: CBE cost breakdown feedback → retrain cost estimator.

Loop:
  1. Collect labeled samples (approved breakdowns with actual vs predicted)
  2. Score unlabeled samples by uncertainty (prediction variance)
  3. Select top-K most uncertain for human annotation
  4. Retrain XGBoost cost model
  5. Publish le.model.retrained event
"""
from __future__ import annotations
import numpy as np
import xgboost as xgb
from dataclasses import dataclass
from uuid import UUID


@dataclass
class LabeledSample:
    breakdown_id: UUID
    features: np.ndarray          # feature vector from CBE
    actual_unit_cost_eur: float
    predicted_unit_cost_eur: float
    error_pct: float


class ActiveLearner:
    UNCERTAINTY_THRESHOLD = 0.15   # select samples where error_pct > 15%
    MIN_RETRAIN_SAMPLES = 100
    N_ESTIMATORS = 500
    EARLY_STOPPING = 20

    def __init__(self, model_registry) -> None:
        self._registry = model_registry
        self._model: xgb.XGBRegressor | None = None
        self._buffer: list[LabeledSample] = []

    def ingest_feedback(self, sample: LabeledSample) -> None:
        self._buffer.append(sample)

    def should_retrain(self) -> bool:
        high_error = [s for s in self._buffer if abs(s.error_pct) > self.UNCERTAINTY_THRESHOLD]
        return len(high_error) >= self.MIN_RETRAIN_SAMPLES

    def retrain(self) -> dict[str, float]:
        X = np.vstack([s.features for s in self._buffer])
        y = np.array([s.actual_unit_cost_eur for s in self._buffer])

        split = int(len(X) * 0.8)
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        self._model = xgb.XGBRegressor(
            n_estimators=self.N_ESTIMATORS,
            learning_rate=0.05,
            max_depth=6,
            subsample=0.8,
            colsample_bytree=0.8,
            objective="reg:squarederror",
            early_stopping_rounds=self.EARLY_STOPPING,
            eval_metric="mape",
        )
        self._model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )

        preds = self._model.predict(X_val)
        mape = float(np.mean(np.abs((y_val - preds) / np.maximum(y_val, 1))))
        rmse = float(np.sqrt(np.mean((y_val - preds) ** 2)))

        run_id = self._registry.log_xgb_run(
            model_name="ici-le-cost-estimator",
            model=self._model,
            metrics={"mape": mape, "rmse": rmse},
            n_samples=len(X),
        )
        self._buffer.clear()
        return {"mape": mape, "rmse": rmse, "run_id": run_id}

    def predict(self, features: np.ndarray) -> float:
        if self._model is None:
            raise RuntimeError("Model not trained")
        return float(self._model.predict(features.reshape(1, -1))[0])
```
