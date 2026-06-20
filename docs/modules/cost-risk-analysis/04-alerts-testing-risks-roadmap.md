# CRAE — Alerty, Testy, Ryzyka, Roadmap

## 10. Alert Engine

### 10.1 AlertEngine

```python
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from .models import RiskFactor, RiskLevel, RiskDomain

logger = logging.getLogger(__name__)


class AlertChannel(str, Enum):
    EMAIL        = "EMAIL"
    SLACK        = "SLACK"
    PAGERDUTY    = "PAGERDUTY"
    TEAMS        = "TEAMS"
    WEBHOOK      = "WEBHOOK"


class AlertSeverity(str, Enum):
    INFO     = "INFO"
    WARNING  = "WARNING"
    CRITICAL = "CRITICAL"
    DISASTER = "DISASTER"


@dataclass
class AlertRule:
    rule_id: str
    name: str
    condition: str                    # human-readable
    severity: AlertSeverity
    channels: list[AlertChannel]
    cooldown_minutes: int = 60
    auto_escalate_minutes: int | None = None


@dataclass
class Alert:
    alert_id: UUID = field(default_factory=uuid4)
    rule_id: str = ""
    severity: AlertSeverity = AlertSeverity.INFO
    title: str = ""
    body: str = ""
    risk_factor_id: UUID | None = None
    portfolio_id: UUID | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    acknowledged_at: datetime | None = None
    resolved_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


ALERT_RULES: list[AlertRule] = [
    AlertRule(
        rule_id="AR-001",
        name="Critical Risk Detected",
        condition="risk_factor.level == CRITICAL",
        severity=AlertSeverity.CRITICAL,
        channels=[AlertChannel.PAGERDUTY, AlertChannel.SLACK],
        cooldown_minutes=30,
        auto_escalate_minutes=60,
    ),
    AlertRule(
        rule_id="AR-002",
        name="Portfolio VaR Breach",
        condition="portfolio.var_95_eur > threshold_eur (default: 5_000_000)",
        severity=AlertSeverity.CRITICAL,
        channels=[AlertChannel.EMAIL, AlertChannel.TEAMS],
        cooldown_minutes=120,
    ),
    AlertRule(
        rule_id="AR-003",
        name="Supplier Concentration Risk",
        condition="single_supplier_spend_share > 0.40",
        severity=AlertSeverity.WARNING,
        channels=[AlertChannel.EMAIL, AlertChannel.SLACK],
        cooldown_minutes=1440,  # once per day
    ),
    AlertRule(
        rule_id="AR-004",
        name="Supplier Financial Distress",
        condition="altman_z_score < 1.23 (DISTRESS zone)",
        severity=AlertSeverity.CRITICAL,
        channels=[AlertChannel.PAGERDUTY, AlertChannel.EMAIL],
        cooldown_minutes=240,
        auto_escalate_minutes=120,
    ),
    AlertRule(
        rule_id="AR-005",
        name="Commodity Price Spike",
        condition="price_volatility_30d > 0.10 AND forecast_change_90d > 0.10",
        severity=AlertSeverity.WARNING,
        channels=[AlertChannel.SLACK, AlertChannel.TEAMS],
        cooldown_minutes=180,
    ),
    AlertRule(
        rule_id="AR-006",
        name="OEE Critical Drop",
        condition="machine.oee_avg_3d < 0.65",
        severity=AlertSeverity.CRITICAL,
        channels=[AlertChannel.PAGERDUTY, AlertChannel.SLACK],
        cooldown_minutes=30,
    ),
    AlertRule(
        rule_id="AR-007",
        name="Material Stock Depletion",
        condition="stock_days < reorder_point",
        severity=AlertSeverity.CRITICAL,
        channels=[AlertChannel.EMAIL, AlertChannel.TEAMS, AlertChannel.SLACK],
        cooldown_minutes=60,
    ),
    AlertRule(
        rule_id="AR-008",
        name="Certification Expiry",
        condition="cert_expiry_days < 30",
        severity=AlertSeverity.WARNING,
        channels=[AlertChannel.EMAIL],
        cooldown_minutes=1440,
    ),
    AlertRule(
        rule_id="AR-009",
        name="Portfolio Score Surge",
        condition="composite_score increase > 15 points in 24h",
        severity=AlertSeverity.WARNING,
        channels=[AlertChannel.SLACK, AlertChannel.TEAMS],
        cooldown_minutes=360,
    ),
    AlertRule(
        rule_id="AR-010",
        name="Geopolitical Tier-5 Supplier",
        condition="supplier.country risk_tier == 5 (RU/BY)",
        severity=AlertSeverity.DISASTER,
        channels=[AlertChannel.PAGERDUTY, AlertChannel.EMAIL, AlertChannel.TEAMS],
        cooldown_minutes=2880,  # 2 days
        auto_escalate_minutes=240,
    ),
]


class AlertEngine:
    """Evaluates risks against rules, dispatches alerts, tracks cooldowns."""

    def __init__(
        self,
        db_pool,
        slack_client=None,
        pagerduty_client=None,
        email_client=None,
        teams_client=None,
    ) -> None:
        self._db = db_pool
        self._slack = slack_client
        self._pd = pagerduty_client
        self._email = email_client
        self._teams = teams_client
        self._cooldowns: dict[str, datetime] = {}   # rule_id → last_fired

    async def process_risks(self, risks: list[RiskFactor]) -> list[Alert]:
        alerts: list[Alert] = []
        for risk in risks:
            for rule in ALERT_RULES:
                if await self._should_fire(rule, risk):
                    alert = await self._build_alert(rule, risk)
                    await self._dispatch(alert)
                    alerts.append(alert)
                    self._cooldowns[rule.rule_id] = alert.created_at
        return alerts

    async def process_portfolio_breach(
        self, var_95_eur: float, threshold_eur: float = 5_000_000.0
    ) -> Alert | None:
        if var_95_eur <= threshold_eur:
            return None
        rule = next(r for r in ALERT_RULES if r.rule_id == "AR-002")
        if not self._cooldown_ok(rule):
            return None
        alert = Alert(
            rule_id=rule.rule_id,
            severity=rule.severity,
            title="Portfolio VaR Breach",
            body=(
                f"Current VaR-95 = {var_95_eur:,.0f} EUR exceeds threshold "
                f"{threshold_eur:,.0f} EUR (+{(var_95_eur/threshold_eur - 1)*100:.1f}%)"
            ),
            metadata={"var_95_eur": var_95_eur, "threshold_eur": threshold_eur},
        )
        await self._dispatch(alert)
        self._cooldowns[rule.rule_id] = alert.created_at
        return alert

    async def _should_fire(self, rule: AlertRule, risk: RiskFactor) -> bool:
        if not self._cooldown_ok(rule):
            return False
        return await self._evaluate_rule(rule, risk)

    def _cooldown_ok(self, rule: AlertRule) -> bool:
        last = self._cooldowns.get(rule.rule_id)
        if last is None:
            return True
        from datetime import timedelta
        elapsed = datetime.now(timezone.utc) - last
        return elapsed.total_seconds() > rule.cooldown_minutes * 60

    async def _evaluate_rule(self, rule: AlertRule, risk: RiskFactor) -> bool:
        mapping = {
            "AR-001": lambda r: r.score >= 75,
            "AR-003": lambda r: r.category.value == "SR01" and r.probability > 0.40,
            "AR-004": lambda r: r.category.value == "SR03",
            "AR-005": lambda r: r.category.value in ("MR01", "MR02") and r.score >= 60,
            "AR-006": lambda r: r.category.value == "PR01" and r.score >= 75,
            "AR-007": lambda r: r.category.value == "MT02" and r.score >= 75,
            "AR-008": lambda r: r.category.value == "MT04",
            "AR-010": lambda r: r.category.value == "SR06" and r.probability >= 0.90,
        }
        fn = mapping.get(rule.rule_id)
        return fn(risk) if fn else False

    async def _build_alert(self, rule: AlertRule, risk: RiskFactor) -> Alert:
        return Alert(
            rule_id=rule.rule_id,
            severity=rule.severity,
            title=rule.name,
            body=f"Risk {risk.category.value} | score={risk.score:.1f} | {risk.description}",
            risk_factor_id=risk.risk_id,
            metadata={
                "category": risk.category.value,
                "score": risk.score,
                "probability": risk.probability,
                "impact_eur": str(risk.impact_eur),
            },
        )

    async def _dispatch(self, alert: Alert) -> None:
        rule = next((r for r in ALERT_RULES if r.rule_id == alert.rule_id), None)
        if rule is None:
            return
        tasks = []
        for ch in rule.channels:
            if ch == AlertChannel.SLACK and self._slack:
                tasks.append(self._slack.send(alert))
            elif ch == AlertChannel.PAGERDUTY and self._pd:
                tasks.append(self._pd.trigger(alert))
            elif ch == AlertChannel.EMAIL and self._email:
                tasks.append(self._email.send(alert))
            elif ch == AlertChannel.TEAMS and self._teams:
                tasks.append(self._teams.post(alert))
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    logger.error("Alert dispatch failed: %s", r)

    async def auto_escalate(self, alert: Alert) -> None:
        """Called by scheduler if alert not acknowledged within auto_escalate_minutes."""
        rule = next((r for r in ALERT_RULES if r.rule_id == alert.rule_id), None)
        if rule is None or rule.auto_escalate_minutes is None:
            return
        escalated = Alert(
            rule_id=alert.rule_id,
            severity=AlertSeverity.DISASTER,
            title=f"[ESCALATED] {alert.title}",
            body=f"Original alert unacknowledged. {alert.body}",
            risk_factor_id=alert.risk_factor_id,
            portfolio_id=alert.portfolio_id,
            metadata={**alert.metadata, "escalated_from": str(alert.alert_id)},
        )
        await self._dispatch(escalated)
```

