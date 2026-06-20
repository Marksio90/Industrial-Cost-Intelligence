# BOM Engine — Sekcje 10–13: Change Management, CAD Integration, Validation Rules, Monitoring

---

## 10. Change Management

### 10.1 Typy zmian

| Typ | Skrót | Opis | Wymagana akceptacja |
|-----|-------|------|---------------------|
| Engineering Change Order | ECO | Zmiana techniczna (rysunek, materiał, tolerancje) | Inżynieria + Jakość |
| Manufacturing Change Order | MCO | Zmiana procesu produkcji bez zmiany formy/fit/function | Technologia |
| Document Change Order | DCO | Zmiana dokumentacji bez zmian fizycznych | Inżynieria |
| Deviation | DEV | Jednorazowe odchylenie od standardu (ograniczony czas/ilość) | Jakość + Klient |
| Correction | COR | Błąd we wcześniejszym ECO — poprawka formalna | Właściciel ECO |

### 10.2 Przepływ Change Order

```
DRAFT → SUBMITTED → IN_REVIEW → APPROVED → IMPLEMENTING → CLOSED
                              ↘ REJECTED
                                          ↘ CANCELLED (w każdym stanie)
```

### 10.3 ChangeOrderService

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Optional
import uuid

UTC = timezone.utc
logger = logging.getLogger(__name__)


APPROVAL_CHAINS = {
    # change_type → ordered list of approver roles
    "ECO":       ["engineering_lead", "quality_manager", "procurement"],
    "MCO":       ["manufacturing_engineer", "quality_manager"],
    "DCO":       ["engineering_lead"],
    "DEVIATION": ["quality_manager", "customer_approval"],
    "CORRECTION": ["change_owner"],
    "INITIAL_RELEASE": ["engineering_lead", "quality_manager"],
}

RISK_REQUIRED_ROLES = {
    "HIGH":     ["engineering_lead", "quality_manager", "program_manager"],
    "CRITICAL": ["engineering_lead", "quality_manager", "program_manager", "plant_director"],
    "MEDIUM":   ["engineering_lead", "quality_manager"],
    "LOW":      ["engineering_lead"],
}


@dataclass
class ApprovalStep:
    step_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    role: str = ""
    approver_id: Optional[str] = None
    decision: str = "PENDING"   # PENDING / APPROVED / REJECTED
    comment: Optional[str] = None
    decided_at: Optional[datetime] = None
    due_date: Optional[date] = None


@dataclass
class ChangeImpactAssessment:
    """Ocena wpływu change order przed zatwierdzeniem."""
    change_id: str
    affected_bom_count: int
    affected_component_count: int
    estimated_cost_impact_eur: Optional[float]
    lead_time_impact_days: int
    quality_risk: str             # LOW / MEDIUM / HIGH
    requires_customer_approval: bool
    requires_requalification: bool
    obsolete_stock_qty: Optional[float]
    obsolete_stock_value_eur: Optional[float]
    rollout_plan: Optional[str]


