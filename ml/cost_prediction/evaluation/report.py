"""
Model evaluation report generator.
Logs metrics, plots, and artefacts to MLflow.
"""
from __future__ import annotations

import io
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .metrics import RegressionMetrics, compute_all, percentile_errors


def log_to_mlflow(
    run,
    metrics: RegressionMetrics,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    prefix: str = "",
    log_plots: bool = True,
) -> None:
    """Log all metrics and evaluation plots to an active MLflow run."""
    import mlflow

    tag = f"{prefix}_" if prefix else ""

    # Scalar metrics
    mlflow.log_metrics({
        f"{tag}mape":           metrics.mape,
        f"{tag}wmape":          metrics.wmape,
        f"{tag}smape":          metrics.smape,
        f"{tag}mae":            metrics.mae,
        f"{tag}rmse":           metrics.rmse,
        f"{tag}rmsle":          metrics.rmsle,
        f"{tag}r2":             metrics.r2,
        f"{tag}mdape":          metrics.mdape,
        f"{tag}coverage_10pct": metrics.coverage_10pct,
        f"{tag}coverage_20pct": metrics.coverage_20pct,
        f"{tag}n_samples":      metrics.n_samples,
    })

    # Percentile breakdown
    pct = percentile_errors(y_true, y_pred)
    mlflow.log_metrics({f"{tag}{k}": v for k, v in pct.items()})

    if log_plots:
        _log_actual_vs_predicted(run, y_true, y_pred, tag)
        _log_residuals_plot(run, y_true, y_pred, tag)
        _log_error_distribution(run, y_true, y_pred, tag)


def build_segment_report(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    segments: pd.Series,
) -> pd.DataFrame:
    """Compute metrics per segment (e.g. material_class, process_type)."""
    rows = []
    for seg in segments.unique():
        mask = segments == seg
        if mask.sum() < 5:
            continue
        m = compute_all(y_true[mask], y_pred[mask])
        rows.append({
            "segment": seg,
            "n": mask.sum(),
            "mape": m.mape,
            "mae": m.mae,
            "rmse": m.rmse,
            "r2": m.r2,
            "coverage_10pct": m.coverage_10pct,
        })
    return pd.DataFrame(rows).sort_values("mape", ascending=False)


# ── Private plot helpers ───────────────────────────────────────────────────

def _log_actual_vs_predicted(run, y_true, y_pred, tag: str) -> None:
    import mlflow
    fig, ax = plt.subplots(figsize=(7, 7))
    lim = max(y_true.max(), y_pred.max()) * 1.05
    ax.scatter(y_true, y_pred, alpha=0.3, s=10, color="steelblue")
    ax.plot([0, lim], [0, lim], "r--", linewidth=1.2, label="Perfect")
    ax.set_xlabel("Actual cost (€)")
    ax.set_ylabel("Predicted cost (€)")
    ax.set_title(f"Actual vs Predicted — {tag or 'eval'}")
    ax.legend()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
    buf.seek(0)
    mlflow.log_figure(fig, f"{tag}actual_vs_predicted.png")
    plt.close(fig)


def _log_residuals_plot(run, y_true, y_pred, tag: str) -> None:
    import mlflow
    residuals = y_pred - y_true
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.scatter(y_pred, residuals, alpha=0.3, s=10, color="coral")
    ax.axhline(0, color="black", linewidth=1)
    ax.set_xlabel("Predicted (€)")
    ax.set_ylabel("Residual (€)")
    ax.set_title(f"Residuals vs Predicted — {tag or 'eval'}")
    mlflow.log_figure(fig, f"{tag}residuals.png")
    plt.close(fig)


def _log_error_distribution(run, y_true, y_pred, tag: str) -> None:
    import mlflow
    ape = np.abs(y_true - y_pred) / np.maximum(np.abs(y_true), 1.0) * 100
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.hist(ape.clip(0, 100), bins=50, color="teal", edgecolor="white")
    ax.axvline(np.median(ape), color="red", linestyle="--", label=f"Median APE={np.median(ape):.1f}%")
    ax.set_xlabel("Absolute Percentage Error (%)")
    ax.set_ylabel("Count")
    ax.set_title(f"Error Distribution — {tag or 'eval'}")
    ax.legend()
    mlflow.log_figure(fig, f"{tag}error_distribution.png")
    plt.close(fig)