### 10.2 Escalation Matrix

| Alert Rule | Trigger | Severity | Channels | Cooldown | Auto-Escalate |
|------------|---------|----------|----------|----------|---------------|
| AR-001 | score ≥ 75 | CRITICAL | PD + Slack | 30 min | 60 min |
| AR-002 | VaR-95 > 5M EUR | CRITICAL | Email + Teams | 120 min | — |
| AR-003 | Supplier spend > 40% | WARNING | Email + Slack | 24 h | — |
| AR-004 | Altman Z < 1.23 | CRITICAL | PD + Email | 240 min | 120 min |
| AR-005 | Vol > 10% + forecast +10% | WARNING | Slack + Teams | 180 min | — |
| AR-006 | OEE avg 3d < 65% | CRITICAL | PD + Slack | 30 min | — |
| AR-007 | Stock < reorder point | CRITICAL | Email + Teams + Slack | 60 min | — |
| AR-008 | Cert expiry < 30 d | WARNING | Email | 24 h | — |
| AR-009 | Portfolio score +15 pts/24h | WARNING | Slack + Teams | 360 min | — |
| AR-010 | Geo tier-5 supplier | DISASTER | PD + Email + Teams | 48 h | 240 min |

### 10.3 Notification Templates

```python
SLACK_TEMPLATE = """
:rotating_light: *{severity}* — {title}
> {body}

*Risk ID:* `{risk_factor_id}`
*Score:* `{score:.1f}` | *Category:* `{category}`
*Time:* {created_at}

<{dashboard_url}|View in CRAE Dashboard>
"""

PAGERDUTY_PAYLOAD = {
    "routing_key": "${PAGERDUTY_ROUTING_KEY}",
    "event_action": "trigger",
    "payload": {
        "summary": "{title}",
        "severity": "{pd_severity}",   # critical | warning | error | info
        "source": "CRAE",
        "custom_details": {
            "risk_id": "{risk_factor_id}",
            "score": "{score}",
            "category": "{category}",
            "impact_eur": "{impact_eur}",
        },
    },
}
```