class ChangeOrderService:
    """Zarządza cyklem życia Change Orders z approval chain."""

    NUMBERING_PREFIX = {
        "ECO": "ECO", "MCO": "MCO", "DCO": "DCO",
        "DEVIATION": "DEV", "CORRECTION": "COR", "INITIAL_RELEASE": "REL",
    }

    def __init__(self, db_pool, notification_service, kafka_producer):
        self.db = db_pool
        self.notify = notification_service
        self.kafka = kafka_producer

    async def create(
        self,
        change_type: str,
        title: str,
        description: str,
        requester: str,
        affected_bom_ids: list[str],
        affected_item_codes: list[str],
        risk_level: str = "MEDIUM",
        implementation_date: Optional[date] = None,
        cost_impact_eur: Optional[float] = None,
    ) -> dict:
        change_number = await self._next_change_number(change_type)

        # Zbuduj approval chain
        required_roles = APPROVAL_CHAINS.get(change_type, ["engineering_lead"])
        risk_roles = RISK_REQUIRED_ROLES.get(risk_level, [])
        all_roles = list(dict.fromkeys(required_roles + risk_roles))  # deduplicate, preserve order

        approval_chain = [
            ApprovalStep(role=role).__dict__
            for role in all_roles
        ]

        async with self.db.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO bome.change_orders (
                    change_number, change_type, title, description, status,
                    affected_bom_ids, affected_item_codes, requester,
                    implementation_date, risk_level, cost_impact_eur,
                    approval_chain
                ) VALUES ($1,$2,$3,$4,'DRAFT',$5,$6,$7,$8,$9,$10,$11::jsonb)
                RETURNING *
                """,
                change_number, change_type, title, description,
                affected_bom_ids, affected_item_codes, requester,
                implementation_date, risk_level, cost_impact_eur,
                __import__("json").dumps(approval_chain),
            )

        change = dict(row)
        logger.info("Created %s %s by %s", change_type, change_number, requester)
        return change

    async def submit_for_review(self, change_id: str, submitted_by: str) -> None:
        async with self.db.acquire() as conn:
            change = await conn.fetchrow(
                "SELECT * FROM bome.change_orders WHERE change_id=$1", change_id
            )
            if not change or change["status"] != "DRAFT":
                raise ValueError("Can only submit DRAFT change orders")

            await conn.execute(
                """
                UPDATE bome.change_orders
                SET status='SUBMITTED', owner=$2, updated_at=NOW()
                WHERE change_id=$1
                """,
                change_id, submitted_by,
            )

        # Powiadom pierwszego approver
        chain = change["approval_chain"]
        if chain:
            await self.notify.notify_approval_required(
                change_id=str(change["change_id"]),
                change_number=change["change_number"],
                role=chain[0]["role"],
                risk_level=change["risk_level"],
            )

    async def record_approval(
        self,
        change_id: str,
        role: str,
        approver_id: str,
        decision: str,   # APPROVED / REJECTED
        comment: Optional[str] = None,
    ) -> None:
        import json

        async with self.db.acquire() as conn:
            async with conn.transaction():
                change = await conn.fetchrow(
                    "SELECT * FROM bome.change_orders WHERE change_id=$1 FOR UPDATE", change_id
                )
                if not change:
                    raise ValueError(f"Change order {change_id} not found")
                if change["status"] not in ("SUBMITTED", "IN_REVIEW"):
                    raise ValueError(f"Cannot approve in status {change['status']}")

                chain = list(change["approval_chain"])
                now = datetime.now(UTC)
                updated = False

                for step in chain:
                    if step["role"] == role and step["decision"] == "PENDING":
                        step["approver_id"] = approver_id
                        step["decision"] = decision
                        step["comment"] = comment
                        step["decided_at"] = now.isoformat()
                        updated = True
                        break

                if not updated:
                    raise ValueError(f"No pending approval step for role {role}")

                # Oblicz nowy status
                if decision == "REJECTED":
                    new_status = "REJECTED"
                elif all(s["decision"] == "APPROVED" for s in chain):
                    new_status = "APPROVED"
                else:
                    new_status = "IN_REVIEW"

                await conn.execute(
                    """
                    UPDATE bome.change_orders
                    SET status=$2, approval_chain=$3::jsonb, updated_at=NOW()
                    WHERE change_id=$1
                    """,
                    change_id, new_status, json.dumps(chain),
                )

        if new_status == "APPROVED":
            await self._on_change_approved(change_id, str(change["change_number"]))
        elif new_status == "REJECTED":
            await self.notify.notify_change_rejected(
                change_id=change_id,
                change_number=change["change_number"],
                rejector=approver_id,
                comment=comment or "",
            )
        else:
            # Powiadom następnego approver
            pending = [s for s in chain if s["decision"] == "PENDING"]
            if pending:
                await self.notify.notify_approval_required(
                    change_id=change_id,
                    change_number=change["change_number"],
                    role=pending[0]["role"],
                    risk_level=change["risk_level"],
                )

    async def implement(self, change_id: str, implemented_by: str) -> None:
        """Oznacz change order jako zaimplementowany → CLOSED."""
        async with self.db.acquire() as conn:
            change = await conn.fetchrow(
                "SELECT * FROM bome.change_orders WHERE change_id=$1", change_id
            )
            if not change or change["status"] != "APPROVED":
                raise ValueError("Can only implement APPROVED change orders")

            await conn.execute(
                """
                UPDATE bome.change_orders
                SET status='IMPLEMENTING', updated_at=NOW()
                WHERE change_id=$1
                """,
                change_id,
            )

        # Sprawdź czy wszystkie BOM mają zaktualizowane statusy
        async with self.db.acquire() as conn:
            unreleased = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bome.bom_headers
                WHERE bom_id = ANY($1::uuid[])
                  AND status NOT IN ('RELEASED', 'FROZEN')
                """,
                change["affected_bom_ids"],
            )

        if unreleased > 0:
            raise ValueError(
                f"{unreleased} affected BOM(s) still not in RELEASED/FROZEN status"
            )

        async with self.db.acquire() as conn:
            await conn.execute(
                """
                UPDATE bome.change_orders
                SET status='CLOSED', closed_at=NOW(), updated_at=NOW()
                WHERE change_id=$1
                """,
                change_id,
            )

        await self._emit_change_implemented(change_id, str(change["change_number"]))

    async def assess_impact(self, change_id: str) -> ChangeImpactAssessment:
        async with self.db.acquire() as conn:
            change = await conn.fetchrow(
                "SELECT * FROM bome.change_orders WHERE change_id=$1", change_id
            )
            bom_count = len(change["affected_bom_ids"])
            item_count = len(change["affected_item_codes"])

            # Sprawdź czy ktryś materiał ma zapas w ERP (placeholder)
            obsolete_stock = await conn.fetchrow(
                """
                SELECT
                    SUM(mm.min_order_qty) AS qty,
                    SUM(mm.min_order_qty * mm.last_po_price_eur) AS value
                FROM bome.material_master mm
                WHERE mm.item_code = ANY($1::text[])
                """,
                change["affected_item_codes"],
            )

        return ChangeImpactAssessment(
            change_id=change_id,
            affected_bom_count=bom_count,
            affected_component_count=item_count,
            estimated_cost_impact_eur=change["cost_impact_eur"],
            lead_time_impact_days=0,  # do zintegrowania z ERP
            quality_risk=change["risk_level"],
            requires_customer_approval=change["risk_level"] in ("HIGH", "CRITICAL"),
            requires_requalification=change["change_type"] in ("ECO",),
            obsolete_stock_qty=float(obsolete_stock["qty"]) if obsolete_stock["qty"] else None,
            obsolete_stock_value_eur=float(obsolete_stock["value"]) if obsolete_stock["value"] else None,
            rollout_plan=None,
        )

    async def _next_change_number(self, change_type: str) -> str:
        prefix = self.NUMBERING_PREFIX.get(change_type, "CHG")
        year = datetime.now(UTC).year
        async with self.db.acquire() as conn:
            count = await conn.fetchval(
                """
                SELECT COUNT(*) FROM bome.change_orders
                WHERE change_type=$1
                  AND EXTRACT(YEAR FROM created_at) = $2
                """,
                change_type, year,
            )
        return f"{prefix}-{year}-{(count + 1):04d}"

    async def _on_change_approved(self, change_id: str, change_number: str) -> None:
        await self.notify.notify_change_approved(change_id, change_number)
        await self.kafka.send(
            "bome.change_order.approved",
            {"change_id": change_id, "change_number": change_number},
        )

    async def _emit_change_implemented(self, change_id: str, change_number: str) -> None:
        await self.kafka.send(
            "bome.change_order.implemented",
            {"change_id": change_id, "change_number": change_number},
        )
