from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

import structlog
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    AgentRunORM, AgentStepORM, DiscoveredSupplierORM,
    EmailRateLedgerORM, ParsedQuoteORM, RFQJobORM, SentEmailORM,
)

log = structlog.get_logger(__name__)


class AgentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    # ── Agent Runs ────────────────────────────────────────────────────────────

    async def create_run(self, tenant_id: str, goal: str) -> AgentRunORM:
        run = AgentRunORM(tenant_id=tenant_id, goal=goal, status="RUNNING")
        self._s.add(run)
        await self._s.flush()
        return run

    async def get_run(self, run_id: uuid.UUID) -> AgentRunORM | None:
        return await self._s.get(AgentRunORM, run_id)

    async def update_run(
        self,
        run_id: uuid.UUID,
        *,
        status: str | None = None,
        iterations: int | None = None,
        context: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        values: dict[str, Any] = {}
        if status is not None:
            values["status"] = status
        if iterations is not None:
            values["iterations"] = iterations
        if context is not None:
            values["context"] = context
        if result is not None:
            values["result"] = result
        if error is not None:
            values["error"] = error
        if status in ("DONE", "FAILED", "STOPPED"):
            values["finished_at"] = datetime.now(timezone.utc)
        if values:
            await self._s.execute(
                update(AgentRunORM).where(AgentRunORM.id == run_id).values(**values)
            )

    async def save_step(
        self,
        run_id: uuid.UUID,
        iteration: int,
        thought: str | None,
        action: str | None,
        action_input: dict[str, Any],
        observation: str | None,
    ) -> AgentStepORM:
        step = AgentStepORM(
            run_id=run_id, iteration=iteration, thought=thought,
            action=action, action_input=action_input, observation=observation,
        )
        self._s.add(step)
        await self._s.flush()
        return step

    # ── Supplier Discovery ────────────────────────────────────────────────────

    async def upsert_supplier(self, supplier: DiscoveredSupplierORM) -> DiscoveredSupplierORM:
        existing = None
        if supplier.email:
            result = await self._s.execute(
                select(DiscoveredSupplierORM).where(
                    DiscoveredSupplierORM.email == supplier.email,
                    DiscoveredSupplierORM.tenant_id == supplier.tenant_id,
                )
            )
            existing = result.scalar_one_or_none()
        if existing:
            existing.name = supplier.name
            existing.capabilities = supplier.capabilities
            existing.metadata = supplier.metadata
            return existing
        self._s.add(supplier)
        await self._s.flush()
        return supplier

    async def list_suppliers(
        self,
        tenant_id: str,
        *,
        keyword: str | None = None,
        country_code: str | None = None,
        limit: int = 50,
    ) -> list[DiscoveredSupplierORM]:
        q = select(DiscoveredSupplierORM).where(
            DiscoveredSupplierORM.tenant_id == tenant_id,
            DiscoveredSupplierORM.blacklisted.is_(False),
            DiscoveredSupplierORM.email.isnot(None),
        )
        if country_code:
            q = q.where(DiscoveredSupplierORM.country_code == country_code)
        if keyword:
            q = q.where(
                DiscoveredSupplierORM.name.ilike(f"%{keyword}%")
            )
        q = q.limit(limit)
        result = await self._s.execute(q)
        return list(result.scalars().all())

    async def blacklist_supplier(self, supplier_id: uuid.UUID) -> None:
        await self._s.execute(
            update(DiscoveredSupplierORM)
            .where(DiscoveredSupplierORM.id == supplier_id)
            .values(blacklisted=True)
        )

    # ── RFQ Jobs ──────────────────────────────────────────────────────────────

    async def create_job(
        self,
        tenant_id: str,
        rfq_number: str,
        title: str,
        requirements: dict[str, Any],
        run_id: uuid.UUID | None = None,
        deadline: datetime | None = None,
    ) -> RFQJobORM:
        job = RFQJobORM(
            tenant_id=tenant_id, rfq_number=rfq_number, title=title,
            requirements=requirements, run_id=run_id, deadline=deadline,
        )
        self._s.add(job)
        await self._s.flush()
        return job

    async def get_job(self, job_id: uuid.UUID) -> RFQJobORM | None:
        return await self._s.get(RFQJobORM, job_id)

    async def update_job_status(self, job_id: uuid.UUID, status: str) -> None:
        await self._s.execute(
            update(RFQJobORM).where(RFQJobORM.id == job_id).values(status=status)
        )

    async def increment_emails_sent(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(RFQJobORM)
            .where(RFQJobORM.id == job_id)
            .values(emails_sent=RFQJobORM.emails_sent + 1)
        )

    async def increment_responses(self, job_id: uuid.UUID) -> None:
        await self._s.execute(
            update(RFQJobORM)
            .where(RFQJobORM.id == job_id)
            .values(responses_received=RFQJobORM.responses_received + 1)
        )

    # ── Sent Emails ───────────────────────────────────────────────────────────

    async def record_sent_email(
        self,
        job_id: uuid.UUID,
        supplier_name: str,
        supplier_email: str,
        subject: str,
        body_html: str,
        message_id: str | None = None,
    ) -> SentEmailORM:
        record = SentEmailORM(
            job_id=job_id, supplier_name=supplier_name, supplier_email=supplier_email,
            subject=subject, body_html=body_html, message_id=message_id,
        )
        self._s.add(record)
        await self._s.flush()
        return record

    # ── Parsed Quotes ─────────────────────────────────────────────────────────

    async def save_parsed_quote(self, quote: ParsedQuoteORM) -> ParsedQuoteORM:
        self._s.add(quote)
        await self._s.flush()
        return quote

    async def list_quotes_for_job(self, job_id: uuid.UUID) -> list[ParsedQuoteORM]:
        result = await self._s.execute(
            select(ParsedQuoteORM)
            .where(ParsedQuoteORM.job_id == job_id)
            .order_by(ParsedQuoteORM.unit_price.asc().nulls_last())
        )
        return list(result.scalars().all())

    # ── Rate Ledger ───────────────────────────────────────────────────────────

    async def get_daily_email_count(self, domain: str) -> int:
        today = date.today().isoformat()
        result = await self._s.execute(
            select(EmailRateLedgerORM.count).where(
                EmailRateLedgerORM.domain == domain,
                EmailRateLedgerORM.date == today,
            )
        )
        return result.scalar_one_or_none() or 0

    async def increment_daily_email_count(self, domain: str) -> None:
        today = date.today().isoformat()
        result = await self._s.execute(
            select(EmailRateLedgerORM).where(
                EmailRateLedgerORM.domain == domain,
                EmailRateLedgerORM.date == today,
            )
        )
        ledger = result.scalar_one_or_none()
        if ledger:
            ledger.count += 1
        else:
            self._s.add(EmailRateLedgerORM(domain=domain, date=today, count=1))
        await self._s.flush()