---

## 11. Testing

### 11.1 Macierz testów

| # | Typ | Moduł | Kluczowe przypadki | Narzędzie |
|---|-----|-------|--------------------|-----------|
| T01 | Unit | RiskScorer | score=0 (p=0, i=0), score=100 (p=1, i=10M, v=0, d=0), weights sum | pytest |
| T02 | Unit | ImpactCalculator | VaR95 > 0, CVaR95 ≥ VaR95, deterministic seed | pytest |
| T03 | Unit | SupplierRiskAnalyzer | SR01 single-source, SR03 Altman Z zones, SR06 geo tier-5 | pytest |
| T04 | Unit | MarketRiskAnalyzer | MR01 high vol, MR04 energy spike, MR06 inflation passthrough | pytest |
| T05 | Unit | ProductionRiskAnalyzer | PR01 OEE <75%, PR02 bottleneck >35%, PR03 tooling <20% | pytest |
| T06 | Unit | MaterialRiskAnalyzer | MT01 zero suppliers, MT02 depletion, MT04 cert expiry | pytest |
| T07 | Unit | AlertEngine | AR-001 fires on CRITICAL, cooldown blocks repeat, dispatch errors swallowed | pytest |
| T08 | Integration | SQL schema `crae` | GENERATED level column, triggers publish outbox, FK constraints | pytest + asyncpg |
| T09 | Integration | RiskAnalysisOrchestrator | Full pipeline dry-run, portfolio snapshot persists | pytest + testcontainers |
| T10 | Integration | Kafka Outbox | CRAEOutboxPublisher delivers events, at-least-once, no dupes after retry | pytest + kafka-python |
| T11 | Accuracy | Portfolio VaR | Monte Carlo convergence: 10k vs 100k sim diff < 5% | pytest |
| T12 | Load | API k6 | p95 GET /risks < 400ms, p95 POST /portfolio/analyze < 5s | k6 |