```

---

## 11. Integration with CAD

### 11.1 Architektura integracji PLM/CAD

```
CAD System (CATIA V5 / SolidWorks / NX)
        │
        │  PLM Connector (REST/SOAP)
        ▼
PLM System (Teamcenter / Windchill / Enovia)
        │
        │  BOMImporter (webhook / pull / file-based)
        ▼
BOM Engine API ──────────────────────────────────►  bome.bom_headers
        │                                            bome.bom_lines
        │  Event: bome.bom.released
        ▼
CEE / CLS / ERP
```

### 11.2 Metody importu

| Metoda | Protokół | Kiedy | Obsługiwane formaty |
|--------|----------|-------|---------------------|
| Push (webhook) | HTTP POST | Przy każdym checkin w PLM | JSON (PLM native) |
| Pull (polling) | REST GET | Co noc lub na żądanie | JSON / XML |
| File-based | SFTP | Systemy legacy | CSV, EBOM Excel |
| CAD plugin | SDK | Direktnie z CAD | Native CAD BOM |

### 11.3 BOMImporter

```python
from __future__ import annotations

import csv
import io
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional
import httpx

logger = logging.getLogger(__name__)


@dataclass
class PLMItem:
    """Struktura elementu PLM (znormalizowana z różnych systemów)."""
    plm_item_id: str
    item_code: str          # Part number
    description: str
    revision: str
    parent_item_id: Optional[str]
    quantity: Decimal
    uom: str
    material_spec: Optional[str]
    weight_kg: Optional[Decimal]
    finish: Optional[str]
    drawing_number: Optional[str]
    find_number: Optional[str]
    reference_designator: Optional[str]
    cad_document_id: Optional[str]
    cad_document_version: Optional[str]


