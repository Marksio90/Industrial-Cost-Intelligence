# BOM Engine — Sekcje 14–17: Testing, Scalability, Risks, Roadmap

---

## 14. Testing

### 14.1 Macierz testów

| Typ | Narzędzie | Zakres | Pokrycie |
|-----|-----------|--------|----------|
| Unit | pytest | BOMTreeService, CostRollupService, BOMValidator, VariantConfigurator, MaterialSubstitutionEngine | 90%+ |
| Integration | pytest + Testcontainers | DB schema, triggers, stored functions, outbox relay | 80%+ |
| API Contract | schemathesis | OpenAPI spec vs implementation, wszystkie 26 endpointów | 100% |
| Import | pytest + mock PLM | Teamcenter/Windchill/CSV parsery, BOMImporter pipeline | 85%+ |
| Change Management | pytest | Approval chain, status transitions, impact assessment | 85%+ |
| BOM Diff | pytest | Version comparison, all diff scenarios | 90%+ |
| Validation | pytest | Wszystkie 16 reguł walidacji (V001-V016) | 100% |
| Load | k6 | Tree build latency, rollup throughput, where-used query | steady state |

### 14.2 Testy jednostkowe

```python
# tests/unit/test_bom_tree.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from bome.tree import BOMTreeService, BOMNode, BOMNotFoundError
from bome.domain import BOMLine, BOMLineType, UnitOfMeasure


def make_line(
    line_id: str,
    item_code: str,
    parent_id=None,
    position: int = 10,
    qty: Decimal = Decimal("1"),
    phantom: bool = False,
    line_type: BOMLineType = BOMLineType.COMPONENT,
) -> BOMLine:
    return BOMLine(
        line_id=line_id,
        bom_id="bom-1",
        parent_line_id=parent_id,
        position=position,
        item_code=item_code,
        quantity=qty,
        uom=UnitOfMeasure.PC,
        line_type=line_type,
        phantom=phantom,
    )


class TestBOMNode:
    def test_is_leaf_when_no_children(self):
        node = BOMNode(line=make_line("l1", "ITEM-A"), depth=0, path="10")
        assert node.is_leaf is True

    def test_is_not_leaf_with_children(self):
        parent = BOMNode(line=make_line("l1", "ASSY-A"), depth=0, path="10")
        child = BOMNode(line=make_line("l2", "ITEM-B", parent_id="l1"), depth=1, path="10.10")
        parent.children = [child]
        assert parent.is_leaf is False

    def test_iter_all_yields_all_nodes(self):
        root = BOMNode(line=make_line("l1", "ASSY"), depth=0, path="10")
        c1 = BOMNode(line=make_line("l2", "A"), depth=1, path="10.10")
        c2 = BOMNode(line=make_line("l3", "B"), depth=1, path="10.20")
        gc = BOMNode(line=make_line("l4", "C"), depth=2, path="10.10.10")
        c1.children = [gc]
        root.children = [c1, c2]

        all_nodes = list(root.iter_all())
        assert len(all_nodes) == 4
        assert {n.line.item_code for n in all_nodes} == {"ASSY", "A", "B", "C"}

    def test_iter_leaves_returns_only_leaves(self):
        root = BOMNode(line=make_line("l1", "ASSY"), depth=0, path="10")
        c1 = BOMNode(line=make_line("l2", "A"), depth=1, path="10.10")
        c2 = BOMNode(line=make_line("l3", "B"), depth=1, path="10.20")
        root.children = [c1, c2]

        leaves = list(root.iter_leaves())
        assert len(leaves) == 2
        assert all(n.is_leaf for n in leaves)

    def test_effective_quantity_with_scrap(self):
        line = make_line("l1", "ITEM")
        line.scrap_factor_pct = Decimal("10")  # 10% scrap
        line.quantity = Decimal("1")
        # effective_qty = 1 / (1 - 0.10) = 1.111...
        assert line.effective_quantity == pytest.approx(Decimal("1") / Decimal("0.9"), rel=1e-6)

    def test_effective_quantity_zero_scrap(self):
        line = make_line("l1", "ITEM")
        line.scrap_factor_pct = Decimal("0")
        line.quantity = Decimal("5")
        assert line.effective_quantity == Decimal("5")


# tests/unit/test_cost_rollup.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from bome.cost_rollup import CostRollupService, OVERHEAD_RATES
from bome.domain import BOMLine, BOMLineType, UnitOfMeasure
from bome.tree import BOMNode


@pytest.fixture
def mock_rollup_service():
    tree_service = MagicMock()
    tree_service.db = MagicMock()
    cee_client = AsyncMock()
    price_service = AsyncMock()
    price_service.get_price = AsyncMock(return_value=None)
    price_service.prefetch = AsyncMock()
    return CostRollupService(tree_service, cee_client, price_service)


class TestCostRollup:
    def test_overhead_rate_germany(self):
        assert OVERHEAD_RATES["DE"] == Decimal("0.25")

    def test_overhead_rate_poland(self):
        assert OVERHEAD_RATES["PL"] == Decimal("0.18")

    @pytest.mark.asyncio
    async def test_unit_cost_with_price_master(self, mock_rollup_service):
        line = BOMLine(
            line_id="l1", bom_id="bom-1", item_code="STEEL-01",
            quantity=Decimal("2"), uom=UnitOfMeasure.KG,
            line_type=BOMLineType.RAW_MATERIAL,
        )
        mock_rollup_service.price_service.get_price = AsyncMock(return_value=Decimal("5.50"))
        cost, source = await mock_rollup_service._get_unit_cost(line, [])
        assert cost == Decimal("5.50")
        assert source == "PRICE_MASTER"

    @pytest.mark.asyncio
    async def test_unit_cost_with_override(self, mock_rollup_service):
        line = BOMLine(
            line_id="l1", bom_id="bom-1", item_code="COMP-X",
            quantity=Decimal("1"), uom=UnitOfMeasure.PC,
            line_type=BOMLineType.COMPONENT,
            cost_override_eur=Decimal("99.99"),
        )
        cost, source = await mock_rollup_service._get_unit_cost(line, [])
        assert cost == Decimal("99.99")
        assert source == "OVERRIDE"

    @pytest.mark.asyncio
    async def test_unit_cost_fallback_to_zero(self, mock_rollup_service):
        line = BOMLine(
            line_id="l1", bom_id="bom-1", item_code="UNKNOWN",
            quantity=Decimal("1"), uom=UnitOfMeasure.PC,
            line_type=BOMLineType.COMPONENT,
        )
        mock_rollup_service.price_service.get_price = AsyncMock(return_value=None)
        mock_rollup_service.cee.estimate_material_cost = AsyncMock(side_effect=Exception("CEE down"))
        warnings = []
        cost, source = await mock_rollup_service._get_unit_cost(line, warnings)
        assert cost == Decimal("0")
        assert source == "ZERO"
        assert any("UNKNOWN" in w for w in warnings)

    def test_confidence_all_price_master(self):
        from bome.cost_rollup import LineRollupResult
        results = [
            LineRollupResult(
                line_id="l1", item_code="A", line_type="COMPONENT",
                quantity=Decimal("1"), effective_quantity=Decimal("1"),
                unit_cost_eur=Decimal("10"), total_cost_eur=Decimal("10"),
                cost_source="PRICE_MASTER",
            )
        ] * 5
        conf = CostRollupService._compute_confidence(results)
        assert conf == pytest.approx(1.0 - (0 * 0.5 + 0 * 0.15) / 5)

    def test_confidence_penalized_for_zero(self):
        from bome.cost_rollup import LineRollupResult
        results = [
            LineRollupResult(
                line_id=f"l{i}", item_code=f"X{i}", line_type="COMPONENT",
                quantity=Decimal("1"), effective_quantity=Decimal("1"),
                unit_cost_eur=Decimal("0"), total_cost_eur=Decimal("0"),
                cost_source="ZERO",
            )
            for i in range(3)
        ] + [
            LineRollupResult(
                line_id="l4", item_code="Y", line_type="COMPONENT",
                quantity=Decimal("1"), effective_quantity=Decimal("1"),
                unit_cost_eur=Decimal("10"), total_cost_eur=Decimal("10"),
                cost_source="PRICE_MASTER",
            )
        ]
        conf = CostRollupService._compute_confidence(results)
        assert conf < 0.7  # 3/4 ZERO heavily penalized


# tests/unit/test_bom_validator.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from bome.validation import BOMValidator, ValidationIssue
from bome.domain import BOMLine, BOMLineType, UnitOfMeasure
from bome.tree import BOMNode


def make_valid_line(line_id="l1", item_code="ITEM-A", qty=Decimal("1")):
    return BOMLine(
        line_id=line_id, bom_id="bom-1", item_code=item_code,
        quantity=qty, uom=UnitOfMeasure.PC,
        line_type=BOMLineType.COMPONENT,
    )


class TestBOMValidator:
    def setup_method(self):
        self.validator = BOMValidator()

    def test_valid_line_no_issues(self):
        line = make_valid_line()
        issues = self.validator._validate_line(line)
        assert len(issues) == 0

    def test_zero_quantity_error(self):
        line = make_valid_line(qty=Decimal("0"))
        issues = self.validator._validate_line(line)
        errors = [i for i in issues if i.code == "BOM_V004"]
        assert len(errors) == 1
        assert errors[0].severity == "ERROR"

    def test_negative_quantity_error(self):
        line = make_valid_line(qty=Decimal("-1"))
        issues = self.validator._validate_line(line)
        assert any(i.code == "BOM_V004" for i in issues)

    def test_scrap_at_limit_error(self):
        line = make_valid_line()
        line.scrap_factor_pct = Decimal("50")
        issues = self.validator._validate_line(line)
        assert any(i.code == "BOM_V005" for i in issues)

    def test_high_scrap_warning(self):
        line = make_valid_line()
        line.scrap_factor_pct = Decimal("25")
        issues = self.validator._validate_line(line)
        assert any(i.code == "BOM_V006" and i.severity == "WARNING" for i in issues)

    def test_empty_item_code_error(self):
        line = make_valid_line(item_code="")
        issues = self.validator._validate_line(line)
        assert any(i.code == "BOM_V007" for i in issues)

    def test_duplicate_positions_detected(self):
        root1 = BOMNode(line=make_valid_line("l1", "A"), depth=0, path="10")
        root2 = BOMNode(line=make_valid_line("l2", "B"), depth=0, path="10")
        root1.line.position = 10
        root2.line.position = 10  # same position as root1
        dups = self.validator._check_duplicate_positions([root1, root2])
        assert len(dups) > 0

    def test_no_duplicate_positions_different(self):
        root1 = BOMNode(line=make_valid_line("l1", "A"), depth=0, path="10")
        root2 = BOMNode(line=make_valid_line("l2", "B"), depth=0, path="20")
        root1.line.position = 10
        root2.line.position = 20
        dups = self.validator._check_duplicate_positions([root1, root2])
        assert len(dups) == 0


# tests/unit/test_material_substitution.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from bome.substitution import (
    MaterialSubstitutionEngine, SubstitutionRequest, SubstitutionReason, ApprovalStatus
)


@pytest.fixture
def mock_engine():
    db_pool = MagicMock()
    cee_client = AsyncMock()
    sie_client = AsyncMock()
    engine = MaterialSubstitutionEngine(db_pool, cee_client, sie_client)
    return engine


class TestMaterialSubstitution:
    @pytest.mark.asyncio
    async def test_evaluate_unavailable_substitute(self, mock_engine):
        mock_engine._check_item_availability = AsyncMock(return_value={"available": False})
        mock_engine._get_item_cost = AsyncMock(return_value=Decimal("100"))
        mock_engine._check_regulatory = AsyncMock(return_value={"rohs": True, "reach": True})
        mock_engine._get_preferred_supplier_score = AsyncMock(return_value=None)

        request = SubstitutionRequest(
            original_item_code="MAT-A",
            substitute_item_code="MAT-B",
            reason=SubstitutionReason.COST_REDUCTION,
        )
        result = await mock_engine.evaluate_substitution(request)
        assert not result.can_substitute
        assert any("not in material master" in b for b in result.blocking_issues)

    @pytest.mark.asyncio
    async def test_evaluate_non_rohs_blocks(self, mock_engine):
        mock_engine._check_item_availability = AsyncMock(return_value={"available": True})
        mock_engine._get_item_cost = AsyncMock(return_value=Decimal("100"))
        mock_engine._check_regulatory = AsyncMock(return_value={"rohs": False, "reach": True})
        mock_engine._get_preferred_supplier_score = AsyncMock(return_value=None)

        request = SubstitutionRequest(
            original_item_code="MAT-A",
            substitute_item_code="MAT-B",
            reason=SubstitutionReason.COST_REDUCTION,
        )
        result = await mock_engine.evaluate_substitution(request)
        assert not result.can_substitute
        assert any("RoHS" in b for b in result.blocking_issues)

    @pytest.mark.asyncio
    async def test_apply_without_approval_raises(self, mock_engine):
        request = SubstitutionRequest(
            request_id="req-1",
            original_item_code="MAT-A",
            substitute_item_code="MAT-B",
            reason=SubstitutionReason.COST_REDUCTION,
            approval_status=ApprovalStatus.PENDING,
        )
        with pytest.raises(PermissionError, match="not yet approved"):
            await mock_engine.apply_substitution(request, "user@company.com")

    @pytest.mark.asyncio
    async def test_cost_delta_computed(self, mock_engine):
        mock_engine._check_item_availability = AsyncMock(return_value={"available": True})
        mock_engine._get_item_cost = AsyncMock(side_effect=[
            Decimal("100.00"),  # original
            Decimal("85.00"),   # substitute
        ])
        mock_engine._check_regulatory = AsyncMock(return_value={"rohs": True, "reach": True})
        mock_engine._get_preferred_supplier_score = AsyncMock(return_value=None)

        request = SubstitutionRequest(
            original_item_code="MAT-A",
            substitute_item_code="MAT-B",
            reason=SubstitutionReason.COST_REDUCTION,
        )
        result = await mock_engine.evaluate_substitution(request)
        assert result.cost_delta_eur == Decimal("-15.00")
        assert result.cost_delta_pct == Decimal("-15")


# tests/unit/test_variant_configurator.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from bome.variants import VariantConfigurator, VariantRule, ProductFamily
from bome.domain import BOMLine, BOMLineType, UnitOfMeasure
from bome.tree import BOMNode


def make_node(line_id, item_code, qty=Decimal("1")):
    line = BOMLine(
        line_id=line_id, bom_id="bom-gen", item_code=item_code,
        quantity=qty, uom=UnitOfMeasure.PC, line_type=BOMLineType.COMPONENT,
    )
    return BOMNode(line=line, depth=0, path=str(line_id))


class TestVariantConfigurator:
    def setup_method(self):
        self.tree_service = MagicMock()
        self.configurator = VariantConfigurator(self.tree_service)

    def test_apply_rule_excludes_item(self):
        nodes = [
            make_node("l1", "ITEM-BLACK"),
            make_node("l2", "ITEM-WHITE"),
        ]
        nodes[0].line.line_id = "l1"
        nodes[1].line.line_id = "l2"

        rules = [
            VariantRule(line_id="l1", condition_expr="COLOR == 'BLACK'"),
            VariantRule(line_id="l2", condition_expr="COLOR == 'WHITE'"),
        ]
        result = self.configurator._apply_rules(nodes, rules, {"COLOR": "BLACK"})
        assert len(result) == 1
        assert result[0].line.item_code == "ITEM-BLACK"

    def test_apply_rule_adjusts_quantity(self):
        nodes = [make_node("l1", "SHEET")]
        nodes[0].line.line_id = "l1"
        rules = [
            VariantRule(
                line_id="l1",
                condition_expr="True",
                quantity_expr="1.5 if SIZE == 'L' else 1.0",
            )
        ]
        result = self.configurator._apply_rules(nodes, rules, {"SIZE": "L"})
        assert result[0].line.quantity == Decimal("1.5")

    def test_apply_rule_item_override(self):
        nodes = [make_node("l1", "GASKET-STD")]
        nodes[0].line.line_id = "l1"
        rules = [
            VariantRule(
                line_id="l1",
                condition_expr="GRADE == 'PREMIUM'",
                item_override="GASKET-PREMIUM",
            )
        ]
        result = self.configurator._apply_rules(nodes, rules, {"GRADE": "PREMIUM"})
        assert result[0].line.item_code == "GASKET-PREMIUM"

    def test_config_hash_deterministic(self):
        opts1 = {"SIZE": "L", "COLOR": "BLACK"}
        opts2 = {"COLOR": "BLACK", "SIZE": "L"}  # different order
        assert self.configurator.get_config_hash(opts1) == self.configurator.get_config_hash(opts2)
```

