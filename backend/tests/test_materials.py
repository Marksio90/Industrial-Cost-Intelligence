from __future__ import annotations
from decimal import Decimal
import pytest
from src.modules.materials.domain.models import Material, MaterialClass, MaterialNumber, MaterialStatus, UnitOfMeasure


class TestMaterialNumber:
    def test_valid(self):
        mn = MaterialNumber("MAT-001")
        assert mn.value == "MAT-001"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            MaterialNumber("")

    def test_invalid_chars_raises(self):
        with pytest.raises(ValueError):
            MaterialNumber("MAT 001")

    def test_equality(self):
        assert MaterialNumber("ABC") == MaterialNumber("ABC")
        assert MaterialNumber("ABC") != MaterialNumber("DEF")


class TestMaterialDomain:
    def _make(self) -> Material:
        return Material.create(
            material_number="MAT-001",
            name="Steel Sheet",
            description="Cold rolled",
            material_class=MaterialClass.STEEL,
            unit_of_measure=UnitOfMeasure.KG,
            base_price_eur=Decimal("5.50"),
            tenant_id="t1",
            lead_time_days=14,
            min_order_qty=Decimal("100"),
        )

    def test_create_fires_event(self):
        m = self._make()
        events = m.pop_events()
        assert len(events) == 1
        assert events[0].__class__.__name__ == "MaterialCreated"
        assert events[0].material_number == "MAT-001"

    def test_initial_status_active(self):
        m = self._make()
        assert m.status == MaterialStatus.ACTIVE

    def test_update_price_fires_event(self):
        m = self._make()
        m.pop_events()
        m.update_price(Decimal("6.00"))
        events = m.pop_events()
        assert len(events) == 1
        assert events[0].__class__.__name__ == "MaterialPriceUpdated"
        assert events[0].new_price_eur == Decimal("6.00")
        assert m.base_price_eur == Decimal("6.00")

    def test_deprecate(self):
        m = self._make()
        m.deprecate("end of life")
        assert m.status == MaterialStatus.DEPRECATED

    def test_deprecate_fires_event(self):
        m = self._make()
        m.pop_events()
        m.deprecate("obsolete")
        events = m.pop_events()
        assert any(e.__class__.__name__ == "MaterialDeprecated" for e in events)

    def test_reactivate(self):
        m = self._make()
        m.deprecate("test")
        m.reactivate()
        assert m.status == MaterialStatus.ACTIVE

    def test_calculate_cost(self):
        m = self._make()
        cost = m.calculate_cost(quantity=Decimal("100"), weight_kg=Decimal("2"))
        # For KG unit of measure, cost is price per kg * weight_kg
        assert cost == Decimal("5.50") * Decimal("2")

    def test_pop_events_clears(self):
        m = self._make()
        m.pop_events()
        assert m.pop_events() == []


class TestMaterialAPI:
    @pytest.mark.asyncio
    async def test_create_material(self, client):
        resp = await client.post("/api/v1/materials", json={
            "material_number": "MAT-API-001",
            "name": "Test Material",
            "material_class": "STEEL",
            "unit_of_measure": "KG",
            "base_price_eur": "10.00",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["material_number"] == "MAT-API-001"
        assert data["status"] == "ACTIVE"

    @pytest.mark.asyncio
    async def test_create_duplicate_raises_422(self, client):
        payload = {"material_number": "MAT-DUP-001", "name": "Dup", "material_class": "STEEL", "unit_of_measure": "KG", "base_price_eur": "1.00"}
        r1 = await client.post("/api/v1/materials", json=payload)
        assert r1.status_code == 201
        r2 = await client.post("/api/v1/materials", json=payload)
        assert r2.status_code == 422
        assert r2.json()["error"] == "BUSINESS_RULE_VIOLATION"

    @pytest.mark.asyncio
    async def test_list_materials(self, client):
        resp = await client.get("/api/v1/materials")
        assert resp.status_code == 200
        data = resp.json()
        assert "items" in data
        assert "total" in data

    @pytest.mark.asyncio
    async def test_get_material_not_found(self, client):
        import uuid
        resp = await client.get(f"/api/v1/materials/{uuid.uuid4()}")
        assert resp.status_code == 404