@dataclass
class BOMImportResult:
    product_code: str
    revision: str
    bom_id: Optional[str]
    status: str             # SUCCESS / PARTIAL / FAILED
    lines_imported: int
    lines_skipped: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class BOMImporter:
    """
    Importuje BOM z różnych źródeł PLM/CAD do BOME.
    Wspiera Teamcenter, Windchill, SolidWorks PDM, plik CSV.
    """

    def __init__(self, bome_api_client, material_master_service):
        self.api = bome_api_client
        self.mm = material_master_service

    async def import_from_teamcenter(
        self,
        tc_item_id: str,
        tc_revision: str,
        tc_url: str,
        tc_credentials: dict,
    ) -> BOMImportResult:
        """Import z Teamcenter via SOA REST API."""
        async with httpx.AsyncClient(base_url=tc_url, timeout=30.0) as client:
            # Uwierzytelnienie
            session = await client.post(
                "/tc/login",
                json={"credentials": tc_credentials},
            )
            session.raise_for_status()
            cookies = session.cookies

            # Pobierz strukturę BOM
            response = await client.get(
                f"/tc/structure/{tc_item_id}/{tc_revision}/bom",
                cookies=cookies,
                params={"depth": "all", "format": "json"},
            )
            response.raise_for_status()
            data = response.json()

        items = self._parse_teamcenter_structure(data)
        return await self._import_items(items, source="TEAMCENTER")

    async def import_from_csv(
        self,
        csv_content: str,
        product_code: str,
        revision: str,
        bom_type: str = "ENGINEERING",
    ) -> BOMImportResult:
        """
        Import z pliku CSV (format: pos,parent_pos,item_code,description,qty,uom,scrap_pct,make_buy).
        Nagłówek: position,parent_position,item_code,description,quantity,uom,scrap_pct,make_or_buy
        """
        reader = csv.DictReader(io.StringIO(csv_content))
        items: list[PLMItem] = []
        pos_to_id: dict[str, str] = {}
        errors: list[str] = []

        for i, row in enumerate(reader, start=2):
            try:
                pos = str(row["position"]).strip()
                parent_pos = str(row.get("parent_position", "")).strip()
                item = PLMItem(
                    plm_item_id=pos,
                    item_code=row["item_code"].strip(),
                    description=row.get("description", "").strip(),
                    revision="",
                    parent_item_id=parent_pos if parent_pos else None,
                    quantity=Decimal(str(row["quantity"])),
                    uom=row.get("uom", "PC").strip().upper(),
                    material_spec=None,
                    weight_kg=None,
                    finish=None,
                    drawing_number=None,
                    find_number=None,
                    reference_designator=row.get("reference_designator"),
                    cad_document_id=None,
                    cad_document_version=None,
                )
                items.append(item)
            except (KeyError, ValueError) as e:
                errors.append(f"Row {i}: {e}")

        result = await self._import_items(
            items, source="CSV",
            product_code=product_code, revision=revision, bom_type=bom_type,
        )
        result.errors.extend(errors)
        return result

    async def import_from_windchill(
        self,
        wc_number: str,
        wc_revision: str,
        wc_url: str,
        wc_token: str,
    ) -> BOMImportResult:
        """Import z PTC Windchill via REST API."""
        async with httpx.AsyncClient(
            base_url=wc_url,
            headers={"Authorization": f"Bearer {wc_token}"},
            timeout=30.0,
        ) as client:
            response = await client.get(
                f"/Windchill/servlet/rest/wctype/wt.part.WTPart/{wc_number}/{wc_revision}/uses",
                params={"maxDepth": 20},
            )
            response.raise_for_status()
            data = response.json()

        items = self._parse_windchill_structure(data)
        return await self._import_items(items, source="WINDCHILL")

    async def _import_items(
        self,
        items: list[PLMItem],
        source: str,
        product_code: Optional[str] = None,
        revision: Optional[str] = None,
        bom_type: str = "ENGINEERING",
    ) -> BOMImportResult:
        if not items:
            return BOMImportResult(
                product_code=product_code or "", revision=revision or "",
                bom_id=None, status="FAILED", lines_imported=0, lines_skipped=0,
                errors=["No items to import"],
            )

        # Znajdź root element
        root = next((i for i in items if i.parent_item_id is None), None)
        if not root:
            raise ValueError("Cannot find root element (item without parent)")

        prod_code = product_code or root.item_code
        rev = revision or root.revision or "A"
        warnings: list[str] = []
        errors: list[str] = []

        # Ensure materials exist in material master
        for item in items:
            await self._ensure_material_exists(item, warnings)

        # Utwórz lub zaktualizuj BOM w BOME
        bom_id = await self.api.create_or_update_bom(
            product_code=prod_code,
            revision=rev,
            bom_type=bom_type,
            cad_document_id=root.cad_document_id,
            cad_document_version=root.cad_document_version,
        )

        # Importuj linie (BFS, żeby parent_line_id był już znany)
        imported = 0
        skipped = 0
        plm_id_to_line_id: dict[str, str] = {}

        from collections import deque
        queue = deque([item for item in items if item.parent_item_id is None])
        remaining = {i.plm_item_id: i for i in items if i.parent_item_id is not None}

        while queue:
            item = queue.popleft()
            parent_line_id = plm_id_to_line_id.get(item.parent_item_id) if item.parent_item_id else None

            try:
                line_id = await self.api.add_bom_line(
                    bom_id=bom_id,
                    parent_line_id=parent_line_id,
                    item_code=item.item_code,
                    quantity=item.quantity,
                    uom=item.uom,
                    find_number=item.find_number,
                    reference_designator=item.reference_designator,
                    cad_reference=item.plm_item_id,
                )
                plm_id_to_line_id[item.plm_item_id] = line_id
                imported += 1
            except Exception as e:
                errors.append(f"Failed to import {item.item_code}: {e}")
                skipped += 1
                continue

            # Dodaj dzieci do kolejki
            children = [i for i in remaining.values() if i.parent_item_id == item.plm_item_id]
            for child in children:
                queue.append(child)
                del remaining[child.plm_item_id]

        return BOMImportResult(
            product_code=prod_code, revision=rev,
            bom_id=bom_id,
            status="SUCCESS" if not errors else ("PARTIAL" if imported > 0 else "FAILED"),
            lines_imported=imported, lines_skipped=skipped,
            errors=errors, warnings=warnings,
        )

    async def _ensure_material_exists(self, item: PLMItem, warnings: list[str]) -> None:
        """Stwórz placeholder w material_master jeśli materiał nie istnieje."""
        exists = await self.mm.exists(item.item_code)
        if not exists:
            await self.mm.create_placeholder(
                item_code=item.item_code,
                description=item.description,
                weight_kg_per_uom=item.weight_kg,
            )
            warnings.append(f"Created placeholder for {item.item_code} — price missing")

    @staticmethod
    def _parse_teamcenter_structure(data: dict) -> list[PLMItem]:
        """Mapuje Teamcenter SOA response na PLMItem list."""
        items = []
        def _parse(node: dict, parent_id: Optional[str] = None):
            items.append(PLMItem(
                plm_item_id=node["uid"],
                item_code=node.get("item_id", node["uid"]),
                description=node.get("object_string", ""),
                revision=node.get("revision_id", "A"),
                parent_item_id=parent_id,
                quantity=Decimal(str(node.get("quantity", 1))),
                uom=node.get("uom", "PC"),
                material_spec=node.get("material_spec"),
                weight_kg=Decimal(str(node["weight_kg"])) if node.get("weight_kg") else None,
                finish=node.get("finish"),
                drawing_number=node.get("drawing_number"),
                find_number=str(node.get("find_number", "")),
                reference_designator=node.get("ref_des"),
                cad_document_id=node.get("cad_id"),
                cad_document_version=node.get("cad_version"),
            ))
            for child in node.get("children", []):
                _parse(child, node["uid"])
        _parse(data)
        return items

    @staticmethod
    def _parse_windchill_structure(data: dict) -> list[PLMItem]:
        """Mapuje Windchill REST response na PLMItem list."""
        items = []
        for link in data.get("links", []):
            item_data = link.get("item", {})
            items.append(PLMItem(
                plm_item_id=item_data.get("oid", ""),
                item_code=item_data.get("number", ""),
                description=item_data.get("name", ""),
                revision=item_data.get("version", {}).get("identifier", {}).get("versionId", "A"),
                parent_item_id=link.get("parentOid"),
                quantity=Decimal(str(link.get("quantity", 1))),
                uom=link.get("unit", "PC"),
                material_spec=None,
                weight_kg=None,
                finish=None,
                drawing_number=None,
                find_number=link.get("referenceDesignator"),
                reference_designator=None,
                cad_document_id=item_data.get("oid"),
                cad_document_version=None,
            ))
        return items