### 14.3 Testy integracyjne

```python
# tests/integration/test_bom_db.py
import pytest
import asyncpg
from testcontainers.postgres import PostgresContainer
from decimal import Decimal
from datetime import date


@pytest.fixture(scope="module")
async def pg():
    with PostgresContainer("postgres:16") as pg_container:
        pool = await asyncpg.create_pool(pg_container.get_connection_url())
        async with pool.acquire() as conn:
            with open("migrations/bome_schema.sql") as f:
                await conn.execute(f.read())
        yield pool
        await pool.close()


@pytest.mark.asyncio
async def test_bom_header_uq_constraint(pg):
    """Duplikat product_code+revision+type powinien zgłosić błąd."""
    async with pg.acquire() as conn:
        # Wstaw materiał
        await conn.execute(
            "INSERT INTO bome.material_master (item_code, description, material_group) "
            "VALUES ('TEST-001','Test item','MISC') ON CONFLICT DO NOTHING"
        )
        # Wstaw BOM
        await conn.execute(
            "INSERT INTO bome.bom_headers (product_code, revision, bom_type, status) "
            "VALUES ('PROD-X','A','ENGINEERING','DRAFT')"
        )
        # Drugi BOM z tymi samymi kluczami powinien rzucić wyjątek
        with pytest.raises(asyncpg.UniqueViolationError):
            await conn.execute(
                "INSERT INTO bome.bom_headers (product_code, revision, bom_type, status) "
                "VALUES ('PROD-X','A','ENGINEERING','DRAFT')"
            )


@pytest.mark.asyncio
async def test_bom_line_trigger_blocks_released_bom_edit(pg):
    """Edycja linii RELEASED BOM bez change_order_id powinna być zablokowana."""
    async with pg.acquire() as conn:
        bom_id = await conn.fetchval(
            "INSERT INTO bome.bom_headers (product_code, revision, bom_type, status, released_by) "
            "VALUES ('PROD-Y','A','MANUFACTURING','RELEASED','engineer') RETURNING bom_id"
        )
        await conn.execute(
            "INSERT INTO bome.material_master (item_code, description, material_group) "
            "VALUES ('COMP-001','Component','COMP') ON CONFLICT DO NOTHING"
        )
        with pytest.raises(asyncpg.RaiseError, match="change_order_id"):
            await conn.execute(
                "INSERT INTO bome.bom_lines (bom_id, item_code, quantity, uom, line_type) "
                "VALUES ($1,'COMP-001',1,'PC','COMPONENT')",
                bom_id,
            )


@pytest.mark.asyncio
async def test_snapshot_trigger_on_status_change(pg):
    """Status change powinien auto-tworzyć snapshot w bom_versions."""
    async with pg.acquire() as conn:
        bom_id = await conn.fetchval(
            "INSERT INTO bome.bom_headers (product_code, revision, bom_type, status, released_by) "
            "VALUES ('PROD-Z','B','ENGINEERING','DRAFT','test') RETURNING bom_id"
        )
        # Zmień status DRAFT → IN_REVIEW
        await conn.execute(
            "UPDATE bome.bom_headers SET status='IN_REVIEW' WHERE bom_id=$1", bom_id
        )
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM bome.bom_versions WHERE bom_id=$1", bom_id
        )
        assert count == 1


@pytest.mark.asyncio
async def test_recursive_bom_query(pg):
    """Rekurencyjne CTE powinno zwracać strukturę 3-poziomową."""
    async with pg.acquire() as conn:
        # Utwórz materiały
        for code in ["ASSY-1", "SUB-1", "LEAF-1"]:
            await conn.execute(
                "INSERT INTO bome.material_master (item_code, description, material_group) "
                "VALUES ($1,$2,'TEST') ON CONFLICT DO NOTHING", code, f"Desc {code}"
            )

        bom_id = await conn.fetchval(
            "INSERT INTO bome.bom_headers (product_code, revision, bom_type, status) "
            "VALUES ('ASSY-1','A','ENGINEERING','DRAFT') RETURNING bom_id"
        )
        # Level 1
        l1 = await conn.fetchval(
            "INSERT INTO bome.bom_lines (bom_id, item_code, quantity, uom, line_type) "
            "VALUES ($1,'SUB-1',1,'PC','SUBASSEMBLY') RETURNING line_id", bom_id
        )
        # Level 2
        await conn.execute(
            "INSERT INTO bome.bom_lines (bom_id, parent_line_id, item_code, quantity, uom, line_type) "
            "VALUES ($1,$2,'LEAF-1',2,'PC','COMPONENT')", bom_id, l1
        )

        rows = await conn.fetch(
            """
            WITH RECURSIVE bom_tree AS (
                SELECT line_id, item_code, parent_line_id, 0 AS depth
                FROM bome.bom_lines WHERE bom_id=$1 AND parent_line_id IS NULL
                UNION ALL
                SELECT c.line_id, c.item_code, c.parent_line_id, p.depth+1
                FROM bome.bom_lines c JOIN bom_tree p ON p.line_id=c.parent_line_id
            )
            SELECT * FROM bom_tree ORDER BY depth
            """,
            bom_id
        )
        assert len(rows) == 2
        assert rows[1]["depth"] == 1
```

