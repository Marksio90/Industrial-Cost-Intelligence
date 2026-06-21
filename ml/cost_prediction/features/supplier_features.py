"""
Supplier feature engineering.

Raw inputs               → Engineered features
────────────────────────────────────────────────
overall_score            → as-is + tier (1-5)
quality_score            → as-is
delivery_score           → as-is
price_score              → as-is
financial_score          → as-is
country_code             → target-encoded + labour cost index
years_active             → log
num_certifications       → as-is + has_iatf, has_iso9001 flags
capacity_utilisation     → as-is + busy_flag (>0.85)
avg_lead_time_days       → log
quote_win_rate           → logit
avg_price_deviation_pct  → signed deviation from market median
supplier_tier            → ordinal (PREFERRED < APPROVED < STANDARD < NEW)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, TransformerMixin

# Rough labour cost index by country (EU avg = 1.0)
_LABOUR_COST_INDEX: dict[str, float] = {
    "DE": 1.30, "AT": 1.25, "CH": 1.60, "SE": 1.35, "FI": 1.20,
    "NL": 1.20, "BE": 1.20, "FR": 1.10, "IT": 1.05, "ES": 0.85,
    "PL": 0.55, "CZ": 0.60, "SK": 0.58, "HU": 0.55, "RO": 0.45,
    "PT": 0.70, "GR": 0.65, "BG": 0.40, "HR": 0.55,
    "CN": 0.30, "IN": 0.20, "TR": 0.40, "MX": 0.35, "US": 1.40,
}

_SUPPLIER_TIERS = {"PREFERRED": 0, "APPROVED": 1, "STANDARD": 2, "NEW": 3}


class SupplierFeatureTransformer(BaseEstimator, TransformerMixin):
    def __init__(self) -> None:
        self._score_means: dict[str, float] = {}
        self._country_means: dict[str, float] = {}  # target-encoded

    def fit(self, X: pd.DataFrame, y=None) -> "SupplierFeatureTransformer":
        X = _df(X)
        for col in [
            "overall_score", "quality_score", "delivery_score",
            "price_score", "financial_score", "years_active",
            "capacity_utilisation", "avg_lead_time_days",
            "quote_win_rate", "avg_price_deviation_pct",
        ]:
            if col in X.columns:
                self._score_means[col] = float(X[col].mean())

        # Target encoding for country_code (requires y; fall back to labour index)
        if y is not None and "country_code" in X.columns:
            tmp = X[["country_code"]].copy()
            tmp["_y"] = np.array(y)
            self._country_means = (
                tmp.groupby("country_code")["_y"].mean().to_dict()
            )
        return self

    def transform(self, X: pd.DataFrame) -> pd.DataFrame:
        X = _df(X).copy()

        for col, mean in self._score_means.items():
            if col in X.columns:
                X[col] = X[col].fillna(mean)

        # Composite KPI score (mirrors SQL generated column)
        score_weights = {
            "quality_score": 0.35,
            "delivery_score": 0.35,
            "price_score": 0.20,
            "financial_score": 0.10,
        }
        kpi = pd.Series(np.zeros(len(X)), index=X.index)
        for col, w in score_weights.items():
            if col in X.columns:
                kpi += X[col].fillna(0) * w
        X["kpi_composite"] = kpi

        # Score tier (0=poor, 4=excellent)
        X["supplier_score_tier"] = pd.cut(
            X.get("overall_score", kpi),
            bins=[-np.inf, 0.3, 0.5, 0.7, 0.85, np.inf],
            labels=[0, 1, 2, 3, 4],
        ).astype(float)

        # Years active log
        if "years_active" in X.columns:
            X["log_years_active"] = np.log1p(X["years_active"].clip(0))
            X["is_established"] = (X["years_active"] >= 10).astype(np.int8)

        # Lead time log
        if "avg_lead_time_days" in X.columns:
            X["log_avg_lead_time"] = np.log1p(X["avg_lead_time_days"].clip(0))

        # Capacity: busy flag
        if "capacity_utilisation" in X.columns:
            X["is_capacity_constrained"] = (
                X["capacity_utilisation"] > 0.85
            ).astype(np.int8)

        # Win rate: logit
        if "quote_win_rate" in X.columns:
            p = X["quote_win_rate"].clip(1e-4, 1 - 1e-4)
            X["win_rate_logit"] = np.log(p / (1 - p))

        # Country: labour cost index + target encoding
        if "country_code" in X.columns:
            X["labour_cost_index"] = (
                X["country_code"].map(_LABOUR_COST_INDEX).fillna(0.80)
            )
            if self._country_means:
                global_mean = np.mean(list(self._country_means.values()))
                X["country_target_enc"] = (
                    X["country_code"].map(self._country_means).fillna(global_mean)
                )

        # Supplier tier ordinal
        if "supplier_tier" in X.columns:
            X["supplier_tier_ordinal"] = (
                X["supplier_tier"].map(_SUPPLIER_TIERS).fillna(2).astype(np.int8)
            )

        # Certification flags
        if "certifications" in X.columns:
            certs = X["certifications"].fillna("").str.upper()
            X["has_iso9001"] = certs.str.contains("ISO 9001|ISO9001").astype(np.int8)
            X["has_iatf"] = certs.str.contains("IATF").astype(np.int8)
            X["has_iso14001"] = certs.str.contains("ISO 14001|ISO14001").astype(np.int8)
            X["num_certifications"] = certs.str.count(r"ISO|IATF|AS9100|NADCAP")

        # Price deviation: positive = above market (expensive)
        if "avg_price_deviation_pct" in X.columns:
            X["price_above_market"] = (X["avg_price_deviation_pct"] > 0).astype(np.int8)
            X["abs_price_deviation"] = X["avg_price_deviation_pct"].abs()

        return X


def _df(X) -> pd.DataFrame:
    return X if isinstance(X, pd.DataFrame) else pd.DataFrame(X)
