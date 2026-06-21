"""Tests for inference API and predictor — mocked model."""
from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from ..inference.predictor import CostPredictor, _hash_features
from ..inference.schemas import CostPredictionRequest


class TestCostPredictor:
    @pytest.fixture
    def mock_model(self):
        m = MagicMock()
        m.predict.return_value = np.array([42.50])
        return m

    def test_predict_returns_value(self, mock_model):
        predictor = CostPredictor()
        predictor._model = mock_model
        predictor._model_version = "test-v1"
        result = predictor.predict({"price_per_kg_eur": 2.0}, include_ci=False)
        assert result.value == pytest.approx(42.50)
        assert result.model_version == "test-v1"
        assert not result.cached

    def test_predict_clips_negative(self):
        model = MagicMock()
        model.predict.return_value = np.array([-5.0])
        predictor = CostPredictor()
        predictor._model = model
        predictor._model_version = "test"
        result = predictor.predict({}, include_ci=False)
        assert result.value == 0.0

    def test_cache_hit(self, mock_model):
        predictor = CostPredictor()
        predictor._model = mock_model
        predictor._model_version = "v1"
        features = {"price_per_kg_eur": 2.0, "volume_cm3": 100.0}
        # First call
        r1 = predictor.predict(features, include_ci=False)
        assert not r1.cached
        # Second call — should be cached
        r2 = predictor.predict(features, include_ci=False)
        assert r2.cached
        assert r2.value == r1.value
        # Model called only once
        assert mock_model.predict.call_count == 1

    def test_batch_predict(self, mock_model):
        mock_model.predict.return_value = np.array([10.0, 20.0, 30.0])
        predictor = CostPredictor()
        predictor._model = mock_model
        predictor._model_version = "v1"
        features_list = [{"x": i} for i in range(3)]
        results = predictor.predict_batch(features_list)
        assert len(results) == 3
        assert results[0].value == pytest.approx(10.0)

    def test_fallback_model_used_on_load_failure(self):
        predictor = CostPredictor()
        with patch("mlflow.pyfunc.load_model", side_effect=RuntimeError("no server")):
            predictor.load()
        result = predictor.predict({}, include_ci=False)
        assert result.value == pytest.approx(42.0)  # fallback price
        assert result.model_version == "fallback"


class TestFeatureHashing:
    def test_deterministic(self):
        f = {"a": 1, "b": 2.0, "c": "x"}
        assert _hash_features(f) == _hash_features(f)

    def test_different_content_different_hash(self):
        assert _hash_features({"a": 1}) != _hash_features({"a": 2})

    def test_key_order_invariant(self):
        f1 = {"a": 1, "b": 2}
        f2 = {"b": 2, "a": 1}
        assert _hash_features(f1) == _hash_features(f2)


class TestPredictionSchemas:
    def test_to_flat_dict(self):
        req = CostPredictionRequest(
            material={"price_per_kg_eur": 2.5},
            process={"cycle_time_min": 15.0},
            geometry={"volume_cm3": 100.0},
            quantity=50,
        )
        flat = req.to_flat_dict()
        assert flat["price_per_kg_eur"] == pytest.approx(2.5)
        assert flat["cycle_time_min"] == pytest.approx(15.0)
        assert flat["volume_cm3"] == pytest.approx(100.0)
        assert flat["quantity"] == 50

    def test_defaults(self):
        req = CostPredictionRequest()
        flat = req.to_flat_dict()
        assert flat["quantity"] == 1.0


class TestInferenceAPI:
    @pytest.fixture
    def app_client(self):
        mock_predictor = MagicMock()
        mock_predictor._model_version = "test-v1"
        mock_predictor._feature_names = ["f1", "f2"]
        mock_predictor._model = MagicMock()

        from ..inference.predictor import PredictionResult
        mock_predictor.predict.return_value = PredictionResult(
            value=125.50, lower=100.0, upper=150.0,
            model_version="test-v1", feature_contributions=None,
            latency_ms=5.0, cached=False,
        )

        with patch("cost_prediction.inference.api.CostPredictor", return_value=mock_predictor):
            from ..inference.api import create_inference_app
            from ..config import MLSettings
            import os
            os.environ["ANTHROPIC_API_KEY"] = "sk-test"
            settings = MLSettings()  # type: ignore
            app = create_inference_app(settings, predictor=mock_predictor)
            with TestClient(app) as client:
                yield client

    def test_health_endpoint(self, app_client):
        r = app_client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"

    def test_predict_endpoint(self, app_client):
        payload = {
            "material": {"price_per_kg_eur": 2.5},
            "process": {"cycle_time_min": 10.0},
            "geometry": {"volume_cm3": 50.0},
            "quantity": 100,
        }
        r = app_client.post("/predict", json=payload)
        assert r.status_code == 200
        data = r.json()
        assert "predicted_cost_eur" in data
        assert data["predicted_cost_eur"] == pytest.approx(125.50)
        assert "model_version" in data

    def test_predict_invalid_quantity(self, app_client):
        r = app_client.post("/predict", json={"quantity": -1})
        assert r.status_code == 422