### 14.4 Testy Change Management

```python
# tests/unit/test_change_management.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bome.changes import ChangeOrderService, APPROVAL_CHAINS


class TestChangeOrderService:
    def setup_method(self):
        self.db = MagicMock()
        self.notify = AsyncMock()
        self.kafka = AsyncMock()
        self.service = ChangeOrderService(self.db, self.notify, self.kafka)

    def test_approval_chain_eco_includes_quality(self):
        chain = APPROVAL_CHAINS["ECO"]
        assert "quality_manager" in chain

    def test_approval_chain_dco_minimal(self):
        chain = APPROVAL_CHAINS["DCO"]
        assert chain == ["engineering_lead"]

    @pytest.mark.asyncio
    async def test_numbering_format(self):
        self.db.acquire = MagicMock()
        conn = AsyncMock()
        conn.fetchval = AsyncMock(return_value=5)  # 5 existing ECOs this year
        self.db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        self.db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        from datetime import datetime
        year = datetime.now().year
        number = await self.service._next_change_number("ECO")
        assert number == f"ECO-{year}-0006"

    @pytest.mark.asyncio
    async def test_submit_nonexistent_change_raises(self):
        conn = AsyncMock()
        conn.fetchrow = AsyncMock(return_value=None)
        self.db.acquire = MagicMock()
        self.db.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        self.db.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        with pytest.raises(ValueError):
            await self.service.submit_for_review("non-existent-id", "user")
```