```

### 11.4 Synchronizacja dwukierunkowa (CAD ← BOME)

```python
class BOMExporter:
    """Eksport zmian kosztowych z BOME z powrotem do PLM (np. standard cost update)."""

    async def export_standard_cost_to_plm(
        self,
        bom_id: str,
        plm_client,
        rollup_service: "CostRollupService",
        volume: int = 1,
    ) -> dict:
        """Aktualizuje pola 'standard cost' w PLM dla każdego itemu."""
        rollup = await rollup_service.rollup(bom_id, volume=volume)

        updates = []
        for lr in rollup.line_results:
            updates.append({
                "item_code": lr.item_code,
                "standard_cost_eur": float(lr.unit_cost_eur),
                "cost_source": lr.cost_source,
            })

        result = await plm_client.batch_update_standard_cost(updates)
        logger.info("Updated standard cost for %d items in PLM", len(updates))
        return result
```

---

## 12. Validation Rules

### 12.1 BOMValidator

```python
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ValidationIssue:
    severity: str           # ERROR / WARNING / INFO
    code: str               # np. "BOM_V001"
    message: str
    line_id: Optional[str] = None
    item_code: Optional[str] = None
    field: Optional[str] = None


@dataclass
class ValidationReport:
    bom_id: str
    passed: bool
    errors: list[ValidationIssue] = field(default_factory=list)
    warnings: list[ValidationIssue] = field(default_factory=list)
    infos: list[ValidationIssue] = field(default_factory=list)

    @property
    def total_issues(self) -> int:
        return len(self.errors) + len(self.warnings)