### 11.2 Unit Tests — RiskScorer

```python
import math
import pytest
from decimal import Decimal
from uuid import uuid4

from crae.scoring import RiskScorer, ImpactCalculator
from crae.models import RiskFactor, RiskCategory


@pytest.fixture
def scorer() -> RiskScorer:
    return RiskScorer()


@pytest.fixture
def calculator() -> ImpactCalculator:
    return ImpactCalculator(rng_seed=42)


class TestRiskScorer:

    def test_zero_probability_gives_low_score(self, scorer):
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.SR01,
            probability=0.0, impact_eur=Decimal("1_000_000"),
            velocity_days=30, detectability=0.5,
        )
        score = scorer.score(rf)
        assert score < 25, "Zero probability should yield LOW score"

    def test_max_inputs_give_score_near_100(self, scorer):
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.SR03,
            probability=1.0, impact_eur=Decimal("10_000_000"),
            velocity_days=0, detectability=0.0,
        )
        score = scorer.score(rf)
        assert score >= 95

    def test_weights_sum_to_one(self, scorer):
        total = scorer.W_PROB + scorer.W_IMPACT + scorer.W_VELOCITY + scorer.W_DETECT
        assert abs(total - 1.0) < 1e-9

    def test_impact_log_normalization_ceiling(self, scorer):
        # 10M EUR → impact_norm = 1.0 (ceiling)
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.MR01,
            probability=0.5, impact_eur=Decimal("100_000_000"),
            velocity_days=90, detectability=0.5,
        )
        score = scorer.score(rf)
        # Compare with exact 10M EUR impact
        rf2 = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.MR01,
            probability=0.5, impact_eur=Decimal("10_000_000"),
            velocity_days=90, detectability=0.5,
        )
        score2 = scorer.score(rf2)
        assert abs(score - score2) < 0.01, "Impact saturates at log10(10M)=6"

    def test_velocity_zero_days_maximizes_velocity_component(self, scorer):
        base_rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.PR01,
            probability=0.5, impact_eur=Decimal("500_000"),
            velocity_days=180, detectability=0.5,
        )
        fast_rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.PR01,
            probability=0.5, impact_eur=Decimal("500_000"),
            velocity_days=0, detectability=0.5,
        )
        assert scorer.score(fast_rf) > scorer.score(base_rf)

    def test_level_thresholds(self, scorer):
        def make_rf(prob, impact_eur):
            return RiskFactor(
                risk_id=uuid4(), category=RiskCategory.MT01,
                probability=prob, impact_eur=Decimal(str(impact_eur)),
                velocity_days=45, detectability=0.5,
            )
        assert scorer.score(make_rf(0.05, 10_000)) < 25    # LOW
        assert scorer.score(make_rf(0.30, 100_000)) < 50   # MEDIUM range expected
        assert scorer.score(make_rf(0.80, 1_000_000)) > 50


class TestImpactCalculator:

    def test_var95_positive(self, calculator):
        risks = [
            RiskFactor(
                risk_id=uuid4(), category=RiskCategory.SR01,
                probability=0.30, impact_eur=Decimal("500_000"),
                velocity_days=30, detectability=0.4,
            )
        ]
        var95, cvar95 = calculator.monte_carlo_var(risks)
        assert var95 > 0
        assert cvar95 >= var95

    def test_cvar_ge_var(self, calculator):
        risks = [
            RiskFactor(
                risk_id=uuid4(), category=c,
                probability=0.5, impact_eur=Decimal("1_000_000"),
                velocity_days=30, detectability=0.5,
            )
            for c in [RiskCategory.SR01, RiskCategory.MR01, RiskCategory.PR01]
        ]
        var95, cvar95 = calculator.monte_carlo_var(risks)
        assert cvar95 >= var95

    def test_deterministic_with_seed(self):
        risks = [
            RiskFactor(
                risk_id=uuid4(), category=RiskCategory.MR02,
                probability=0.4, impact_eur=Decimal("2_000_000"),
                velocity_days=60, detectability=0.3,
            )
        ]
        calc_a = ImpactCalculator(rng_seed=42)
        calc_b = ImpactCalculator(rng_seed=42)
        var_a, _ = calc_a.monte_carlo_var(risks)
        var_b, _ = calc_b.monte_carlo_var(risks)
        assert var_a == var_b

    def test_convergence_10k_vs_50k(self):
        risks = [
            RiskFactor(
                risk_id=uuid4(), category=RiskCategory.SR03,
                probability=0.25, impact_eur=Decimal("3_000_000"),
                velocity_days=45, detectability=0.4,
            )
        ]
        calc_10k = ImpactCalculator(rng_seed=1, n_simulations=10_000)
        calc_50k = ImpactCalculator(rng_seed=1, n_simulations=50_000)
        var_10k, _ = calc_10k.monte_carlo_var(risks)
        var_50k, _ = calc_50k.monte_carlo_var(risks)
        diff_pct = abs(var_10k - var_50k) / max(var_50k, 1)
        assert diff_pct < 0.05, f"Convergence gap too large: {diff_pct:.2%}"
```