### 14.5 Load Test (k6)

```javascript
// tests/load/bome_load.js
import http from "k6/http";
import { check, group, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

const errorRate = new Rate("error_rate");
const treeLatency = new Trend("tree_build_latency_ms");
const rollupLatency = new Trend("rollup_latency_ms");
const whereUsedLatency = new Trend("where_used_latency_ms");

export const options = {
  stages: [
    { duration: "2m", target: 20 },
    { duration: "5m", target: 50 },
    { duration: "2m", target: 100 },
    { duration: "1m", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<500", "p(99)<1000"],
    error_rate: ["rate<0.01"],
    tree_build_latency_ms: ["p(95)<300"],
    rollup_latency_ms: ["p(95)<2000"],
    where_used_latency_ms: ["p(95)<200"],
  },
};

const BASE_URL = __ENV.BOME_URL || "http://localhost:8080";
const TOKEN = __ENV.BOME_TOKEN || "";
const HEADERS = {
  "Content-Type": "application/json",
  Authorization: `Bearer ${TOKEN}`,
};

// Pre-loaded BOM IDs for testing
const BOM_IDS = JSON.parse(__ENV.BOM_IDS || '["bom-1","bom-2","bom-3"]');
const ITEM_CODES = JSON.parse(__ENV.ITEM_CODES || '["COMP-001","COMP-002"]');

export default function () {
  const bomId = BOM_IDS[Math.floor(Math.random() * BOM_IDS.length)];
  const itemCode = ITEM_CODES[Math.floor(Math.random() * ITEM_CODES.length)];

  group("BOM Tree Build", () => {
    const res = http.get(`${BASE_URL}/bome/v1/boms/${bomId}/tree`, { headers: HEADERS });
    const ok = check(res, {
      "tree 200": (r) => r.status === 200,
      "tree latency": (r) => r.timings.duration < 500,
    });
    errorRate.add(!ok);
    treeLatency.add(res.timings.duration);
  });

  group("Cost Rollup", () => {
    const res = http.post(
      `${BASE_URL}/bome/v1/boms/${bomId}/cost-rollup`,
      JSON.stringify({ volume: 1000, include_tooling: true }),
      { headers: HEADERS }
    );
    const ok = check(res, {
      "rollup 200": (r) => r.status === 200,
    });
    errorRate.add(!ok);
    rollupLatency.add(res.timings.duration);
  });

  group("Where Used", () => {
    const res = http.get(
      `${BASE_URL}/bome/v1/materials/${itemCode}/where-used`,
      { headers: HEADERS }
    );
    const ok = check(res, {
      "where-used 200": (r) => r.status === 200,
      "where-used latency": (r) => r.timings.duration < 200,
    });
    errorRate.add(!ok);
    whereUsedLatency.add(res.timings.duration);
  });

  sleep(0.5);
}

export function handleSummary(data) {
  return {
    "tests/load/bome_results.json": JSON.stringify(data, null, 2),
  };
}
```

