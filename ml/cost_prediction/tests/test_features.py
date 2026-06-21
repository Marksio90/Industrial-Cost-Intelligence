"""Tests for all feature engineering transformers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ..features.geometry_features import GeometryFeatureTransformer
from ..features.material_features import MaterialFeatureTransformer
from ..features.process_features import ProcessFeatureTransformer
from ..features.supplier_features import SupplierFeatureTransformer


# ── Material Features ─────────────────────────────────────────────────────────

class TestMaterialFeatureTransformer:
    @pytest.fixture
    def sample(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "material_class": "METAL",
            "density_kg_m3": 7850.0,
            "hardness_hv": 200.0,
            "ultimate_tensile_strength_mpa": 500.0,
            "yield_strength_mpa": 350.0,
            "elongation_pct": 20.0,
            "thermal_conductivity_w_mk": 50.0,
            "price_per_kg_eur": 1.50,
            "machinability_index": 0.7,
        }])

    def test_fit_transform_no_nans(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        assert isinstance(result, pd.DataFrame)
        assert not result.isnull().any().any()

    def test_log_features_created(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        for col in ["log_density_kg_m3", "log_price_per_kg_eur"]:
            assert col in result.columns

    def test_log_density_value(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        expected = np.log1p(7850.0)
        assert result["log_density_kg_m3"].iloc[0] == pytest.approx(expected)

    def test_material_cost_per_m3_derived(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        assert "material_cost_per_m3" in result.columns
        expected = 1.50 * 7850.0
        assert result["material_cost_per_m3"].iloc[0] == pytest.approx(expected)

    def test_is_ductile_flag(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["is_ductile"].iloc[0] == 1

    def test_strength_to_weight_ratio(self, sample):
        t = MaterialFeatureTransformer()
        result = t.fit_transform(sample)
        assert "strength_to_weight" in result.columns
        assert result["strength_to_weight"].iloc[0] == pytest.approx(500 / 7850, rel=1e-4)

    def test_missing_values_filled(self):
        df = pd.DataFrame([{"density_kg_m3": np.nan, "price_per_kg_eur": 2.0}])
        t = MaterialFeatureTransformer()
        t.fit(pd.DataFrame([{"density_kg_m3": 7850.0, "price_per_kg_eur": 1.5}]))
        result = t.transform(df)
        assert not np.isnan(result["density_kg_m3"].iloc[0])


# ── Process Features ──────────────────────────────────────────────────────────

class TestProcessFeatureTransformer:
    @pytest.fixture
    def sample(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "process_type": "milling",
            "setup_time_h": 2.0,
            "cycle_time_min": 15.0,
            "machine_hourly_rate_eur": 80.0,
            "operator_count": 1,
            "scrap_rate_pct": 3.0,
            "tooling_cost_eur": 500.0,
            "num_operations": 4,
            "surface_finish_ra": 1.6,
            "tolerance_grade": "IT7",
            "requires_heat_treatment": False,
            "requires_inspection": True,
            "batch_size": 100,
        }])

    def test_basic_transform(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert isinstance(result, pd.DataFrame)
        assert not result.isnull().any().any()

    def test_process_cost_rate(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert "process_cost_rate" in result.columns
        expected = 80.0 * 15.0 / 60.0
        assert result["process_cost_rate"].iloc[0] == pytest.approx(expected)

    def test_scrap_logit(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert "scrap_logit" in result.columns
        p = 0.03
        expected = np.log(p / (1 - p))
        assert result["scrap_logit"].iloc[0] == pytest.approx(expected, rel=1e-3)

    def test_process_complexity(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["process_complexity"].iloc[0] == 2  # milling = 2

    def test_tolerance_ordinal(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["tolerance_ordinal"].iloc[0] == 3  # IT7

    def test_operations_squared(self, sample):
        t = ProcessFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["num_operations_sq"].iloc[0] == 16


# ── Geometry Features ─────────────────────────────────────────────────────────

class TestGeometryFeatureTransformer:
    @pytest.fixture
    def sample(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "length_mm": 200.0,
            "width_mm": 100.0,
            "height_mm": 50.0,
            "surface_area_cm2": 180.0,
            "wall_thickness_mm": 3.0,
            "num_holes": 4,
            "num_threads": 2,
            "num_pockets": 1,
        }])

    def test_volume_derived(self, sample):
        t = GeometryFeatureTransformer()
        result = t.fit_transform(sample)
        assert "volume_cm3" in result.columns
        expected = 200 * 100 * 50 / 1000
        assert result["volume_cm3"].iloc[0] == pytest.approx(expected)

    def test_aspect_ratio(self, sample):
        t = GeometryFeatureTransformer()
        result = t.fit_transform(sample)
        assert "aspect_ratio" in result.columns
        assert result["aspect_ratio"].iloc[0] == pytest.approx(200.0 / 50.0)

    def test_thin_wall_false(self, sample):
        t = GeometryFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["is_thin_wall"].iloc[0] == 0

    def test_thin_wall_true(self):
        df = pd.DataFrame([{"length_mm": 100, "width_mm": 100, "height_mm": 100, "wall_thickness_mm": 1.0}])
        t = GeometryFeatureTransformer()
        result = t.fit_transform(df)
        assert result["is_thin_wall"].iloc[0] == 1

    def test_geometric_complexity_score(self, sample):
        t = GeometryFeatureTransformer()
        result = t.fit_transform(sample)
        # 4 holes × 1 + 2 threads × 1.5 + 1 pocket × 2 = 9
        assert result["geometric_complexity_score"].iloc[0] == pytest.approx(9.0)

    def test_log_volume_nonnegative(self, sample):
        t = GeometryFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["log_volume_cm3"].iloc[0] > 0


# ── Supplier Features ─────────────────────────────────────────────────────────

class TestSupplierFeatureTransformer:
    @pytest.fixture
    def sample(self) -> pd.DataFrame:
        return pd.DataFrame([{
            "overall_score": 0.82,
            "quality_score": 0.90,
            "delivery_score": 0.85,
            "price_score": 0.75,
            "financial_score": 0.70,
            "years_active": 15,
            "capacity_utilisation": 0.75,
            "avg_lead_time_days": 21.0,
            "quote_win_rate": 0.45,
            "avg_price_deviation_pct": -5.0,
            "country_code": "DE",
            "supplier_tier": "PREFERRED",
            "certifications": "ISO 9001, IATF 16949",
        }])

    def test_kpi_composite(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert "kpi_composite" in result.columns
        expected = 0.9 * 0.35 + 0.85 * 0.35 + 0.75 * 0.20 + 0.70 * 0.10
        assert result["kpi_composite"].iloc[0] == pytest.approx(expected, rel=1e-4)

    def test_labour_cost_de(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["labour_cost_index"].iloc[0] == pytest.approx(1.30)

    def test_supplier_tier_ordinal(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["supplier_tier_ordinal"].iloc[0] == 0  # PREFERRED = 0

    def test_certifications_detected(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["has_iso9001"].iloc[0] == 1
        assert result["has_iatf"].iloc[0] == 1

    def test_not_capacity_constrained(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["is_capacity_constrained"].iloc[0] == 0

    def test_is_established(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["is_established"].iloc[0] == 1

    def test_price_above_market_flag(self, sample):
        t = SupplierFeatureTransformer()
        result = t.fit_transform(sample)
        assert result["price_above_market"].iloc[0] == 0  # -5% deviation = below market

    def test_missing_values_handled(self):
        df = pd.DataFrame([{"overall_score": np.nan, "country_code": "DE"}])
        t = SupplierFeatureTransformer()
        t.fit(pd.DataFrame([{"overall_score": 0.75}]))
        result = t.transform(df)
        assert not np.isnan(result["overall_score"].iloc[0])
