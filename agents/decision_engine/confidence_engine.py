"""
Confidence Engine — multi-dimensional confidence scoring for recommendations.
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from typing import Any
from .models import (
    DecisionContext, DecisionNode, ConfidenceScore, ConfidenceLevel,
    DataQuality, UrgencyLevel,
)
from .decision_graph import TraversalResult

_DQ_SCORE = {
    DataQuality.VERIFIED: 1.0,
    DataQuality.ESTIMATED: 0.75,
    DataQuality.STALE: 0.5,
    DataQuality.MISSING: 0.1,
    DataQuality.CONFLICTING: 0.3,
}

def _dq(val: DataQuality | None) -> float:
    return _DQ_SCORE.get(val, 0.5) if val else 0.5

class ConfidenceEngine:
    def compute(self, ctx: DecisionContext, signals: dict[str, Any], node: DecisionNode, traversal: TraversalResult) -> ConfidenceScore:
        dq = self._data_quality(ctx)
        ss = self._signal_strength(signals, node)
        consensus = self._consensus(traversal, node)
        hist = 0.75  # baseline; would be updated from feedback store
        missing = self._missing_fields(ctx)

        score = ConfidenceScore(
            overall=0.0,
            level=ConfidenceLevel.MEDIUM,
            data_quality=dq,
            signal_strength=ss,
            consensus=consensus,
            historical_accuracy=hist,
            n_signals=len([v for v in signals.values() if v is not None]),
            n_conflicts=0,
            missing_data=missing,
        )
        score.compute()
        return score

    def _data_quality(self, ctx: DecisionContext) -> float:
        scores = []
        if ctx.cost:
            scores.append(_dq(ctx.cost.data_quality))
        if ctx.supplier:
            scores.append(_dq(ctx.supplier.data_quality))
        if ctx.market:
            scores.append(_dq(ctx.market.data_quality))
        if ctx.production:
            scores.append(_dq(ctx.production.data_quality))
        return sum(scores) / len(scores) if scores else 0.3

    def _signal_strength(self, signals: dict[str, Any], node: DecisionNode) -> float:
        key = node.signal_key
        if not key or key not in signals:
            return 0.5
        val = signals[key]
        thr = node.threshold or 0
        if val is None:
            return 0.3
        try:
            ratio = abs(float(val) - float(thr)) / (abs(float(thr)) + 1e-9)
            return min(1.0, 0.5 + ratio * 0.5)
        except (TypeError, ValueError):
            return 0.6

    def _consensus(self, traversal: TraversalResult, node: DecisionNode) -> float:
        action_count = len(traversal.action_nodes)
        if action_count == 0:
            return 0.5
        # Higher consensus when fewer conflicting actions
        return max(0.4, 1.0 - (action_count - 1) * 0.05)

    def _missing_fields(self, ctx: DecisionContext) -> list[str]:
        missing = []
        if not ctx.cost:
            missing.append("cost_context")
        if not ctx.supplier:
            missing.append("supplier_context")
        if not ctx.market:
            missing.append("market_context")
        if not ctx.production:
            missing.append("production_context")
        return missing