---

## 15. Scalability

### 15.1 Poziomy skalowania

| Poziom | Wolumen BOM | Konfiguracja |
|--------|-------------|-------------|
| L1 — Dev | < 500 BOM, < 10K linii | 1 API pod, PostgreSQL single, Redis single |
| L2 — Small | 500–5K BOM, < 100K linii | 2 API pods, HPA, Redis |
| L3 — Medium | 5K–50K BOM, 100K–1M linii | HPA API 2–10, read replicas, Redis Cluster, ltree index |
| L4 — Enterprise | > 50K BOM, > 1M linii | HPA API 2–20, partycjonowanie, CQRS, materialized views |

### 15.2 Kubernetes HPA

```yaml
# k8s/bome/hpa-api.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bome-api-hpa
  namespace: industrial-cost
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bome-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 65
    - type: Resource
      resource:
        name: memory
        target:
          type: Utilization
          averageUtilization: 75
    - type: Pods
      pods:
        metric:
          name: http_requests_per_second
        target:
          type: AverageValue
          averageValue: "50"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 60
      policies:
        - type: Pods
          value: 3
          periodSeconds: 60
    scaleDown:
      stabilizationWindowSeconds: 300

---
# k8s/bome/hpa-rollup-worker.yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: bome-rollup-worker-hpa
  namespace: industrial-cost
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: bome-rollup-worker
  minReplicas: 1
  maxReplicas: 10
  metrics:
    - type: External
      external:
        metric:
          name: kafka_consumer_lag
          selector:
            matchLabels:
              topic: bome.bom.released
        target:
          type: AverageValue
          averageValue: "20"
```

