"""Generate dummy ML models for ICI cost prediction to bootstrap the system."""
from __future__ import annotations

import pickle
import os

import numpy as np
from sklearn.ensemble import RandomForestRegressor


def generate_unit_cost_model(path: str) -> None:
    """Generate a dummy XGBoost-like model (using sklearn RF as stand-in)."""
    rng = np.random.RandomState(42)
    X = rng.randn(500, 20)
    y = 10 + 5 * X[:, 0] + 3 * X[:, 1] + rng.randn(500) * 2
    model = RandomForestRegressor(n_estimators=50, random_state=42)
    model.fit(X, y)
    with open(path, "wb") as f:
        pickle.dump(model, f)
    print(f"Dummy unit-cost model saved to {path}")


if __name__ == "__main__":
    out_dir = os.path.dirname(os.path.abspath(__file__))
    generate_unit_cost_model(os.path.join(out_dir, "unit_cost_model.pkl"))
