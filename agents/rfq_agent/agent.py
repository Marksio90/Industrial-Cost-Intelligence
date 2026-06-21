"""
RFQ Agent — ReAct-style autonomous loop using Anthropic tool_use.

Architecture:
  1. User submits a procurement goal (natural language)
  2. Agent enters a Reason → Act → Observe loop:
       - LLM reasons about the next action
       - Agent executes a tool (discover_suppliers, generate_rfq, send_rfq_emails, ...)
       - Observation is appended to conversation history
  3. Loop continues until the LLM calls the `finish` tool or max_iterations
  4. All steps, events, and results are persisted to DB and published to Redis

Tool execution is gated by safety layer (rate limiter + compliance checker)
before any external communication.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import anthropic
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from .communication.email_sender import EmailSender
from .communication.response_monitor import InboundEmail, ResponseMonitor
from .config import AgentSettings
from .database.models import DiscoveredSupplierORM, ParsedQuoteORM, RFQJobORM
from .database.repository import AgentRepository
from .discovery.supplier_finder import SupplierFinder
from .events.publisher import EventPublisher, RFQEvents
from .generation.email_templates import EmailTemplateGenerator
from .generation.rfq_generator import RFQGenerator, RFQSpec
from .parsing.normalizer import NormalizedQuote, QuoteNormalizer
from .parsing.response_parser import ResponseParser
from .safety.anti_spam import AntiSpamChecker
from .safety.compliance import ComplianceChecker
from .safety.rate_limiter import RateLimiter
from .tools.registry import TOOL_SCHEMAS

log = structlog.get_logger(__name__)

_AGENT_SYSTEM = """You are an autonomous RFQ (Request for Quotation) procurement agent.
Your goal is to:
1. Understand the procurement need
2. Discover relevant suppliers (use discover_suppliers)
3. Generate a structured RFQ (use generate_rfq)
4. Send personalised RFQ emails to suppliers (use send_rfq_emails)
5. Monitor for and parse responses (use check_responses, parse_and_rank_quotes)
6. Deliver a ranked comparison of received quotes

Rules:
- Always discover suppliers before generating the RFQ
- Check compliance before sending any emails
- If fewer than 3 suppliers are found, try again with different keywords
- Never send more emails than allowed by rate limits
- If a supplier responds asking to stop contact, call blacklist_supplier
- When you have results or cannot proceed further, call finish