### 11.3 Unit Tests — SupplierRiskAnalyzer

```python
from crae.analyzers.supplier import SupplierRiskAnalyzer, SupplierProfile


class TestSupplierRiskAnalyzer:

    @pytest.fixture
    def analyzer(self, db_pool_mock):
        return SupplierRiskAnalyzer(db_pool=db_pool_mock)

    def test_sr01_single_source_high_spend(self, analyzer):
        profile = SupplierProfile(
            supplier_id=uuid4(), name="Acme Steel",
            spend_share=0.55, is_sole_source_parts=["PART-001", "PART-002"],
            altman_z=2.5, lead_time_drift_pct=0.05, ppm=150,
            country_code="DE",
        )
        risks = analyzer._analyze_supplier(profile)
        sr01 = next(r for r in risks if r.category.value == "SR01")
        assert sr01.score > 50    # HIGH or above

    def test_sr03_distress_zone(self, analyzer):
        profile = SupplierProfile(
            supplier_id=uuid4(), name="Risky Supplier",
            spend_share=0.10, is_sole_source_parts=[],
            altman_z=0.90,  # below 1.23 = DISTRESS
            lead_time_drift_pct=0.0, ppm=80, country_code="PL",
        )
        risks = analyzer._analyze_supplier(profile)
        sr03 = next(r for r in risks if r.category.value == "SR03")
        assert sr03.probability == pytest.approx(0.85, abs=0.01)
        assert sr03.score > 75    # CRITICAL

    def test_sr03_safe_zone(self, analyzer):
        profile = SupplierProfile(
            supplier_id=uuid4(), name="Solid Co",
            spend_share=0.05, is_sole_source_parts=[],
            altman_z=3.50,
            lead_time_drift_pct=0.0, ppm=50, country_code="DE",
        )
        risks = analyzer._analyze_supplier(profile)
        sr03 = next(r for r in risks if r.category.value == "SR03")
        assert sr03.probability == pytest.approx(0.10, abs=0.01)

    def test_sr06_tier5_country(self, analyzer):
        profile = SupplierProfile(
            supplier_id=uuid4(), name="Eastern Supplier",
            spend_share=0.08, is_sole_source_parts=[],
            altman_z=2.5, lead_time_drift_pct=0.0, ppm=100,
            country_code="RU",
        )
        risks = analyzer._analyze_supplier(profile)
        sr06 = next(r for r in risks if r.category.value == "SR06")
        assert sr06.probability == pytest.approx(0.90, abs=0.01)
        assert sr06.score > 75    # CRITICAL

    def test_no_risk_healthy_supplier(self, analyzer):
        profile = SupplierProfile(
            supplier_id=uuid4(), name="Gold Supplier",
            spend_share=0.08, is_sole_source_parts=[],
            altman_z=4.20, lead_time_drift_pct=0.02, ppm=40,
            country_code="DE",
        )
        risks = analyzer._analyze_supplier(profile)
        # All scores should be LOW (< 25)
        assert all(r.score < 25 for r in risks)
```

### 11.4 Unit Tests — AlertEngine

