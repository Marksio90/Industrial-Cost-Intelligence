from __future__ import annotations
from decimal import Decimal
import pytest
from src.modules.risk.domain.models import RiskCategory, RiskItem, RiskScore, RiskSeverity, RiskStatus


class TestRiskScore:
    def test_rpn_calculation(self):
        score = RiskScore(Decimal("0.8"), Decimal("0.9"), Decimal("0.2"))
        expected = (Decimal("0.8") * Decimal("0.9") * Decimal("0.8")).quantize(Decimal("0.0001"))
        assert score.rpn == expected

    def test_severity_critical(self):
        score = RiskScore(Decimal("0.9"), Decimal("0.9"), Decimal("0.1"))
        assert score.severity == RiskSeverity.CRITICAL

    def test_severity_low(self):
        score = RiskScore(Decimal("0.1"), Decimal("0.1"), Decimal("0.5"))
        assert score.severity == RiskSeverity.LOW

    def test_invalid_probability_raises(self):
        with pytest.raises(ValueError):
            RiskScore(Decimal("1.5"), Decimal("0.5"), Decimal("0.5"))


class TestRiskItem:
    def _make(self) -> RiskItem:
        return RiskItem.create(
            tenant_id="t1",
            category=RiskCategory.SUPPLY,
            title="Supplier delay risk",
            description="Single source dependency",
            probability=Decimal("0.6"),
            impact=Decimal("0.8"),
            detectability=Decimal("0.3"),
        )

    def test_create_fires_event(self):
        r = self._make()
        events = r.pop_events()
        assert len(events) == 1
        assert events[0].__class__.__name__ == "RiskIdentified"

    def test_initial_status_open(self):
        r = self._make()
        assert r.status == RiskStatus.OPEN

    def test_acknowledge(self):
        r = self._make()
        r.acknowledge()
        assert r.status == RiskStatus.ACKNOWLEDGED

    def test_acknowledge_already_acknowledged_raises(self):
        r = self._make()
        r.acknowledge()
        with pytest.raises(ValueError):
            r.acknowledge()

    def test_resolve_fires_event(self):
        r = self._make()
        r.pop_events()
        r.resolve()
        events = r.pop_events()
        assert any(e.__class__.__name__ == "RiskResolved" for e in events)
        assert r.status == RiskStatus.RESOLVED

    def test_add_mitigation(self):
        r = self._make()
        action = r.add_mitigation("Qualify alternative supplier", "procurement@example.com")
        assert len(r.mitigation_actions) == 1
        assert action.is_completed is False

    def test_complete_mitigation(self):
        r = self._make()
        action = r.add_mitigation("Dual source", "buyer@example.com")
        action.complete()
        assert action.is_completed is True

    def test_update_score(self):
        r = self._make()
        r.update_score(Decimal("0.3"), Decimal("0.5"), Decimal("0.5"))
        assert r.score.probability == Decimal("0.3")


class TestRiskAPI:
    @pytest.mark.asyncio
    async def test_create_risk(self, client):
        resp = await client.post("/api/v1/risk", json={
            "category": "SUPPLY",
            "title": "Test supply risk",
            "probability": "0.5",
            "impact": "0.7",
            "detectability": "0.4",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "OPEN"
        assert "rpn" in data["score"]

    @pytest.mark.asyncio
    async def test_portfolio(self, client):
        resp = await client.get("/api/v1/risk/portfolio")
        assert resp.status_code == 200
        data = resp.json()
        assert "open_count" in data
        assert "total_financial_exposure_eur" in data