Think step-by-step. Use tools one at a time. Read each observation carefully before deciding the next step."""


class RFQAgent:
    """
    Autonomous RFQ agent driven by an Anthropic LLM with tool_use.

    Usage:
        agent = RFQAgent(settings, session, redis_client)
        result = await agent.run(
            goal="Buy 500 aluminium CNC machined brackets, ISO 9001 required, delivery by Q3",
            tenant_id="acme",
        )
    """

    def __init__(
        self,
        settings: AgentSettings,
        session: AsyncSession,
        redis_client: Any | None = None,
    ) -> None:
        self._settings = settings
        self._session = session

        # Infrastructure
        self._repo = AgentRepository(session)
        self._publisher = EventPublisher(redis_client, settings.event_channel_prefix) if redis_client else _NullPublisher()

        # Safety
        self._rate_limiter = RateLimiter(settings, redis_client)
        self._compliance = ComplianceChecker(settings)
        self._anti_spam = AntiSpamChecker()

        # Domain services
        self._rfq_gen = RFQGenerator(settings)
        self._email_gen = EmailTemplateGenerator(settings)
        self._email_sender = EmailSender(settings)
        self._parser = ResponseParser(settings)

        # LLM client
        self._llm = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )

        # Runtime state
        self._tenant_id: str = "default"
        self._run_id: uuid.UUID | None = None
        self._active_rfq: RFQSpec | None = None
        self._active_job: RFQJobORM | None = None
        self._sent_to: dict[str, str] = {}  # email → supplier_name
        self._inbound_emails: list[InboundEmail] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(self, goal: str, tenant_id: str = "default") -> dict[str, Any]:
        self._tenant_id = tenant_id
        run = await self._repo.create_run(tenant_id, goal)
        self._run_id = run.id
        await self._session.commit()

        await self._publisher.publish(
            RFQEvents.AGENT_STARTED,
            {"run_id": str(run.id), "goal": goal},
            tenant_id=tenant_id,
        )
        log.info("agent_run_start", run_id=str(run.id), goal=goal[:100])

        messages: list[dict[str, Any]] = [{"role": "user", "content": goal}]
        iteration = 0
        final_result: dict[str, Any] = {}

        try:
            while iteration < self._settings.max_iterations:
                iteration += 1
                log.info("agent_iteration", iteration=iteration, run_id=str(run.id))

                response = await self._llm.messages.create(
                    model=self._settings.llm_model,
                    max_tokens=self._settings.llm_max_tokens,
                    system=_AGENT_SYSTEM,
                    tools=TOOL_SCHEMAS,
                    messages=messages,
                )

                # Collect text content for logging
                text_parts = [b.text for b in response.content if hasattr(b, "text")]
                thought = " ".join(text_parts).strip()

                # Find tool use blocks
                tool_uses = [b for b in response.content if b.type == "tool_use"]

                if not tool_uses:
                    # LLM stopped without calling a tool
                    final_result = {"message": thought}
                    break

                # Execute tools sequentially (one per iteration)
                tool_results = []
                for tool_use in tool_uses:
                    tool_name = tool_use.name
                    tool_input = tool_use.input or {}

                    log.info("tool_call", tool=tool_name, iteration=iteration)

                    # Finish signal
                    if tool_name == "finish":
                        final_result = {
                            "summary": tool_input.get("summary", ""),
                            "result": tool_input.get("result", {}),
                        }
                        await self._finalise(run.id, "DONE", iteration, final_result)
                        return final_result

                    observation = await self._dispatch_tool(tool_name, tool_input)

                    await self._repo.save_step(
                        run_id=run.id,
                        iteration=iteration,
                        thought=thought or None,
                        action=tool_name,
                        action_input=tool_input,
                        observation=observation[:2000],
                    )
                    await self._repo.update_run(run.id, iterations=iteration)
                    await self._session.commit()

                    await self._publisher.publish(
                        RFQEvents.AGENT_STEP,
                        {"iteration": iteration, "tool": tool_name, "observation": observation[:500]},
                        tenant_id=tenant_id,
                        correlation_id=str(run.id),
                    )

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use.id,
                        "content": observation,
                    })

                # Append assistant message + tool results to history
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            # Max iterations reached
            final_result = {"message": "Max iterations reached", "iterations": iteration}
            await self._finalise(run.id, "DONE", iteration, final_result)

        except Exception as exc:
            log.exception("agent_run_error", run_id=str(run.id))
            await self._finalise(run.id, "FAILED", iteration, {}, error=str(exc))
            await self._publisher.publish(
                RFQEvents.AGENT_FAILED,
                {"run_id": str(run.id), "error": str(exc)},
                tenant_id=tenant_id,
            )
            raise

        return final_result

    # ── Tool dispatcher ────────────────────────────────────────────────────────

    async def _dispatch_tool(self, name: str, inputs: dict[str, Any]) -> str:
        match name:
            case "discover_suppliers":
                return await self._tool_discover_suppliers(**inputs)
            case "generate_rfq":
                return await self._tool_generate_rfq(**inputs)
            case "send_rfq_emails":
                return await self._tool_send_rfq_emails(**inputs)
            case "check_responses":
                return await self._tool_check_responses(**inputs)
            case "parse_and_rank_quotes":
                return await self._tool_parse_and_rank(**inputs)
            case "get_rfq_status":
                return await self._tool_get_rfq_status(**inputs)
            case "blacklist_supplier":
                return await self._tool_blacklist_supplier(**inputs)
            case _:
                return f"Unknown tool: {name}"

    # ── Tool implementations ───────────────────────────────────────────────────

    async def _tool_discover_suppliers(
        self,
        keywords: list[str],
        country_code: str | None = None,
        max_results: int = 10,
    ) -> str:
        finder = SupplierFinder(
            self._settings, self._repo, self._publisher, self._tenant_id
        )
        suppliers = await finder.find(keywords, country_code=country_code, max_per_source=max_results)

        if not suppliers:
            # Try DB fallback
            suppliers = await finder.find_from_db(keywords, country_code=country_code)

        await self._session.commit()

        if not suppliers:
            return "No suppliers found matching the given criteria. Try broader keywords."

        summary = [
            f"{i+1}. {s.name} <{s.email}> [{s.country_code}] caps={s.capabilities[:3]}"
            for i, s in enumerate(suppliers)
        ]
        return f"Discovered {len(suppliers)} suppliers:\n" + "\n".join(summary)

    async def _tool_generate_rfq(self, description: str) -> str:
        spec = await self._rfq_gen.from_natural_language(description, self._tenant_id)
        self._active_rfq = spec

        job = await self._repo.create_job(
            tenant_id=self._tenant_id,
            rfq_number=spec.rfq_number,
            title=spec.title,
            requirements=spec.to_dict(),
            run_id=self._run_id,
        )
        self._active_job = job
        await self._session.commit()

        await self._publisher.publish(
            RFQEvents.JOB_CREATED,
            {"rfq_number": spec.rfq_number, "title": spec.title},
            tenant_id=self._tenant_id,
        )

        return (
            f"RFQ generated:\n"
            f"  Number: {spec.rfq_number}\n"
            f"  Title: {spec.title}\n"
            f"  Quantity: {spec.quantity} {spec.unit}\n"
            f"  Delivery: {spec.delivery_date}\n"
            f"  Keywords: {spec.keywords}\n"
            f"  Specs: {spec.specifications[:200]}"
        )

    async def _tool_send_rfq_emails(
        self, rfq_number: str, supplier_emails: list[str]
    ) -> str:
        if self._active_rfq is None or self._active_rfq.rfq_number != rfq_number:
            return f"No active RFQ matches {rfq_number}. Generate it first."
        if self._active_job is None:
            return "No active job. Generate RFQ first."

        sent: list[str] = []
        skipped: list[str] = []

        for email_addr in supplier_emails:
            # Safety: rate limiting
            ok_global, msg_g = await self._rate_limiter.check_global()
            if not ok_global:
                skipped.append(f"{email_addr}: {msg_g}")
                await self._publisher.publish(
                    RFQEvents.RATE_LIMIT_HIT, {"email": email_addr, "reason": msg_g},
                    tenant_id=self._tenant_id,
                )
                continue

            ok_domain, msg_d = await self._rate_limiter.check_domain_daily(email_addr)
            if not ok_domain:
                skipped.append(f"{email_addr}: {msg_d}")
                continue

            # Safety: compliance
            compliance = self._compliance.check_recipient(email_addr)
            if not compliance.allowed:
                skipped.append(f"{email_addr}: compliance violation — {compliance.violations}")
                await self._publisher.publish(
                    RFQEvents.COMPLIANCE_VIOLATION,
                    {"email": email_addr, "violations": compliance.violations},
                    tenant_id=self._tenant_id,
                )
                continue

            # Generate personalised email
            supplier_name = self._sent_to.get(email_addr, email_addr.split("@")[0].title())
            try:
                email_content = await self._email_gen.generate(
                    rfq_number=rfq_number,
                    title=self._active_rfq.title,
                    supplier_name=supplier_name,
                    requirements=self._active_rfq.to_dict(),
                )
            except Exception as exc:
                skipped.append(f"{email_addr}: email generation failed — {exc}")
                continue

            # Safety: anti-spam
            spam_result = self._anti_spam.check(email_content.subject, email_content.body_text)
            if not spam_result.passed:
                skipped.append(f"{email_addr}: spam check failed (score={spam_result.score:.1f}) — {spam_result.flags}")
                continue

            # Send
            await self._rate_limiter.wait_for_interval()
            result = await self._email_sender.send(
                to_email=email_addr,
                to_name=supplier_name,
                subject=email_content.subject,
                body_html=email_content.body_html,
                body_text=email_content.body_text,
            )

            if result.success:
                await self._rate_limiter.record_send(email_addr)
                await self._repo.record_sent_email(
                    job_id=self._active_job.id,
                    supplier_name=supplier_name,
                    supplier_email=email_addr,
                    subject=email_content.subject,
                    body_html=email_content.body_html,
                    message_id=result.message_id,
                )
                await self._repo.increment_emails_sent(self._active_job.id)
                self._sent_to[email_addr] = supplier_name
                sent.append(email_addr)

                await self._publisher.publish(
                    RFQEvents.EMAIL_SENT,
                    {"email": email_addr, "rfq": rfq_number, "message_id": result.message_id},
                    tenant_id=self._tenant_id,
                )
            else:
                skipped.append(f"{email_addr}: send failed — {result.error}")
                await self._publisher.publish(
                    RFQEvents.EMAIL_FAILED,
                    {"email": email_addr, "error": result.error},
                    tenant_id=self._tenant_id,
                )

        await self._session.commit()

        lines = [f"Sent: {len(sent)}/{len(supplier_emails)} emails"]
        if sent:
            lines.append("Delivered to: " + ", ".join(sent))
        if skipped:
            lines.append("Skipped:")
            lines.extend(f"  - {s}" for s in skipped)
        return "\n".join(lines)

    async def _tool_check_responses(self, rfq_number: str) -> str:
        # In production this would call ResponseMonitor.poll()
        # Here we return buffered inbound emails matching the RFQ
        if not self._inbound_emails:
            return "No responses received yet. Supplier emails may take time to arrive."

        matching = [
            e for e in self._inbound_emails
            if rfq_number.lower() in (e.subject + e.body_text).lower()
        ]
        if not matching:
            return f"No responses found referencing {rfq_number}."

        lines = [f"Found {len(matching)} response(s) for {rfq_number}:"]
        for e in matching:
            lines.append(f"  From: {e.from_email} | Subject: {e.subject[:80]}")
        return "\n".join(lines)

    async def _tool_parse_and_rank(
        self, rfq_number: str, reference_price_eur: float | None = None
    ) -> str:
        if self._active_job is None:
            return "No active job found."

        quotes = await self._repo.list_quotes_for_job(self._active_job.id)
        if not quotes:
            return "No parsed quotes in database yet."

        ref = Decimal(str(reference_price_eur)) if reference_price_eur else None
        normalizer = QuoteNormalizer(reference_price_eur=ref)

        normalized: list[NormalizedQuote] = []
        for q in quotes:
            from .parsing.response_parser import ParsedQuote
            pq = ParsedQuote(
                unit_price=Decimal(str(q.unit_price)) if q.unit_price else None,
                currency=q.currency,
                quantity=None,
                lead_time_days=q.lead_time_days,
                validity_days=q.validity_days,
                incoterms=q.parsed_data.get("incoterms"),
                payment_terms=q.parsed_data.get("payment_terms"),
                certifications=q.parsed_data.get("certifications", []),
                notes=q.parsed_data.get("notes", ""),
                confidence=float(q.confidence or 0),
            )
            nq = normalizer.normalize(pq, q.supplier_email)
            normalized.append(nq)

        ranked = normalizer.rank(normalized)

        lines = [f"Ranked {len(ranked)} quotes for {rfq_number}:"]
        for i, nq in enumerate(ranked, 1):
            price_str = f"€{nq.unit_price_eur}" if nq.unit_price_eur else "N/A"
            lines.append(
                f"  #{i} {nq.supplier_email} | Price: {price_str} | "
                f"Lead: {nq.lead_time_days}d | Score: {nq.total_score:.2f}"
            )
        return "\n".join(lines)

    async def _tool_get_rfq_status(self, rfq_number: str) -> str:
        if self._active_job is None:
            return "No active job."
        job = await self._repo.get_job(self._active_job.id)
        if not job:
            return f"Job {rfq_number} not found."
        return (
            f"RFQ {rfq_number} status: {job.status}\n"
            f"  Emails sent: {job.emails_sent}\n"
            f"  Responses: {job.responses_received}"
        )

    async def _tool_blacklist_supplier(self, email: str, reason: str) -> str:
        domain = email.split("@")[-1] if "@" in email else email
        # Find all suppliers with this domain
        suppliers = await self._repo.list_suppliers(self._tenant_id, limit=100)
        count = 0
        for s in suppliers:
            if s.domain == domain:
                await self._repo.blacklist_supplier(s.id)
                count += 1
        await self._session.commit()
        await self._publisher.publish(
            RFQEvents.SUPPLIER_BLACKLISTED,
            {"domain": domain, "reason": reason},
            tenant_id=self._tenant_id,
        )
        return f"Blacklisted domain {domain} ({count} supplier records). Reason: {reason}"

    # ── Internal ───────────────────────────────────────────────────────────────

    async def _finalise(
        self,
        run_id: uuid.UUID,
        status: str,
        iterations: int,
        result: dict[str, Any],
        error: str | None = None,
    ) -> None:
        await self._repo.update_run(
            run_id, status=status, iterations=iterations,
            result=result, error=error,
        )
        await self._session.commit()

        await self._publisher.publish(
            RFQEvents.AGENT_COMPLETED if status == "DONE" else RFQEvents.AGENT_FAILED,
            {"run_id": str(run_id), "iterations": iterations, "status": status},
            tenant_id=self._tenant_id,
        )
        log.info("agent_run_finished", run_id=str(run_id), status=status, iterations=iterations)

    def inject_inbound_email(self, email: InboundEmail) -> None:
        """Called by ResponseMonitor when a new email arrives."""
        self._inbound_emails.append(email)


class _NullPublisher:
    async def publish(self, *args: Any, **kwargs: Any) -> None:
        pass