```python
from unittest.mock import AsyncMock, patch
from crae.alerts import AlertEngine, AlertSeverity


class TestAlertEngine:

    @pytest.fixture
    def engine(self):
        slack = AsyncMock()
        pd = AsyncMock()
        email = AsyncMock()
        return AlertEngine(
            db_pool=None, slack_client=slack,
            pagerduty_client=pd, email_client=email,
        )

    @pytest.mark.asyncio
    async def test_ar001_fires_on_critical_score(self, engine):
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.SR01,
            probability=1.0, impact_eur=Decimal("10_000_000"),
            velocity_days=0, detectability=0.0,
        )
        rf.score = 95.0  # set pre-computed score
        alerts = await engine.process_risks([rf])
        ar001 = next((a for a in alerts if a.rule_id == "AR-001"), None)
        assert ar001 is not None
        assert ar001.severity == AlertSeverity.CRITICAL

    @pytest.mark.asyncio
    async def test_cooldown_blocks_repeat_fire(self, engine):
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.SR01,
            probability=1.0, impact_eur=Decimal("10_000_000"),
            velocity_days=0, detectability=0.0,
        )
        rf.score = 95.0
        alerts1 = await engine.process_risks([rf])
        alerts2 = await engine.process_risks([rf])
        ar001_count = sum(1 for a in alerts2 if a.rule_id == "AR-001")
        assert ar001_count == 0, "Cooldown should block second fire"

    @pytest.mark.asyncio
    async def test_dispatch_errors_do_not_raise(self, engine):
        engine._slack.send.side_effect = Exception("Slack down")
        rf = RiskFactor(
            risk_id=uuid4(), category=RiskCategory.PR01,
            probability=1.0, impact_eur=Decimal("2_000_000"),
            velocity_days=0, detectability=0.0,
        )
        rf.score = 80.0
        # Should not raise despite Slack failure
        alerts = await engine.process_risks([rf])
        assert isinstance(alerts, list)

    @pytest.mark.asyncio
    async def test_portfolio_var_breach_alert(self, engine):
        alert = await engine.process_portfolio_breach(
            var_95_eur=7_500_000, threshold_eur=5_000_000
        )
        assert alert is not None
        assert alert.rule_id == "AR-002"
        assert "7,500,000" in alert.body

    @pytest.mark.asyncio
    async def test_no_alert_below_var_threshold(self, engine):
        alert = await engine.process_portfolio_breach(
            var_95_eur=3_000_000, threshold_eur=5_000_000
        )
        assert alert is None
```

### 11.5 Integration Tests

```python
import asyncpg
import pytest_asyncio
from testcontainers.postgres import PostgresContainer

@pytest_asyncio.fixture(scope="session")
async def pg():
    with PostgresContainer("postgres:16") as c:
        pool = await asyncpg.create_pool(c.get_connection_url())
        await _apply_schema(pool)
        yield pool
        await pool.close()


class TestCRAESQLSchema:

    @pytest.mark.asyncio
    async def test_generated_level_low(self, pg):
        async with pg.acquire() as conn:
            rf_id = await conn.fetchval("""
                INSERT INTO crae.risk_factors
                    (analysis_run_id, category, domain, score, probability,
                     impact_eur, velocity_days, detectability, description)
                VALUES ($1, 'SR01', 'SUPPLIER', 20.0, 0.10, 50000, 90, 0.5, 'test')
                RETURNING risk_factor_id
            """, uuid4())
            level = await conn.fetchval(
                "SELECT level FROM crae.risk_factors WHERE risk_factor_id = $1", rf_id
            )
        assert level == "LOW"

    @pytest.mark.asyncio
    async def test_generated_level_critical(self, pg):
        async with pg.acquire() as conn:
            rf_id = await conn.fetchval("""
                INSERT INTO crae.risk_factors
                    (analysis_run_id, category, domain, score, probability,
                     impact_eur, velocity_days, detectability, description)
                VALUES ($1, 'SR03', 'SUPPLIER', 82.5, 0.85, 3000000, 5, 0.1, 'test')
                RETURNING risk_factor_id
            """, uuid4())
            level = await conn.fetchval(
                "SELECT level FROM crae.risk_factors WHERE risk_factor_id = $1", rf_id
            )
        assert level == "CRITICAL"

    @pytest.mark.asyncio
    async def test_trigger_inserts_outbox_on_critical(self, pg):
        async with pg.acquire() as conn:
            await conn.execute("""
                INSERT INTO crae.risk_factors
                    (analysis_run_id, category, domain, score, probability,
                     impact_eur, velocity_days, detectability, description)
                VALUES ($1, 'MR01', 'MARKET', 80.0, 0.80, 2000000, 10, 0.2, 'trigger test')
            """, uuid4())
            count = await conn.fetchval(
                "SELECT COUNT(*) FROM crae.outbox_events WHERE topic = 'crae.risk.critical'"
            )
        assert count >= 1

    @pytest.mark.asyncio
    async def test_portfolio_fk_references_run(self, pg):
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            async with pg.acquire() as conn:
                await conn.execute("""
                    INSERT INTO crae.portfolio_snapshots
                        (analysis_run_id, composite_score, var_95_eur, cvar_95_eur,
                         expected_loss_eur, risk_count, critical_count, high_count)
                    VALUES ($1, 55.0, 1000000, 1200000, 300000, 5, 1, 2)
                """, uuid4())   # non-existent run
```

### 11.6 k6 Load Test