### 15.3 Optymalizacja zapytań drzewa

```sql
-- ltree extension dla szybkiego traversal (L3+)
CREATE EXTENSION IF NOT EXISTS ltree;

-- Dodaj kolumnę path jako ltree (po migracji danych)
ALTER TABLE bome.bom_lines ADD COLUMN IF NOT EXISTS ltree_path ltree;

-- Index dla szybkiego subtree query
CREATE INDEX idx_bom_lines_ltree ON bome.bom_lines USING GIST (ltree_path);
CREATE INDEX idx_bom_lines_ltree_btree ON bome.bom_lines USING BTREE (ltree_path);

-- Przykład: wszystkie dzieci podzespołu (bez rekurencji CTE)
-- SELECT * FROM bome.bom_lines
-- WHERE ltree_path <@ 'bom_id.10.20'  -- subpath query O(log n) zamiast O(n)

-- Materialized view dla where-used (odświeżana przy każdym release)
CREATE MATERIALIZED VIEW bome.mv_where_used AS
SELECT
    bl.item_code,
    bh.bom_id,
    bh.product_code,
    bh.revision,
    bh.bom_type,
    bh.status,
    bl.quantity
FROM bome.bom_lines bl
JOIN bome.bom_headers bh ON bh.bom_id = bl.bom_id
WHERE bl.is_active = TRUE
  AND bh.status IN ('RELEASED', 'FROZEN');

CREATE UNIQUE INDEX ON bome.mv_where_used (item_code, bom_id);

-- Odśwież gdzie-jest-używany po release
CREATE OR REPLACE FUNCTION bome.refresh_where_used()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('RELEASED', 'FROZEN') AND OLD.status != NEW.status THEN
        REFRESH MATERIALIZED VIEW CONCURRENTLY bome.mv_where_used;
    END IF;
    RETURN NEW;
END; $$;

CREATE TRIGGER trg_refresh_where_used
    AFTER UPDATE OF status ON bome.bom_headers
    FOR EACH ROW EXECUTE FUNCTION bome.refresh_where_used();
```

### 15.4 Cost Rollup Cache