class BOMValidator:
    """
    Walidator BOM — sprawdza spójność struktury i gotowość do release.
    Uruchamiany przed każdą zmianą statusu.
    """

    # Maksymalna głębokość struktury
    MAX_DEPTH = 15
    # Minimalny scrapped yield (zbyt wysoki scrap jest błędem)
    MAX_SCRAP_PCT = 50.0
    # Minimalny koszt rollup confidence dla RELEASE
    MIN_ROLLUP_CONFIDENCE_FOR_RELEASE = 0.70

    async def validate(
        self,
        bom_id: str,
        target_status: str,
        tree_service: "BOMTreeService",
        cost_rollup_service: Optional["CostRollupService"] = None,
        db_pool=None,
    ) -> ValidationReport:
        report = ValidationReport(bom_id=bom_id, passed=True)

        # Pobierz BOM
        roots = await tree_service.build_tree(bom_id, expand_phantoms=False)
        if not roots:
            report.errors.append(ValidationIssue(
                severity="ERROR", code="BOM_V001",
                message="BOM is empty — no lines found",
            ))
            report.passed = False
            return report

        # Reguła V001: Circular reference
        if await self._check_circular(bom_id, roots, tree_service):
            report.errors.append(ValidationIssue(
                severity="ERROR", code="BOM_V002",
                message="Circular reference detected in BOM structure",
            ))
            report.passed = False

        # Reguła V003: Depth
        max_depth = max((n.depth for root in roots for n in root.iter_all()), default=0)
        if max_depth > self.MAX_DEPTH:
            report.warnings.append(ValidationIssue(
                severity="WARNING", code="BOM_V003",
                message=f"BOM depth {max_depth} exceeds recommended maximum {self.MAX_DEPTH}",
            ))

        # Reguła V004-V010: Per-line validations
        for root in roots:
            for node in root.iter_all():
                line = node.line
                line_issues = self._validate_line(line)
                for issue in line_issues:
                    if issue.severity == "ERROR":
                        report.errors.append(issue)
                    else:
                        report.warnings.append(issue)

        # Reguła V011: Missing prices (WARNING)
        missing_prices = await self._check_missing_prices(roots, db_pool)
        for item_code in missing_prices:
            report.warnings.append(ValidationIssue(
                severity="WARNING", code="BOM_V011",
                message=f"No price in material master for {item_code}",
                item_code=item_code,
            ))

        # Reguła V012: Duplicated positions
        dup_positions = self._check_duplicate_positions(roots)
        for pos_info in dup_positions:
            report.errors.append(ValidationIssue(
                severity="ERROR", code="BOM_V012",
                message=f"Duplicate position {pos_info} in same BOM level",
            ))

        # Reguła V013: Phantom assemblies have sub-BOM
        phantom_issues = await self._check_phantom_boms(roots, db_pool)
        for item_code in phantom_issues:
            report.warnings.append(ValidationIssue(
                severity="WARNING", code="BOM_V013",
                message=f"Phantom {item_code} has no released sub-BOM — will not be expanded",
                item_code=item_code,
            ))

        # Dla RELEASE: dodatkowe walidacje
        if target_status in ("RELEASED", "FROZEN"):
            release_issues = await self._validate_for_release(
                bom_id, roots, cost_rollup_service
            )
            for issue in release_issues:
                if issue.severity == "ERROR":
                    report.errors.append(issue)
                else:
                    report.warnings.append(issue)

        report.passed = len(report.errors) == 0
        return report

    def _validate_line(self, line: "BOMLine") -> list[ValidationIssue]:
        issues = []

        # V004: Quantity > 0
        if line.quantity <= 0:
            issues.append(ValidationIssue(
                severity="ERROR", code="BOM_V004",
                message=f"Quantity must be > 0 (got {line.quantity})",
                line_id=line.line_id, item_code=line.item_code, field="quantity",
            ))

        # V005: Scrap factor range
        if line.scrap_factor_pct < 0 or line.scrap_factor_pct >= self.MAX_SCRAP_PCT:
            issues.append(ValidationIssue(
                severity="ERROR", code="BOM_V005",
                message=f"Scrap factor {line.scrap_factor_pct}% out of range [0, {self.MAX_SCRAP_PCT})",
                line_id=line.line_id, item_code=line.item_code, field="scrap_factor_pct",
            ))

        # V006: High scrap warning
        if 20.0 <= line.scrap_factor_pct < self.MAX_SCRAP_PCT:
            issues.append(ValidationIssue(
                severity="WARNING", code="BOM_V006",
                message=f"Unusually high scrap factor: {line.scrap_factor_pct}% for {line.item_code}",
                line_id=line.line_id, item_code=line.item_code,
            ))

        # V007: item_code not empty
        if not line.item_code or not line.item_code.strip():
            issues.append(ValidationIssue(
                severity="ERROR", code="BOM_V007",
                message="item_code cannot be empty",
                line_id=line.line_id, field="item_code",
            ))

        # V008: TOOLING line must have cost_override
        if line.line_type.value == "TOOLING" and line.cost_override_eur is None:
            issues.append(ValidationIssue(
                severity="WARNING", code="BOM_V008",
                message=f"TOOLING line {line.item_code} should have cost_override_eur set",
                line_id=line.line_id, item_code=line.item_code,
            ))

        # V009: PHANTOM without children_check (checked elsewhere)
        # V010: REFERENCE items should have qty=1
        if line.line_type.value == "REFERENCE" and line.quantity != Decimal("1"):
            issues.append(ValidationIssue(
                severity="INFO", code="BOM_V010",
                message=f"REFERENCE item {line.item_code} has quantity {line.quantity} (expected 1)",
                line_id=line.line_id, item_code=line.item_code,
            ))

        return issues

    async def _check_circular(
        self, bom_id: str, roots: list, tree_service: "BOMTreeService"
    ) -> bool:
        """Sprawdź circular reference przez szukanie bom_id w jego własnych podrzędnych."""
        for root in roots:
            for node in root.iter_all():
                if node.line.item_code and await tree_service.detect_circular_reference(
                    bom_id, node.line.item_code
                ):
                    return True
        return False

    def _check_duplicate_positions(self, roots: list) -> list[str]:
        """Znajdź zduplikowane pozycje na tym samym poziomie."""
        duplicates = []
        def _check(nodes, parent_id):
            positions = {}
            for node in nodes:
                pos = (parent_id, node.line.position)
                if pos in positions:
                    duplicates.append(f"position={node.line.position} parent={parent_id}")
                positions[pos] = node.line.line_id
                _check(node.children, node.line.line_id)
        _check(roots, None)
        return duplicates

    async def _check_missing_prices(self, roots: list, db_pool) -> list[str]:
        if not db_pool:
            return []
        leaf_items = list({
            n.line.item_code
            for root in roots
            for n in root.iter_leaves()
            if n.line.line_type.value not in ("REFERENCE", "TOOLING")
               and n.line.cost_override_eur is None
        })
        if not leaf_items:
            return []
        async with db_pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT item_code FROM bome.material_master
                WHERE item_code = ANY($1::text[])
                  AND list_price_eur IS NULL AND last_po_price_eur IS NULL
                """,
                leaf_items,
            )
        return [r["item_code"] for r in rows]

    async def _check_phantom_boms(self, roots: list, db_pool) -> list[str]:
        if not db_pool:
            return []
        phantom_items = [
            n.line.item_code
            for root in roots
            for n in root.iter_all()
            if n.line.phantom
        ]
        if not phantom_items:
            return []
        missing = []
        async with db_pool.acquire() as conn:
            for item_code in phantom_items:
                exists = await conn.fetchval(
                    """
                    SELECT 1 FROM bome.bom_headers
                    WHERE product_code=$1 AND status IN ('RELEASED','FROZEN')
                    LIMIT 1
                    """,
                    item_code,
                )
                if not exists:
                    missing.append(item_code)
        return missing

    async def _validate_for_release(
        self,
        bom_id: str,
        roots: list,
        cost_rollup_service: Optional["CostRollupService"],
    ) -> list[ValidationIssue]:
        issues = []

        # V014: Przynajmniej jedna linia aktywna
        non_ref_lines = [
            n for root in roots for n in root.iter_all()
            if n.line.line_type.value != "REFERENCE"
        ]
        if not non_ref_lines:
            issues.append(ValidationIssue(
                severity="ERROR", code="BOM_V014",
                message="BOM must have at least one non-REFERENCE line before RELEASE",
            ))

        # V015: Cost rollup confidence
        if cost_rollup_service:
            try:
                rollup = await cost_rollup_service.rollup(bom_id)
                if rollup.rollup_confidence < self.MIN_ROLLUP_CONFIDENCE_FOR_RELEASE:
                    issues.append(ValidationIssue(
                        severity="ERROR", code="BOM_V015",
                        message=(
                            f"Cost rollup confidence {rollup.rollup_confidence:.2f} "
                            f"below minimum {self.MIN_ROLLUP_CONFIDENCE_FOR_RELEASE} for RELEASE. "
                            "Add missing prices."
                        ),
                    ))
                if rollup.warnings:
                    issues.append(ValidationIssue(
                        severity="WARNING", code="BOM_V015W",
                        message=f"Cost rollup has {len(rollup.warnings)} warnings: {rollup.warnings[0]}",
                    ))
            except Exception as e:
                issues.append(ValidationIssue(
                    severity="WARNING", code="BOM_V015E",
                    message=f"Could not compute cost rollup: {e}",
                ))

        # V016: Critical items mają lead_time_days ustawione
        critical_without_lt = [
            n.line.item_code
            for root in roots
            for n in root.iter_all()
            if n.line.critical_item and n.line.lead_time_days is None
        ]
        for item_code in critical_without_lt:
            issues.append(ValidationIssue(
                severity="WARNING", code="BOM_V016",
                message=f"Critical item {item_code} has no lead_time_days — supply risk not quantified",
                item_code=item_code,
            ))

        return issues
```

### 12.2 Tabela reguł walidacji

| Kod | Severity | Reguła | Blokuje release? |
|-----|----------|--------|-----------------|
| BOM_V001 | ERROR | BOM nie ma żadnych linii | Tak |
| BOM_V002 | ERROR | Circular reference wykryty | Tak |
| BOM_V003 | WARNING | Głębokość > 15 poziomów | Nie |
| BOM_V004 | ERROR | Quantity ≤ 0 | Tak |
| BOM_V005 | ERROR | Scrap factor poza [0, 50) % | Tak |
| BOM_V006 | WARNING | Scrap factor > 20% | Nie |
| BOM_V007 | ERROR | item_code pusty | Tak |
| BOM_V008 | WARNING | TOOLING bez cost_override | Nie |
| BOM_V010 | INFO | REFERENCE z qty ≠ 1 | Nie |
| BOM_V011 | WARNING | Brak ceny w material master | Nie |
| BOM_V012 | ERROR | Zduplikowane pozycje na tym samym poziomie | Tak |
| BOM_V013 | WARNING | Phantom assembly bez sub-BOM | Nie |
| BOM_V014 | ERROR | Brak linii nie-REFERENCE przed release | Tak |
| BOM_V015 | ERROR | Rollup confidence < 0.70 | Tak |
| BOM_V016 | WARNING | Critical item bez lead_time_days | Nie |

---

## 13. Monitoring

### 13.1 Metryki Prometheus

```python
from prometheus_client import Counter, Histogram, Gauge, Info

# ── BOM Operations ────────────────────────────────────────────────────────────
BOME_BOM_CREATED = Counter(
    "bome_bom_created_total",
    "Liczba utworzonych BOM",
    ["bom_type"],
)

BOME_BOM_RELEASED = Counter(
    "bome_bom_released_total",
    "Liczba zwolnionych BOM",
    ["bom_type"],
)

BOME_BOM_STATUS_CHANGES = Counter(
    "bome_bom_status_changes_total",
    "Przejścia statusów BOM",
    ["from_status", "to_status"],
)

BOME_BOM_TOTAL = Gauge(
    "bome_bom_total",
    "Łączna liczba BOM per status",
    ["bom_type", "status"],
)

BOME_BOM_LINES_TOTAL = Gauge(
    "bome_bom_lines_total",
    "Łączna liczba pozycji BOM (active)",
)

# ── Cost Roll-up ──────────────────────────────────────────────────────────────
BOME_ROLLUP_DURATION = Histogram(
    "bome_cost_rollup_duration_seconds",
    "Czas obliczeń cost roll-up",
    ["bom_type"],
    buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)

BOME_ROLLUP_CONFIDENCE = Histogram(
    "bome_cost_rollup_confidence",
    "Dystrybucja rollup confidence",
    buckets=[0.5, 0.6, 0.7, 0.75, 0.80, 0.85, 0.90, 0.95, 1.0],
)

BOME_MISSING_PRICES = Gauge(
    "bome_missing_prices_total",
    "Liczba komponentów bez ceny w material master",
)

BOME_ZERO_COST_LINES = Counter(
    "bome_zero_cost_lines_total",
    "Pozycje BOM z kosztem ZERO (brak danych)",
)

# ── Tree Service ──────────────────────────────────────────────────────────────
BOME_TREE_BUILD_DURATION = Histogram(
    "bome_tree_build_duration_seconds",
    "Czas budowania drzewa BOM",
    ["expand_phantoms"],
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

BOME_TREE_MAX_DEPTH = Histogram(
    "bome_tree_depth",
    "Głębokość drzew BOM",
    buckets=[1, 2, 3, 4, 5, 7, 10, 15, 20],
)

BOME_TREE_NODE_COUNT = Histogram(
    "bome_tree_node_count",
    "Liczba węzłów w drzewie BOM",
    buckets=[10, 50, 100, 200, 500, 1000, 5000],
)

# ── Variants ──────────────────────────────────────────────────────────────────
BOME_VARIANT_CONFIGS = Counter(
    "bome_variant_configurations_total",
    "Liczba żądań konfiguracji wariantów",
    ["family_code"],
)

# ── Imports ───────────────────────────────────────────────────────────────────
BOME_IMPORT_DURATION = Histogram(
    "bome_import_duration_seconds",
    "Czas importu BOM",
    ["source"],  # TEAMCENTER / WINDCHILL / CSV
    buckets=[1, 5, 10, 30, 60, 120],
)

BOME_IMPORT_LINES = Histogram(
    "bome_import_lines_count",
    "Liczba zaimportowanych linii per import",
    ["source", "status"],
    buckets=[10, 50, 100, 500, 1000, 5000],
)

BOME_IMPORT_ERRORS = Counter(
    "bome_import_errors_total",
    "Błędy podczas importu BOM",
    ["source", "error_type"],
)

# ── Change Orders ─────────────────────────────────────────────────────────────
BOME_CHANGE_ORDERS_CREATED = Counter(
    "bome_change_orders_created_total",
    "Utworzone Change Orders",
    ["change_type", "risk_level"],
)

BOME_CHANGE_ORDERS_OPEN = Gauge(
    "bome_change_orders_open",
    "Otwarte Change Orders per status",
    ["change_type", "status"],
)

BOME_CHANGE_ORDER_CYCLE_TIME = Histogram(
    "bome_change_order_cycle_time_days",
    "Czas cyklu Change Order (created → closed) [dni]",
    ["change_type", "risk_level"],
    buckets=[1, 3, 7, 14, 30, 60, 90],
)

# ── Substitutions ─────────────────────────────────────────────────────────────
BOME_SUBSTITUTIONS_REQUESTED = Counter(
    "bome_substitutions_requested_total",
    "Wnioski o substytucję",
    ["reason"],
)

BOME_SUBSTITUTIONS_APPROVED = Counter(
    "bome_substitutions_approved_total",
    "Zatwierdzone substytucje",
    ["reason"],
)

BOME_SUBSTITUTION_COST_DELTA = Histogram(
    "bome_substitution_cost_delta_pct",
    "Zmiana kosztowa przy substytucji [%]",
    buckets=[-30, -20, -10, -5, 0, 5, 10, 20, 30],
)

# ── Validation ────────────────────────────────────────────────────────────────
BOME_VALIDATION_RUNS = Counter(
    "bome_validation_runs_total",
    "Uruchomienia walidatora BOM",
    ["target_status", "result"],  # result: passed / failed
)

BOME_VALIDATION_ISSUES = Counter(
    "bome_validation_issues_total",
    "Problemy wykryte przez walidator",
    ["code", "severity"],
)

# ── API ───────────────────────────────────────────────────────────────────────
BOME_API_LATENCY = Histogram(
    "bome_api_request_duration_seconds",
    "Latencja API endpointów",
    ["endpoint", "method", "status"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

BOME_WHERE_USED_LATENCY = Histogram(
    "bome_where_used_duration_seconds",
    "Czas zapytania where-used",
    buckets=[0.01, 0.05, 0.1, 0.5, 1.0, 5.0],
)

# ── Outbox ────────────────────────────────────────────────────────────────────
BOME_OUTBOX_LAG = Gauge(
    "bome_outbox_unpublished_count",
    "Nieopublikowane zdarzenia w outbox",
)
```

### 13.2 Dashboardy Grafana

| Dashboard | Panele |
|-----------|--------|
| **BOME Overview** | BOM total (per status/type), released today, open change orders, missing prices count |
| **Cost Roll-up** | Rollup confidence distribution, zero-cost lines trend, rollup duration p95, top-10 BOM by cost |
| **Structure Health** | BOM depth distribution, tree node count, circular reference alerts, phantom expansion rate |
| **Change Management** | Open ECO/MCO/DCO by risk, cycle time distribution, approval SLA heatmap, overdue changes |
| **Imports** | Import success rate, import duration, error breakdown by source, lines/import distribution |
| **Material Substitutions** | Requests submitted/approved/rejected, cost delta distribution, top substituted items |
| **API Performance** | P50/P95/P99 latency by endpoint, error rate, where-used query performance |