```javascript
// k6 load test: crae_load.js
import http from "k6/http";
import { check, sleep } from "k6";
import { Trend, Rate } from "k6/metrics";

const riskDuration = new Trend("risk_list_duration_ms");
const portfolioAnalyzeDuration = new Trend("portfolio_analyze_duration_ms");
const errorRate = new Rate("error_rate");

export const options = {
  stages: [
    { duration: "2m", target: 20 },
    { duration: "5m", target: 50 },
    { duration: "2m", target: 0 },
  ],
  thresholds: {
    "risk_list_duration_ms": ["p(95)<400"],
    "portfolio_analyze_duration_ms": ["p(95)<5000"],
    "error_rate": ["rate<0.01"],
    "http_req_failed": ["rate<0.01"],
  },
};

const BASE_URL = __ENV.CRAE_BASE_URL || "http://localhost:8000";
const TOKEN = __ENV.CRAE_TOKEN;

export default function () {
  const headers = {
    Authorization: `Bearer ${TOKEN}`,
    "Content-Type": "application/json",
  };

  // GET /risks
  const risksRes = http.get(`${BASE_URL}/crae/v1/risks?limit=50`, { headers });
  check(risksRes, { "risks 200": (r) => r.status === 200 });
  riskDuration.add(risksRes.timings.duration);
  errorRate.add(risksRes.status !== 200);

  sleep(1);

  // POST /portfolio/analyze
  const payload = JSON.stringify({
    scope: { domains: ["SUPPLIER", "MARKET"] },
    include_monte_carlo: true,
    n_simulations: 1000,
  });
  const portfolioRes = http.post(`${BASE_URL}/crae/v1/portfolio/analyze`, payload, { headers });
  check(portfolioRes, { "portfolio 200": (r) => r.status === 200 });
  portfolioAnalyzeDuration.add(portfolioRes.timings.duration);
  errorRate.add(portfolioRes.status !== 200);

  sleep(2);
}
```

---

## 12. Ryzyka projektu

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|--------------------|-------|-----------|
| R01 | Dane finansowe dostawcy niedostępne (Altman Z) | WYSOKI | WYSOKI | Fallback na zewnętrzne credit scoring API; manual upload CSV |
| R02 | Monte Carlo VaR wolne przy >100 ryzyk | ŚREDNI | ŚREDNI | NumPy vectorized; precompute nightly; cache 15 min |
| R03 | Fałszywe alarmy przy volatile rynkach | WYSOKI | ŚREDNI | Cooldown; adaptacyjne progi (percentyl 90d rolling) |
| R04 | Brak danych OEE z MES | WYSOKI | WYSOKI | Mock fallback; integration roadmap; manual KPI entry API |
| R05 | Ryzyko koncentracji dostawcy niedoszacowane | ŚREDNI | WYSOKI | BOM coverage check; quarterly supplier audit integration |
| R06 | Drift modeli PFE wpływa na MarketRisk | ŚREDNI | WYSOKI | CRAE subskrybuje `pfe.drift.detected`; recalculate MR01-MR02 |
| R07 | Geopolitical risk tier przestarzały | NISKI | WYSOKI | Quarterly review; webhook z external geopolitical API |
| R08 | Kafka outbox lag przy masowych ryzykach | NISKI | ŚREDNI | BATCH_SIZE=50; monitoring lag alert >60s |
| R09 | PostgreSQL GENERATED column migracja | NISKI | NISKI | Alembic migration; testy schema przed deploy |
| R10 | Alert fatigue (zbyt wiele alarmów) | WYSOKI | ŚREDNI | Deduplication; digest mode; priority queue |
| R11 | Brak walidacji stress-test inputs | NISKI | ŚREDNI | Pydantic V2 schema; max shock = 3.0× |
| R12 | VaR model assumes independence | WYSOKI | WYSOKI | Copula model (Faza 4); correlation matrix input |
| R13 | Certyfikaty dostawców ręcznie zarządzane | WYSOKI | ŚREDNI | Automated cert scraper; supplier portal integration |
| R14 | RBAC granularity niewystarczalna | NISKI | NISKI | Domain-level RBAC (SUPPLIER_RISK_MANAGER) w Fazie 3 |
| R15 | SLO breach przy równoczesnych analizach | NISKI | WYSOKI | Queue-based orchestration; HPA worker scaling |

---

## 13. Roadmap

### Faza 1 — Foundation (Sprinty S1–S8)