```python
import asyncio
import json
from datetime import timedelta
from typing import Optional
import redis.asyncio as aioredis
from decimal import Decimal


class RollupCacheService:
    """
    Redis cache dla wyników cost roll-up.
    Invalidation przy zmianie cen materiałów lub BOM.
    """

    TTL = timedelta(hours=6)
    KEY_PATTERN = "bome:rollup:{bom_id}:{volume}"

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    def _key(self, bom_id: str, volume: int) -> str:
        return self.KEY_PATTERN.format(bom_id=bom_id, volume=volume)

    async def get(self, bom_id: str, volume: int) -> Optional[dict]:
        raw = await self.redis.get(self._key(bom_id, volume))
        return json.loads(raw) if raw else None

    async def set(self, bom_id: str, volume: int, result: dict) -> None:
        await self.redis.setex(
            self._key(bom_id, volume),
            int(self.TTL.total_seconds()),
            json.dumps(result, default=str),
        )

    async def invalidate(self, bom_id: str) -> None:
        """Usuń wszystkie cache entries dla bom_id (wszystkie wolumeny)."""
        pattern = f"bome:rollup:{bom_id}:*"
        cursor = 0
        while True:
            cursor, keys = await self.redis.scan(cursor, match=pattern, count=100)
            if keys:
                await self.redis.delete(*keys)
            if cursor == 0:
                break

    async def invalidate_by_item(self, item_code: str, db_pool) -> int:
        """Invaliduj rollup cache dla wszystkich BOM zawierających dany item."""
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT bl.bom_id::TEXT
                FROM bome.bom_lines bl
                WHERE bl.item_code = $1 AND bl.is_active = TRUE
                """,
                item_code,
            )
        invalidated = 0
        for row in rows:
            await self.invalidate(row["bom_id"])
            invalidated += 1
        return invalidated
```

### 15.5 CQRS dla gdzie-jest-używany (L4)

```python
class WhereUsedReadModel:
    """
    Dedykowany read model dla where-used queries (L4).
    Utrzymywany asynchronicznie przez Kafka consumers.
    """

    def __init__(self, redis: aioredis.Redis):
        self.redis = redis

    async def get_parents(self, item_code: str) -> list[dict]:
        key = f"bome:wu:{item_code}"
        raw = await self.redis.smembers(key)
        return [json.loads(entry) for entry in raw]

    async def add_usage(self, item_code: str, bom_id: str, product_code: str) -> None:
        key = f"bome:wu:{item_code}"
        entry = json.dumps({"bom_id": bom_id, "product_code": product_code})
        await self.redis.sadd(key, entry)
        await self.redis.expire(key, 86400 * 7)  # 7-day TTL (refresh on access)

    async def remove_usage(self, item_code: str, bom_id: str) -> None:
        key = f"bome:wu:{item_code}"
        members = await self.redis.smembers(key)
        for m in members:
            data = json.loads(m)
            if data.get("bom_id") == bom_id:
                await self.redis.srem(key, m)
```

---

## 16. Risks

| ID | Ryzyko | Prawdopodobieństwo | Wpływ | Mitygacja |
|----|--------|-------------------|-------|-----------|
| R01 | Circular reference w strukturze BOM (A→B→C→A) | Niskie | Krytyczny | detect_circular_reference() przed każdym INSERT; test suite; trigger constraint |
| R02 | Edycja RELEASED BOM bez śladu audytowego | Niskie | Krytyczny | Trigger blokujący INSERT/UPDATE bez change_order_id; append-only audit_log |
| R03 | Niepoprawny import z CAD — zduplikowane item_codes | Wysokie | Wysoki | Walidacja przed importem; deduplication logic; dry-run mode |
| R04 | Rozbieżność BOM między BOME i ERP (SAP MM) | Średnie | Krytyczny | Automatyczna synchronizacja przez Kafka; reconciliation DAG co noc |
| R05 | Cost rollup z dużą liczbą pozycji ZERO — wycena nierealistyczna | Wysokie | Wysoki | Próg confidence 0.70 blokuje release; alert BOMEMissingPrices |
| R06 | Phantom assembly bez sub-BOM — błąd w rozwinięciu kosztów | Średnie | Wysoki | Walidacja BOM_V013 + ostrzeżenie; fallback do kosztu 0 z logowaniem |
| R07 | Variant configurator eval() na złośliwym wyrażeniu | Niskie | Krytyczny | Whitelist allowed names w eval scope; odizolowany namespace; security audit |
| R08 | Zbyt duże drzewa BOM (> 5000 węzłów) — timeout API | Niskie | Wysoki | MAX_NODES=5000 hard limit; async streaming dla eksportu; Kafka dla batch |
| R09 | Brak synchronizacji statusów między BOME i PLM | Średnie | Wysoki | Webhook retry z exponential backoff; dead-letter queue; daily reconciliation |
| R10 | Zbyt częste refresh materialized view — blokowanie WHERE_USED | Niskie | Średni | CONCURRENTLY refresh; dedykowane okno nocne dla bulk releases |
| R11 | Zmiana ceny materiału nie inwaliduje cache rollup | Średnie | Wysoki | Kafka consumer `bome.material.price_updated` → invalidate_by_item() |
| R12 | Utrata danych przy migracji EBOM → MBOM | Niskie | Krytyczny | Snapshot przed migracją; reversible migration przez create_new_revision(); testy |
| R13 | Równoległe zmiany tego samego BOM (race condition) | Niskie | Wysoki | Optimistic locking (updated_at check); SELECT FOR UPDATE w transakcji |
| R14 | Explosion factor — BOM z 1M+ expanded linii | Niskie | Wysoki | Limit na expansion; streaming processing; pre-computed flattened BOM w Redis |
| R15 | Compliance: eksport BOM do krajów objętych sankcjami (item z ITAR/EAR) | Niskie | Krytyczny | Item classification (ECCN/ITAR) w material_master; export control check przed eksportem |

