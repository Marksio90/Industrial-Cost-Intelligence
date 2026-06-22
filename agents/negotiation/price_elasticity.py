"""
Section 4 — Price Elasticity Model

Estimates how price-sensitive a supplier is to our offer changes.

Models implemented:
  1. Arc elasticity        — point-to-point measurement from historical rounds
  2. Log-log regression    — fit to all historical rounds for a material class
  3. Monte Carlo band      — uncertainty quantification via bootstrapped simulation
  4. Seasonal adjustment   — commodity prices follow seasonal cycles
  5. Volume discount curve — non-linear price-volume relationship (power law)

Price elasticity of acceptance (PEA):
  PEA = Δ%acceptance / Δ%price
  PEA > 1  → elastic   (small price increase → large drop in acceptance prob)
  PEA < 1  → inelastic (price matters less than other factors)

Volume discount elasticity:
  P(Q) = P₀ × Q^(-α)
  α > 0 → higher volume = lower unit price
  Typical α ≈ 0.05 – 0.15 for industrial materials
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ElasticityEstimate:
    coefficient:   float           # central estimate
    lower_bound:   float           # 5th percentile (MC)
    upper_bound:   float           # 95th percentile (MC)
    confidence:    float           # 0–1
    interpretation: str
    basis:         str             # "arc" | "regression" | "heuristic"


@dataclass
class PriceBand:
    """Probabilistic price range the supplier is likely to accept."""
    very_likely:   float   # P(accept) > 0.8
    likely:        float   # P(accept) > 0.5
    unlikely:      float   # P(accept) > 0.2
    anchor_price:  float   # recommended opening offer (aggressive but not insulting)
    target_price:  float   # realistic landing zone
    currency:      str = "EUR"


@dataclass
class VolumeDiscount:
    base_price:   float
    exponent:     float   # α in P(Q) = P₀ × Q^(-α)
    break_points: list[tuple[int, float]]   # [(qty, price), ...]


@dataclass
class SeasonalFactor:
    """Multiplier by month (1=Jan, 12=Dec). Values > 1 = higher prices."""
    month_factors: list[float] = field(default_factory=lambda: [1.0] * 12)

    def factor_for(self, month: int) -> float:
        return self.month_factors[(month - 1) % 12]


# ─────────────────────────────────────────────────────────────────────────────
# Material-class seasonal defaults (empirical approximations)
# ─────────────────────────────────────────────────────────────────────────────

_SEASONAL_DEFAULTS: dict[str, list[float]] = {
    "METAL": [
        1.02, 1.03, 1.05, 1.04, 1.02, 0.99,
        0.97, 0.96, 0.98, 1.01, 1.02, 1.01
    ],
    "PLASTIC": [
        1.00, 1.01, 1.02, 1.01, 1.00, 0.99,
        0.98, 0.98, 0.99, 1.00, 1.00, 1.00
    ],
    "ELECTRONIC": [
        1.01, 1.00, 0.99, 1.00, 1.01, 1.02,
        1.02, 1.01, 1.00, 0.99, 1.02, 1.03
    ],
    "DEFAULT": [1.0] * 12,
}


def get_seasonal_factor(material_class: str, month: int) -> float:
    factors = _SEASONAL_DEFAULTS.get(material_class, _SEASONAL_DEFAULTS["DEFAULT"])
    return factors[(month - 1) % 12]


# ─────────────────────────────────────────────────────────────────────────────
# Arc Elasticity Calculator
# ─────────────────────────────────────────────────────────────────────────────

class ArcElasticityCalculator:
    """
    Measures elasticity between two data points:
      E = (ΔQ/Q_avg) / (ΔP/P_avg)
    where Q = acceptance probability, P = price.
    """

    def compute(
        self,
        price1: float, acceptance1: float,
        price2: float, acceptance2: float,
    ) -> ElasticityEstimate:
        p_avg = (price1 + price2) / 2
        q_avg = (acceptance1 + acceptance2) / 2

        if p_avg == 0 or q_avg == 0:
            return self._fallback()

        delta_p = price2 - price1
        delta_q = acceptance2 - acceptance1

        e = (delta_q / q_avg) / (delta_p / p_avg)

        return ElasticityEstimate(
            coefficient    = e,
            lower_bound    = e * 0.75,
            upper_bound    = e * 1.25,
            confidence     = 0.5,
            interpretation = _interpret_elasticity(e),
            basis          = "arc",
        )

    @staticmethod
    def _fallback() -> ElasticityEstimate:
        return ElasticityEstimate(
            coefficient=1.2, lower_bound=0.8, upper_bound=1.8,
            confidence=0.1, interpretation="moderately elastic", basis="heuristic",
        )


# ─────────────────────────────────────────────────────────────────────────────
# Log-Log Regression
# ─────────────────────────────────────────────────────────────────────────────

class LogLogElasticityRegressor:
    """
    Fits ln(acceptance_prob) = β₀ + β₁ × ln(relative_price) on historical data.
    β₁ is the price elasticity coefficient.
    """

    def fit(
        self,
        price_history:       list[float],
        acceptance_history:  list[float],
        base_price:          float,
    ) -> ElasticityEstimate:
        if len(price_history) < 3:
            return ElasticityEstimate(
                coefficient=1.5, lower_bound=0.9, upper_bound=2.5,
                confidence=0.1, interpretation="estimated (insufficient data)", basis="heuristic",
            )

        # Log-transform
        x = []
        y = []
        for p, a in zip(price_history, acceptance_history):
            if p > 0 and a > 0 and base_price > 0:
                x.append(math.log(p / base_price))
                y.append(math.log(max(a, 0.001)))

        if len(x) < 3:
            return ElasticityEstimate(
                coefficient=1.5, lower_bound=0.9, upper_bound=2.5,
                confidence=0.1, interpretation="estimated", basis="heuristic",
            )

        # OLS: β₁ = Cov(x,y) / Var(x)
        x_mean = statistics.mean(x)
        y_mean = statistics.mean(y)
        cov    = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y)) / len(x)
        var_x  = sum((xi - x_mean) ** 2 for xi in x) / len(x)
        beta1  = cov / var_x if var_x != 0 else -1.5

        # Residual std for confidence
        y_hat  = [y_mean + beta1 * (xi - x_mean) for xi in x]
        resid  = [yi - yh for yi, yh in zip(y, y_hat)]
        rmse   = math.sqrt(statistics.mean(r ** 2 for r in resid)) if resid else 1.0
        conf   = max(0.0, min(1.0, 1.0 - rmse / (abs(beta1) + 1e-6)))

        # Bootstrap confidence interval
        lb, ub = self._bootstrap_ci(x, y, n_iter=200)

        return ElasticityEstimate(
            coefficient    = beta1,
            lower_bound    = lb,
            upper_bound    = ub,
            confidence     = conf,
            interpretation = _interpret_elasticity(beta1),
            basis          = "regression",
        )

    @staticmethod
    def _bootstrap_ci(
        x: list[float],
        y: list[float],
        n_iter: int = 200,
        ci: float = 0.90,
    ) -> tuple[float, float]:
        betas: list[float] = []
        n = len(x)
        for _ in range(n_iter):
            indices = [random.randint(0, n - 1) for _ in range(n)]
            bx = [x[i] for i in indices]
            by = [y[i] for i in indices]
            mx = statistics.mean(bx); my = statistics.mean(by)
            cov = sum((bx[i] - mx) * (by[i] - my) for i in range(n)) / n
            var = sum((bx[i] - mx) ** 2 for i in range(n)) / n
            if var > 1e-10:
                betas.append(cov / var)
        if not betas:
            return (-2.0, -0.5)
        betas.sort()
        lo = int((1 - ci) / 2 * len(betas))
        hi = int((1 + ci) / 2 * len(betas))
        return (betas[lo], betas[min(hi, len(betas) - 1)])


# ─────────────────────────────────────────────────────────────────────────────
# Volume Discount Curve Fitter
# ─────────────────────────────────────────────────────────────────────────────

class VolumeDiscountCurveFitter:
    """
    Fits P(Q) = P₀ × Q^(-α) to historical (quantity, price) pairs.
    Used to model how much discount we can expect by increasing order volume.
    """

    def fit(
        self,
        quantity_history: list[float],
        price_history:    list[float],
    ) -> VolumeDiscount:
        if len(quantity_history) < 2:
            return VolumeDiscount(
                base_price   = price_history[0] if price_history else 1.0,
                exponent     = 0.08,
                break_points = [],
            )

        # Log-log regression: ln(P) = ln(P₀) - α×ln(Q)
        log_q = [math.log(q) for q in quantity_history if q > 0]
        log_p = [math.log(p) for p in price_history   if p > 0]
        if len(log_q) < 2:
            return VolumeDiscount(base_price=price_history[0], exponent=0.08, break_points=[])

        mq = statistics.mean(log_q); mp = statistics.mean(log_p)
        cov = sum((lq - mq) * (lp - mp) for lq, lp in zip(log_q, log_p)) / len(log_q)
        var = sum((lq - mq) ** 2 for lq in log_q) / len(log_q)
        alpha = -cov / var if var > 0 else 0.08
        p0    = math.exp(mp - alpha * mq)

        # Generate break-point table at standard quantities
        std_qtys = [100, 500, 1_000, 5_000, 10_000, 50_000]
        bps = [(q, p0 * (q ** (-alpha))) for q in std_qtys if q > 0]

        return VolumeDiscount(
            base_price   = p0,
            exponent     = max(0.0, min(0.30, alpha)),
            break_points = bps,
        )

    def predict_price(self, curve: VolumeDiscount, quantity: float) -> float:
        if quantity <= 0:
            return curve.base_price
        return curve.base_price * (quantity ** (-curve.exponent))

    def volume_for_target_price(self, curve: VolumeDiscount, target_price: float) -> float | None:
        """What quantity do we need to reach target_price?"""
        if curve.exponent <= 0 or target_price <= 0:
            return None
        # target = P0 × Q^(-α)  →  Q = (P0/target)^(1/α)
        try:
            return (curve.base_price / target_price) ** (1.0 / curve.exponent)
        except ZeroDivisionError:
            return None


# ─────────────────────────────────────────────────────────────────────────────
# Monte Carlo Price Band Simulator
# ─────────────────────────────────────────────────────────────────────────────

class MonteCarloPriceBandSimulator:
    """
    Runs N scenarios, sampling elasticity and acceptance probability
    distributions, to generate price bands with confidence intervals.
    """

    def __init__(self, n_simulations: int = 2000, seed: int = 42) -> None:
        self._n    = n_simulations
        self._seed = seed

    def simulate(
        self,
        initial_ask:  float,
        target_price: float,
        walk_away:    float,
        elasticity:   ElasticityEstimate,
    ) -> PriceBand:
        rng = random.Random(self._seed)

        # Sample elasticity from a truncated normal
        e_std = (elasticity.upper_bound - elasticity.lower_bound) / 3.92
        e_mean = elasticity.coefficient

        def sample_e() -> float:
            return rng.gauss(e_mean, e_std)

        # For each candidate price from walk_away to initial_ask, estimate P(accept)
        steps    = 50
        price_step = (initial_ask - walk_away) / steps
        prices   = [walk_away + i * price_step for i in range(steps + 1)]
        accept_p = []

        for price in prices:
            relative = (price - initial_ask) / initial_ask if initial_ask else 0

            # Monte Carlo acceptance probability
            probs = []
            for _ in range(self._n // steps):
                e = sample_e()
                # Logistic model: P = sigmoid(k * (gap - threshold))
                base_prob = 1.0 / (1.0 + math.exp(-e * (relative + 0.05)))
                probs.append(max(0.0, min(1.0, base_prob)))

            accept_p.append(statistics.mean(probs))

        # Find prices at probability thresholds
        very_likely = _price_at_prob(prices, accept_p, 0.80)
        likely      = _price_at_prob(prices, accept_p, 0.50)
        unlikely    = _price_at_prob(prices, accept_p, 0.20)

        anchor = max(walk_away, target_price * 0.82)

        return PriceBand(
            very_likely  = very_likely or walk_away,
            likely       = likely      or target_price,
            unlikely     = unlikely    or initial_ask,
            anchor_price = anchor,
            target_price = target_price,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Unified Price Elasticity Service
# ─────────────────────────────────────────────────────────────────────────────

class PriceElasticityService:
    """
    Facade combining all elasticity models into a single price band recommendation.
    Called during ANALYZING phase before first offer is sent.
    """

    def __init__(self) -> None:
        self._arc        = ArcElasticityCalculator()
        self._regressor  = LogLogElasticityRegressor()
        self._mc         = MonteCarloPriceBandSimulator()
        self._volume     = VolumeDiscountCurveFitter()

    def analyze(
        self,
        initial_ask:       float,
        target_price:      float,
        walk_away:         float,
        quantity:          float,
        material_class:    str,
        month:             int,
        price_history:     list[float] | None = None,
        acceptance_history: list[float] | None = None,
        qty_price_pairs:   list[tuple[float, float]] | None = None,
    ) -> dict[str, Any]:
        """
        Returns a dict with elasticity estimate, price band, volume curve,
        and seasonal-adjusted price recommendation.
        """
        # Elasticity estimate
        if price_history and acceptance_history and len(price_history) >= 3:
            elasticity = self._regressor.fit(price_history, acceptance_history, initial_ask)
        else:
            elasticity = ElasticityEstimate(
                coefficient=1.5, lower_bound=0.8, upper_bound=2.5,
                confidence=0.2, interpretation="moderately elastic (default)", basis="heuristic",
            )

        # Price band
        band = self._mc.simulate(initial_ask, target_price, walk_away, elasticity)

        # Volume discount
        if qty_price_pairs and len(qty_price_pairs) >= 2:
            qtys   = [p[0] for p in qty_price_pairs]
            prices = [p[1] for p in qty_price_pairs]
            vol_curve = self._volume.fit(qtys, prices)
            vol_adjusted = self._volume.predict_price(vol_curve, quantity)
        else:
            vol_curve    = None
            vol_adjusted = target_price

        # Seasonal adjustment
        seasonal_factor  = get_seasonal_factor(material_class, month)
        seasonal_adjusted = vol_adjusted * seasonal_factor

        return {
            "elasticity":           elasticity,
            "price_band":           band,
            "volume_curve":         vol_curve,
            "volume_adjusted_price": vol_adjusted,
            "seasonal_factor":      seasonal_factor,
            "seasonal_adjusted_price": seasonal_adjusted,
            "recommended_anchor":   band.anchor_price,
            "recommended_target":   target_price,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _interpret_elasticity(e: float) -> str:
    e = abs(e)
    if e > 2.0:
        return "highly elastic (very sensitive to price changes)"
    if e > 1.0:
        return "elastic (sensitive to price changes)"
    if e > 0.5:
        return "moderately inelastic (some price sensitivity)"
    return "inelastic (price matters less than other factors)"


def _price_at_prob(
    prices:   list[float],
    probs:    list[float],
    target_p: float,
) -> float | None:
    for price, prob in zip(reversed(prices), reversed(probs)):
        if prob >= target_p:
            return price
    return None
