"""
MLflow Model Registry integration.

Manages the full model lifecycle:
  NONE → Staging → Production → Archived

Promotion logic:
  - A new model is registered as version N in Staging
  - If its MAPE is better than the current Production model by > 1%,
    it is promoted to Production and the old version is Archived
  - The feature pipeline (sklearn) is saved as a separate artifact
    alongside each model version for reproducibility
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import structlog

from ..config import MLSettings
from ..evaluation.metrics import RegressionMetrics

log = structlog.get_logger(__name__)


@dataclass
class RegistryEntry:
    name: str
    version: str
    stage: str
    run_id: str
    mape: float
    rmse: float
    model_uri: str


class ModelRegistry:
    def __init__(self, settings: MLSettings) -> None:
        self._settings = settings
        self._client = None

    @property
    def client(self):
        if self._client is None:
            import mlflow
            mlflow.set_tracking_uri(self._settings.mlflow_tracking_uri)
            self._client = mlflow.MlflowClient()
        return self._client

    def register(
        self,
        run_id: str,
        artifact_path: str,
        metrics: RegressionMetrics,
        tags: dict[str, str] | None = None,
    ) -> RegistryEntry:
        import mlflow

        model_uri = f"runs:/{run_id}/{artifact_path}"
        name = self._settings.mlflow_registry_name

        # Ensure registry exists
        try:
            self.client.create_registered_model(
                name,
                description="ICI Cost Prediction — LightGBM/XGBoost ensemble",
            )
        except Exception:
            pass  # already exists

        mv = mlflow.register_model(model_uri, name)
        version = mv.version

        # Tag with metrics
        self.client.set_model_version_tag(name, version, "mape", f"{metrics.mape:.4f}")
        self.client.set_model_version_tag(name, version, "rmse", f"{metrics.rmse:.4f}")
        self.client.set_model_version_tag(name, version, "r2",   f"{metrics.r2:.4f}")
        for k, v in (tags or {}).items():
            self.client.set_model_version_tag(name, version, k, v)

        log.info("model_registered", name=name, version=version, mape=f"{metrics.mape:.2%}")

        # Auto-promote if better than Production
        self._maybe_promote(name, version, metrics)

        entry = RegistryEntry(
            name=name, version=version, stage="Staging",
            run_id=run_id, mape=metrics.mape, rmse=metrics.rmse,
            model_uri=f"models:/{name}/{version}",
        )
        return entry

    def _maybe_promote(self, name: str, version: str, new_metrics: RegressionMetrics) -> None:
        prod = self._get_production_model(name)
        if prod is None:
            self._promote_to_production(name, version)
            return

        prod_mape = float(prod.tags.get("mape", "1.0"))
        improvement = (prod_mape - new_metrics.mape) / max(prod_mape, 1e-9)

        if improvement > 0.01:  # >1% MAPE improvement
            log.info(
                "promoting_to_production",
                version=version,
                improvement=f"{improvement:.1%}",
                old_mape=f"{prod_mape:.2%}",
                new_mape=f"{new_metrics.mape:.2%}",
            )
            self._promote_to_production(name, version)
            # Archive the old version
            self.client.transition_model_version_stage(
                name, prod.version, "Archived", archive_existing_versions=False
            )
        else:
            log.info(
                "model_not_promoted",
                version=version,
                improvement=f"{improvement:.1%}",
                threshold="1.0%",
            )

    def _promote_to_production(self, name: str, version: str) -> None:
        self.client.transition_model_version_stage(
            name, version, "Production", archive_existing_versions=True
        )
        log.info("model_in_production", name=name, version=version)

    def _get_production_model(self, name: str):
        try:
            versions = self.client.get_latest_versions(name, stages=["Production"])
            return versions[0] if versions else None
        except Exception:
            return None

    def load_production_model(self) -> Any:
        import mlflow.pyfunc
        name = self._settings.mlflow_registry_name
        uri = f"models:/{name}/Production"
        log.info("loading_production_model", uri=uri)
        return mlflow.pyfunc.load_model(uri)

    def load_model_version(self, version: str) -> Any:
        import mlflow.pyfunc
        name = self._settings.mlflow_registry_name
        return mlflow.pyfunc.load_model(f"models:/{name}/{version}")

    def list_versions(self) -> list[RegistryEntry]:
        name = self._settings.mlflow_registry_name
        try:
            versions = self.client.search_model_versions(f"name='{name}'")
        except Exception:
            return []
        return [
            RegistryEntry(
                name=mv.name,
                version=mv.version,
                stage=mv.current_stage,
                run_id=mv.run_id,
                mape=float(mv.tags.get("mape", "1.0")),
                rmse=float(mv.tags.get("rmse", "0.0")),
                model_uri=f"models:/{mv.name}/{mv.version}",
            )
            for mv in versions
        ]

    def compare_versions(self, v1: str, v2: str) -> dict[str, Any]:
        """Return side-by-side metric comparison of two registered versions."""
        name = self._settings.mlflow_registry_name
        mv1 = self.client.get_model_version(name, v1)
        mv2 = self.client.get_model_version(name, v2)
        return {
            "version_a": {"version": v1, "stage": mv1.current_stage, **mv1.tags},
            "version_b": {"version": v2, "stage": mv2.current_stage, **mv2.tags},
        }

    def archive_version(self, version: str) -> None:
        name = self._settings.mlflow_registry_name
        self.client.transition_model_version_stage(name, version, "Archived")
        log.info("model_archived", name=name, version=version)