---

## 17. Roadmap

### Fazy i sprinty

| Faza | Sprinty | Cel | Kluczowe deliverables |
|------|---------|-----|-----------------------|
| **Foundation** | S1–S8 | Podstawy struktury BOM | DB schema, BOMHeader/Lines CRUD, adjacency list, snapshot trigger, audit log, outbox |
| **Core Services** | S9–S16 | Multi-level BOM i koszty | BOMTreeService (recursive CTE), CostRollupService, BOMValidator (16 reguł), API v1 |
| **Advanced** | S17–S24 | Warianty, substytuty, zmiany | VariantConfigurator, MaterialSubstitutionEngine, ChangeOrderService, where-used |
| **Integration** | S25–S32 | CAD/PLM, ERP, skalowanie | BOMImporter (TC/Windchill/CSV), ERP sync, ltree index, rollup cache, load tests |

### Szczegółowy plan

```
S1  DB schema bome.* — ENUMy, material_master, bom_headers, bom_lines, audit_log, outbox
S2  Triggers: set_updated_at, validate_bom_edit (blokada RELEASED bez ECO)
S3  Trigger: snapshot_bom_on_status_change → bom_versions; get_effective_bom() function
S4  BOMHeader CRUD API (POST/GET/PUT/DELETE /boms, PATCH /boms/{id}/status)
S5  BOMLine CRUD API (POST/GET/PUT/DELETE /boms/{id}/lines)
S6  Outbox worker + Kafka producer (bome.bom.created, bome.bom.released)
S7  Audit log integration — wszystkie write operations
S8  Basic monitoring (Prometheus metrics), Grafana BOME Overview dashboard

S9  BOMTreeService — recursive CTE build_tree(), iter_all(), iter_leaves()
S10 BOMTreeService — get_where_used(), detect_circular_reference(), get_indented_bom()
S11 CostRollupService — bottom-up DFS, price lookup, overhead rates, tooling amortization
S12 CostRollupService — CEE integration dla process cost, rollup persistence
S13 BOMValidator — reguły V001–V016, ValidationReport, release gate
S14 GET /boms/{id}/tree, GET /boms/{id}/indented, POST /boms/{id}/cost-rollup API
S15 GET /boms/{id}/compare diff endpoint, BOMDiff dataclass
S16 Full API test suite (schemathesis), integration tests (Testcontainers)

S17 ProductFamily + VariantOption + VariantRule DB tables
S18 VariantConfigurator — _apply_rules(), condition/quantity/item-override eval
S19 POST /families/{id}/configure API + config_hash cache
S20 PlanningBOM (percentowy split, weighted cost)
S21 MaterialSubstitutionEngine — evaluate_substitution(), RoHS/REACH check
S22 SubstitutionRequest workflow + approval + apply_substitution()
S23 /substitutions API + /materials/{code}/substitutes endpoint
S24 Substitution Kafka event (bome.substitution.approved), CEE invalidation

S25 ChangeOrderService — APPROVAL_CHAINS, numbering, submit_for_review()
S26 record_approval() + approval chain progression + notification
S27 assess_impact() — affected BOM count, obsolete stock, requalification flags
S28 /change-orders API (CREATE/SUBMIT/APPROVE/IMPLEMENT)
S29 BOMImporter — CSV import, parser + _import_items pipeline
S30 BOMImporter — Teamcenter connector (SOA REST)
S31 BOMImporter — Windchill connector (REST API)
S32 BOMVersionControl — create_new_revision(), _copy_lines(), diff_versions()

S33 ltree extension + index (L3 optimization)
S34 Materialized view mv_where_used + CONCURRENTLY refresh trigger
S35 RollupCacheService (Redis) + invalidate_by_item() Kafka consumer
S36 WhereUsedReadModel (CQRS, Redis Set) + consumer wiring
S37 ERP SAP connector — IDOC/RFC sync bome → SAP MM
S38 Export BOM endpoint (CSV/Excel/JSON) + ITAR/EAR export control check
S39 k6 load tests, performance tuning, HPA configuration
S40 Security review, RBAC hardening, audit log immutability test
```

### Docelowe KPIs (po S40)

| Metryka | Cel |
|---------|-----|
| BOM tree build (100-node) | < 100ms p95 |
| Cost roll-up (500-node BOM) | < 2s p95 |
| Where-used query | < 200ms p95 |
| BOM import (1000 lines) | < 30s |
| API P95 latency | < 500ms |
| Rollup confidence (released BOM) | ≥ 0.90 |
| Change order cycle time (ECO) | < 14 days |
| BOM release success rate | > 95% (first attempt) |
| Circular reference detection | < 50ms |
| CAD sync lag | < 4h |