| Sprint | Cel | Kryteria sukcesu |
|--------|-----|-----------------|
| S1 | Risk taxonomy + modele danych | `RiskFactor`, `RiskPortfolio` dataclasses; 24 kategorie RiskCategory |
| S2 | SQL Schema `crae` | 5 tabel, GENERATED `level`, triggery, migracja Alembic |
| S3 | RiskScorer FMEA | Testy T01; score range [0,100]; weights sum=1 |
| S4 | SupplierRiskAnalyzer SR01–SR06 | Testy T03; Altman Z 3 strefy; geo tier |
| S5 | API CRUD (GET/POST /risks, /analyses) | OpenAPI 3.1; JWT RS256; RBAC viewer/analyst |
| S6 | Kafka Outbox + 4 podstawowe topics | `crae.risk.critical`, `crae.analysis.completed`; CRAEOutboxPublisher |
| S7 | Prometheus 15 metryk + Grafana 3 dashboardy | `crae_risk_score` histogram; risk_by_domain panel |
| S8 | CI gate: testy T01–T03 + schema | pytest ≥ 95% pass; migration test w testcontainers |

### Faza 2 — Market & Production Risk (Sprinty S9–S18)

| Sprint | Cel | Kryteria sukcesu |
|--------|-----|-----------------|
| S9 | MarketRiskAnalyzer MR01–MR06 | Testy T04; integracja z PFE `/forecasts` |
| S10 | ProductionRiskAnalyzer PR01–PR06 | Testy T05; MES mock connector |
| S11 | MaterialRiskAnalyzer MT01–MT06 | Testy T06; BOM coverage check |
| S12 | Monte Carlo VaR (10k sim) | Test T11 convergence <5%; VaR95 + CVaR95 |
| S13 | RiskAnalysisOrchestrator (asyncio.gather) | Full pipeline integration test T09 |
| S14 | AlertEngine AR-001–AR-005 | Testy T07; cooldown; PagerDuty + Slack |
| S15 | Stress-test endpoint `/scenarios/stress-test` | Shock types: COMMODITY/FX/OEE; response <5s |
| S16 | Portfolio heatmap API + widok SQL | `v_risk_dashboard`; GET /portfolio/heatmap 200ms |
| S17 | Dodatkowe Kafka topics (10 razem) | `crae.mitigation.overdue`, `crae.portfolio.updated` |
| S18 | k6 load test (T12) + SLO gates | p95 GET <400ms; p95 analyze <5s |

### Faza 3 — Intelligence & Scale (Sprinty S19–S28)

| Sprint | Cel | Kryteria sukcesu |
|--------|-----|-----------------|
| S19 | Integracja PFE drift → auto-recalc MarketRisk | Subskrypcja `pfe.drift.detected`; MR01-02 recalc <30s |
| S20 | Supplier financial API (Dun & Bradstreet) | Altman Z auto-fetch; fallback manual CSV |
| S21 | Domain-level RBAC (Faza 3 granularity) | SUPPLIER_RISK_MANAGER, MARKET_RISK_MANAGER roles |
| S22 | AlertEngine AR-006–AR-010 + Teams | OEE, stock, cert, portfolio surge, geo tier-5 |
| S23 | Risk trend analytics (30/60/90d) | `v_portfolio_trend`; GET /analytics/trend 200ms |
| S24 | VaR confidence interval (bootstrap) | 95% CI na VaR95 estimate; bootstrap 1000× |
| S25 | TimescaleDB hypertable dla `risk_factors` | Compress policy >90d; query speedup 5× |
| S26 | HPA CRAE API 2–8 pods | k6 200 VU test; SLO maintained |
| S27 | Mitigation tracking workflow | POST /risks/{id}/mitigations; status flow |
| S28 | Multi-site risk (DE/PL/CN/MX) | Location-aware PR01-PR06; site comparison API |

### Faza 4 — Advanced Analytics (Sprinty S29–S32)

| Sprint | Cel | Kryteria sukcesu |
|--------|-----|-----------------|
| S29 | Copula model (Gaussian) dla zależności ryzyk | R12 mitygacja; correlation matrix input; VaR delta <15% vs independence |
| S30 | ML risk prediction (XGBoost classifier) | Train na historycznych ryzykach; precision >0.75 dla CRITICAL |
| S31 | Real-time risk streaming (Kafka Streams) | Price tick → MR01 update <5s lag |
| S32 | External threat intelligence feed | Geopolitical tier auto-update; news NLP risk signals |

### KPIs dojrzałości systemu

| Miara | Faza 1 | Faza 2 | Faza 3 | Faza 4 |
|-------|--------|--------|--------|--------|
| Risk categories covered | SR01-SR06 | +MR+PR+MT (24) | 24 + external | 24 + ML |
| VaR method | — | Monte Carlo independent | Bootstrap CI | Copula |
| Alert channels | Email + Slack | +PD + Teams | All 5 | All 5 + AI triage |
| API p95 latency | <1s | <500ms | <400ms | <400ms |
| Monte Carlo simulations | — | 10k | 10k + bootstrap | 100k streaming |
| Commodities monitored | — | LME / EEX 5 | 9 commodities | Real-time tick |
