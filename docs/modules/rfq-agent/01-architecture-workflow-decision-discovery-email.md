# RFQ Agent — Architecture, Workflow, Decision Engine, Supplier Discovery, Email Generation

## 1. Agent Architecture

### 1.1 Przegląd systemu

RFQ Agent (RFQA) to autonomiczny agent AI sterowany zdarzeniami, zaprojektowany
do przeprowadzenia pełnego cyklu zapytania ofertowego: od wygenerowania RFQ,
przez wysyłkę email do dostawców, scraping danych, aż po analizę odpowiedzi,
normalizację ofert i aktualizację bazy cen — z kontrolowanym udziałem człowieka
w punktach decyzyjnych wysokiego ryzyka.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RFQ Agent System                             │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────────────────────────────┐  │
│  │  Trigger     │    │          Agent Core (LLM-powered)        │  │
│  │  Sources     │    │                                          │  │
│  │              │    │  ┌──────────┐    ┌───────────────────┐  │  │
│  │ • RFQ Engine │───►│  │Planner   │───►│  Tool Executor    │  │  │
│  │ • Scheduler  │    │  │(ReAct)   │    │                   │  │  │
│  │ • Procurement│    │  └──────────┘    │ • email_send      │  │  │
│  │   Portal     │    │       ▲          │ • supplier_search  │  │  │
│  │ • API call   │    │       │          │ • web_scrape      │  │  │
│  └──────────────┘    │  ┌────┴─────┐   │ • parse_response   │  │  │
│                      │  │ Memory   │   │ • compare_offers   │  │  │
│                      │  │ • STM    │   │ • db_write        │  │  │
│                      │  │ • LTM    │   │ • hitl_request    │  │  │
│                      │  │ • Episodic│  └───────────────────┘  │  │
│                      │  └──────────┘                          │  │
│                      └──────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                    Supporting Services                        │  │
│  │  SupplierDiscovery │ EmailEngine │ ResponseParser │ Comparator│  │
│  │  RiskController    │ PriceUpdater│ HITLGateway    │ AuditLog  │  │
│  └──────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Wzorzec agenta: ReAct + Tool Use

Agent używa wzorca **ReAct** (Reasoning + Acting) z pętlą:

```
Thought → Action → Observation → Thought → ...
```

Każda iteracja jest logowana do `rfqa.agent_traces` i może być przerwana
przez `RiskController` gdy osiągnie próg ryzyka lub czeka na decyzję HITL.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import uuid
from datetime import datetime, timezone

class AgentStatus(Enum):
    IDLE          = "IDLE"
    PLANNING      = "PLANNING"
    EXECUTING     = "EXECUTING"
    AWAITING_HITL = "AWAITING_HITL"
    PAUSED        = "PAUSED"
    COMPLETED     = "COMPLETED"
    FAILED        = "FAILED"

class ToolName(Enum):
    EMAIL_SEND          = "email_send"
    EMAIL_READ          = "email_read"
    SUPPLIER_SEARCH     = "supplier_search"
    WEB_SCRAPE          = "web_scrape"
    PARSE_RESPONSE      = "parse_response"
    COMPARE_OFFERS      = "compare_offers"
    NORMALIZE_OFFER     = "normalize_offer"
    UPDATE_PRICE_DB     = "update_price_db"
    REQUEST_HITL        = "request_hitl"
    SEND_SLACK          = "send_slack"
    QUERY_CEE           = "query_cee"
    QUERY_SCSE          = "query_scse"
    SEARCH_CHE          = "search_che"

@dataclass
class AgentMemory:
    """Short-Term + Long-Term + Episodic memory."""
    # Short-term: current RFQ cycle context
    current_rfq_id:       str | None = None
    active_suppliers:     list[dict] = field(default_factory=list)
    sent_emails:          list[str]  = field(default_factory=list)
    received_responses:   list[dict] = field(default_factory=list)
    normalized_offers:    list[dict] = field(default_factory=list)
    # Long-term: persisted knowledge (loaded from DB at startup)
    supplier_blacklist:   set[str]   = field(default_factory=set)
    preferred_suppliers:  dict[str, float] = field(default_factory=dict)   # supplier_id → score
    material_price_index: dict[str, float] = field(default_factory=dict)
    # Episodic: past RFQ summaries for learning
    past_rfq_outcomes:    list[dict] = field(default_factory=list)

@dataclass
class AgentTrace:
    trace_id:    str = field(default_factory=lambda: str(uuid.uuid4()))
    rfq_id:      str | None = None
    step:        int = 0
    thought:     str = ""
    action:      str = ""
    tool:        ToolName | None = None
    tool_input:  dict = field(default_factory=dict)
    observation: str = ""
    timestamp:   datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    tokens_used: int = 0
    latency_ms:  int = 0

@dataclass
class RFQAgentConfig:
    model:              str   = "claude-opus-4-8"
    max_iterations:     int   = 50
    max_suppliers:      int   = 10
    email_timeout_days: int   = 5
    auto_select_threshold: float = 0.80    # confidence above which agent auto-selects
    risk_score_threshold:  float = 0.65    # above → mandatory HITL
    max_spend_eur_auto:    float = 50_000  # above → HITL required
    spam_cooldown_days:    int   = 30
    scrape_concurrency:    int   = 5
    temperature:           float = 0.1     # Low: deterministic procurement decisions
```

### 1.3 Agent Core

```python
import asyncio
import anthropic
import json
from typing import AsyncIterator

class RFQAgent:
    """
    Autonomous RFQ Agent using Claude claude-opus-4-8 with tool use.
    Orchestrates the full RFQ lifecycle with risk controls and HITL gates.
    """

    SYSTEM_PROMPT = """You are an autonomous procurement agent responsible for
managing Request for Quotation (RFQ) cycles for industrial components.

Your objective: obtain the best quality-adjusted price from verified suppliers
within the specified timeline, while controlling risk and flagging anomalies.

Principles:
1. ACCURACY: Never fabricate supplier data or prices — always verify via tools
2. RISK-FIRST: When uncertain, escalate to human review (request_hitl tool)
3. AUDITABILITY: Every decision must have a documented reason in your thoughts
4. EFFICIENCY: Batch similar requests; avoid redundant tool calls
5. ANTI-SPAM: Never contact the same supplier more than once per 30 days for
   the same material unless explicitly authorized

You have access to tools. Use them step by step. Think before each action."""

    def __init__(
        self,
        config:  RFQAgentConfig,
        tools:   "ToolRegistry",
        memory:  AgentMemory,
        db:      "asyncpg.Pool",
        llm:     anthropic.AsyncAnthropic,
    ):
        self._config  = config
        self._tools   = tools
        self._memory  = memory
        self._db      = db
        self._llm     = llm

    async def run(self, rfq_request: "RFQRequest") -> "RFQResult":
        """Main agent loop: ReAct until completion, HITL, or failure."""
        self._memory.current_rfq_id = rfq_request.rfq_id
        traces:   list[AgentTrace] = []
        messages: list[dict]       = [
            {"role": "user", "content": self._build_initial_prompt(rfq_request)}
        ]

        for iteration in range(self._config.max_iterations):
            trace = AgentTrace(rfq_id=rfq_request.rfq_id, step=iteration)
            t0    = asyncio.get_event_loop().time()

            response = await self._llm.messages.create(
                model=self._config.model,
                max_tokens=4096,
                system=self.SYSTEM_PROMPT,
                tools=self._tools.to_anthropic_schema(),
                messages=messages,
                temperature=self._config.temperature,
            )

            trace.latency_ms = int((asyncio.get_event_loop().time() - t0) * 1000)
            trace.tokens_used = response.usage.input_tokens + response.usage.output_tokens

            if response.stop_reason == "end_turn":
                final_text = next(
                    (b.text for b in response.content if hasattr(b, "text")), ""
                )
                await self._persist_traces(traces)
                return await self._build_result(rfq_request, final_text)

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    tool_name = ToolName(block.name)
                    trace.tool       = tool_name
                    trace.tool_input = block.input

                    # Risk gate before execution
                    risk = await self._tools.risk_check(tool_name, block.input)
                    if risk.requires_hitl:
                        await self._request_hitl(rfq_request, risk, traces)
                        return RFQResult(status="AWAITING_HITL", rfq_id=rfq_request.rfq_id)

                    result = await self._tools.execute(tool_name, block.input)
                    trace.observation = json.dumps(result)[:2000]

                    tool_results.append({
                        "type":        "tool_result",
                        "tool_use_id": block.id,
                        "content":     json.dumps(result),
                    })

                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user",      "content": tool_results})
                traces.append(trace)

        await self._persist_traces(traces)
        raise RuntimeError(f"RFQ {rfq_request.rfq_id}: max_iterations={self._config.max_iterations} reached")

    def _build_initial_prompt(self, req: "RFQRequest") -> str:
        return f"""New RFQ cycle initiated.

RFQ ID: {req.rfq_id}
Product: {req.product_name}
Material: {req.material_code} ({req.material_description})
Quantity: {req.quantity} {req.unit}
Required delivery: {req.required_delivery_date}
Target price (CEE estimate): {req.target_price_eur:.2f} EUR/unit
Production location preference: {req.preferred_location}
Certifications required: {', '.join(req.required_certifications)}
Special requirements: {req.special_requirements or 'None'}
Deadline for quotes: {req.quote_deadline}
Budget limit: {req.budget_limit_eur:.2f} EUR total

Steps to complete:
1. Search for qualified suppliers (use supplier_search + web_scrape)
2. Generate and send RFQ emails to top suppliers (use email_send)
3. Wait for responses or scrape supplier portals (use web_scrape + email_read)
4. Parse and normalize all offers (use parse_response + normalize_offer)
5. Compare offers against target price and rank (use compare_offers)
6. Update price database with new data (use update_price_db)
7. Request human review if required (use request_hitl)
8. Return final recommendation with reasoning

Begin now."""

    async def _persist_traces(self, traces: list[AgentTrace]) -> None:
        async with self._db.acquire() as conn:
            await conn.executemany(
                """INSERT INTO rfqa.agent_traces
                   (trace_id, rfq_id, step, thought, action, tool_name,
                    tool_input, observation, tokens_used, latency_ms, created_at)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
                [
                    (t.trace_id, t.rfq_id, t.step, t.thought, t.action,
                     t.tool.value if t.tool else None,
                     json.dumps(t.tool_input), t.observation,
                     t.tokens_used, t.latency_ms, t.timestamp)
                    for t in traces
                ],
            )
```

### 1.4 Tool Registry

```python
from typing import Callable, Awaitable
import anthropic

@dataclass
class ToolSchema:
    name:        str
    description: str
    input_schema: dict

class ToolRegistry:
    """Registry of all agent tools with risk metadata."""

    RISK_LEVELS: dict[ToolName, str] = {
        ToolName.EMAIL_SEND:      "HIGH",     # Exteranlly visible action
        ToolName.WEB_SCRAPE:      "LOW",
        ToolName.SUPPLIER_SEARCH: "LOW",
        ToolName.PARSE_RESPONSE:  "LOW",
        ToolName.NORMALIZE_OFFER: "LOW",
        ToolName.COMPARE_OFFERS:  "LOW",
        ToolName.UPDATE_PRICE_DB: "MEDIUM",
        ToolName.REQUEST_HITL:    "LOW",
        ToolName.QUERY_CEE:       "LOW",
        ToolName.QUERY_SCSE:      "LOW",
        ToolName.SEARCH_CHE:      "LOW",
        ToolName.SEND_SLACK:      "MEDIUM",
    }

    def __init__(self):
        self._handlers: dict[ToolName, Callable] = {}
        self._schemas:  dict[ToolName, ToolSchema] = {}

    def register(self, tool: ToolName, handler: Callable, schema: ToolSchema) -> None:
        self._handlers[tool] = handler
        self._schemas[tool]  = schema

    async def execute(self, tool: ToolName, inputs: dict) -> dict:
        handler = self._handlers[tool]
        return await handler(**inputs)

    async def risk_check(self, tool: ToolName, inputs: dict) -> "RiskCheck":
        level = self.RISK_LEVELS.get(tool, "MEDIUM")
        requires_hitl = False
        reason = ""
        if tool == ToolName.EMAIL_SEND:
            if inputs.get("recipient_count", 1) > 5:
                requires_hitl = True
                reason = f"Bulk email to {inputs['recipient_count']} suppliers requires approval"
            elif inputs.get("total_value_eur", 0) > 50_000:
                requires_hitl = True
                reason = f"RFQ value > €50K requires human approval"
        return RiskCheck(level=level, requires_hitl=requires_hitl, reason=reason)

    def to_anthropic_schema(self) -> list[dict]:
        return [
            {
                "name":         s.name,
                "description":  s.description,
                "input_schema": s.input_schema,
            }
            for s in self._schemas.values()
        ]
```

---

## 2. Workflow Design

### 2.1 Cykl życia RFQ

```
┌─────────────────────────────────────────────────────────────────────┐
│                        RFQ Lifecycle                                │
│                                                                     │
│  TRIGGER ──► SUPPLIER_DISCOVERY ──► RFQ_GENERATION                 │
│                     │                      │                        │
│                     ▼                      ▼                        │
│             [HITL: approve list?]   EMAIL_DISPATCH                  │
│                     │                      │                        │
│                     ▼                      ▼                        │
│             AWAITING_RESPONSES      SCRAPING_PORTAL                 │
│                     │                      │                        │
│                     └──────────┬───────────┘                        │
│                                ▼                                    │
│                       RESPONSE_PARSING                              │
│                                │                                    │
│                                ▼                                    │
│                      OFFER_NORMALIZATION                            │
│                                │                                    │
│                                ▼                                    │
│                       OFFER_COMPARISON                              │
│                                │                                    │
│                    ┌───────────┴───────────┐                        │
│                    ▼                       ▼                        │
│            confidence ≥ 0.80        confidence < 0.80              │
│            spend ≤ €50K             OR spend > €50K                 │
│                    │                       │                        │
│                    ▼                       ▼                        │
│            AUTO_RECOMMEND          [HITL: approve winner?]          │
│                    │                       │                        │
│                    └──────────┬────────────┘                        │
│                               ▼                                     │
│                       PRICE_DB_UPDATE                               │
│                               │                                     │
│                               ▼                                     │
│                           COMPLETED                                 │
└─────────────────────────────────────────────────────────────────────┘
```

### 2.2 Stany i przejścia

```python
from enum import Enum
from typing import TYPE_CHECKING

class RFQCycleState(Enum):
    DRAFT              = "DRAFT"
    SUPPLIER_DISCOVERY = "SUPPLIER_DISCOVERY"
    AWAITING_HITL      = "AWAITING_HITL"
    RFQ_GENERATION     = "RFQ_GENERATION"
    EMAIL_DISPATCH     = "EMAIL_DISPATCH"
    AWAITING_RESPONSES = "AWAITING_RESPONSES"
    SCRAPING_PORTALS   = "SCRAPING_PORTALS"
    RESPONSE_PARSING   = "RESPONSE_PARSING"
    OFFER_NORMALIZATION= "OFFER_NORMALIZATION"
    OFFER_COMPARISON   = "OFFER_COMPARISON"
    RECOMMENDATION     = "RECOMMENDATION"
    PRICE_DB_UPDATE    = "PRICE_DB_UPDATE"
    COMPLETED          = "COMPLETED"
    CANCELLED          = "CANCELLED"
    FAILED             = "FAILED"

VALID_TRANSITIONS: dict[RFQCycleState, list[RFQCycleState]] = {
    RFQCycleState.DRAFT:              [RFQCycleState.SUPPLIER_DISCOVERY],
    RFQCycleState.SUPPLIER_DISCOVERY: [RFQCycleState.AWAITING_HITL, RFQCycleState.RFQ_GENERATION],
    RFQCycleState.AWAITING_HITL:      [RFQCycleState.RFQ_GENERATION, RFQCycleState.CANCELLED,
                                       RFQCycleState.RECOMMENDATION],
    RFQCycleState.RFQ_GENERATION:     [RFQCycleState.EMAIL_DISPATCH],
    RFQCycleState.EMAIL_DISPATCH:     [RFQCycleState.AWAITING_RESPONSES, RFQCycleState.SCRAPING_PORTALS],
    RFQCycleState.AWAITING_RESPONSES: [RFQCycleState.RESPONSE_PARSING, RFQCycleState.SCRAPING_PORTALS],
    RFQCycleState.SCRAPING_PORTALS:   [RFQCycleState.RESPONSE_PARSING],
    RFQCycleState.RESPONSE_PARSING:   [RFQCycleState.OFFER_NORMALIZATION],
    RFQCycleState.OFFER_NORMALIZATION:[RFQCycleState.OFFER_COMPARISON],
    RFQCycleState.OFFER_COMPARISON:   [RFQCycleState.AWAITING_HITL, RFQCycleState.RECOMMENDATION],
    RFQCycleState.RECOMMENDATION:     [RFQCycleState.PRICE_DB_UPDATE],
    RFQCycleState.PRICE_DB_UPDATE:    [RFQCycleState.COMPLETED],
}

@dataclass
class RFQRequest:
    rfq_id:                  str
    product_name:            str
    material_code:           str
    material_description:    str
    quantity:                float
    unit:                    str
    required_delivery_date:  str
    target_price_eur:        float
    budget_limit_eur:        float
    preferred_location:      str
    required_certifications: list[str]
    quote_deadline:          str
    special_requirements:    str | None = None
    requestor_id:            str | None = None

@dataclass
class RFQResult:
    rfq_id:           str
    status:           str
    winner_offer:     dict | None = None
    runner_up:        dict | None = None
    all_offers:       list[dict] = field(default_factory=list)
    recommendation:   str = ""
    confidence_score: float = 0.0
    savings_eur:      float = 0.0
    savings_pct:      float = 0.0
    price_updated:    bool = False
    traces_count:     int  = 0
```

### 2.3 Orkiestrator cyklu

```python
import asyncio
from datetime import datetime, timezone, timedelta

class RFQCycleOrchestrator:
    """
    Manages the state machine for an RFQ cycle.
    Handles timeouts, retries, and lifecycle events.
    """

    RESPONSE_TIMEOUT_DAYS = 5

    def __init__(
        self,
        agent:      RFQAgent,
        db:         "asyncpg.Pool",
        kafka:      "AIOKafkaProducer",
        scheduler:  "AsyncIOScheduler",
    ):
        self._agent     = agent
        self._db        = db
        self._kafka     = kafka
        self._scheduler = scheduler

    async def start_cycle(self, request: RFQRequest) -> str:
        """Initialize DB record, enqueue agent task, return rfq_id."""
        await self._persist_cycle(request, RFQCycleState.DRAFT)
        await self._transition(request.rfq_id, RFQCycleState.SUPPLIER_DISCOVERY)

        # Schedule response deadline check
        deadline = datetime.now(timezone.utc) + timedelta(days=self.RESPONSE_TIMEOUT_DAYS)
        self._scheduler.add_job(
            self._check_response_deadline,
            trigger="date",
            run_date=deadline,
            args=[request.rfq_id],
            id=f"rfq_deadline_{request.rfq_id}",
        )

        asyncio.create_task(self._run_agent(request))
        return request.rfq_id

    async def _run_agent(self, request: RFQRequest) -> None:
        try:
            result = await self._agent.run(request)
            await self._transition(request.rfq_id, RFQCycleState.COMPLETED)
            await self._emit_event("rfqa.cycle.completed", request.rfq_id, result.__dict__)
        except Exception as exc:
            await self._transition(request.rfq_id, RFQCycleState.FAILED)
            await self._emit_event("rfqa.cycle.failed", request.rfq_id, {"error": str(exc)})
            raise

    async def _transition(self, rfq_id: str, new_state: RFQCycleState) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                "UPDATE rfqa.rfq_cycles SET state = $1, updated_at = now() WHERE rfq_id = $2",
                new_state.value, rfq_id,
            )
        await self._emit_event("rfqa.state.changed", rfq_id, {"new_state": new_state.value})

    async def _emit_event(self, topic: str, rfq_id: str, payload: dict) -> None:
        import json
        await self._kafka.send(
            topic,
            key=rfq_id.encode(),
            value=json.dumps({"rfq_id": rfq_id, **payload}).encode(),
        )
```

---

## 3. Decision Engine

### 3.1 Logika wyboru dostawcy

```python
import numpy as np
from dataclasses import dataclass

@dataclass
class OfferScore:
    supplier_id:     str
    supplier_name:   str
    unit_price_eur:  float
    total_price_eur: float
    delivery_days:   int
    certifications:  list[str]
    quality_score:   float       # 0–1 (from SIE scorecard)
    risk_score:      float       # 0–1 (higher = riskier)
    price_score:     float = 0.0
    delivery_score:  float = 0.0
    composite_score: float = 0.0
    rank:            int   = 0
    recommendation:  str   = ""
    auto_eligible:   bool  = False

SCORING_WEIGHTS = {
    "price":         0.45,
    "quality":       0.25,
    "delivery":      0.15,
    "certifications":0.10,
    "relationship":  0.05,
}

class DecisionEngine:
    """
    Multi-criteria decision analysis (MCDA) for offer ranking.
    Uses weighted scoring with normalization and anomaly detection.
    """

    def __init__(self, config: RFQAgentConfig, db: "asyncpg.Pool"):
        self._config = config
        self._db     = db

    async def score_offers(
        self,
        offers:          list["NormalizedOffer"],
        target_price:    float,
        required_certs:  list[str],
        required_delivery_days: int,
    ) -> list[OfferScore]:
        if not offers:
            return []

        prices    = np.array([o.unit_price_eur for o in offers])
        deliveries= np.array([o.delivery_days  for o in offers])

        # Load supplier quality scores from SIE
        quality_scores = await self._load_quality_scores([o.supplier_id for o in offers])

        scored = []
        for i, offer in enumerate(offers):
            # Price score: inverse normalized (lower price = higher score)
            # Penalize if > 20% above target
            price_ratio  = offer.unit_price_eur / max(target_price, 0.01)
            price_score  = max(0.0, 1.0 - (price_ratio - 0.80) / 0.80)
            price_score  = float(np.clip(price_score, 0.0, 1.0))

            # Delivery score: normalized against required deadline
            delivery_ratio = offer.delivery_days / max(required_delivery_days, 1)
            delivery_score = max(0.0, 1.0 - (delivery_ratio - 0.80) / 0.80)
            delivery_score = float(np.clip(delivery_score, 0.0, 1.0))

            # Certification score
            missing_certs  = set(required_certs) - set(offer.certifications or [])
            cert_score     = 1.0 - len(missing_certs) / max(len(required_certs), 1)

            # Quality from SIE (0–1)
            quality = quality_scores.get(offer.supplier_id, 0.70)

            # Relationship score (past transactions)
            relationship = await self._get_relationship_score(offer.supplier_id)

            # Composite
            composite = (
                price_score       * SCORING_WEIGHTS["price"]         +
                quality           * SCORING_WEIGHTS["quality"]        +
                delivery_score    * SCORING_WEIGHTS["delivery"]       +
                cert_score        * SCORING_WEIGHTS["certifications"] +
                relationship      * SCORING_WEIGHTS["relationship"]
            )

            # Auto-eligible: high confidence, no missing certs, within budget
            auto_eligible = (
                composite >= self._config.auto_select_threshold
                and len(missing_certs) == 0
                and offer.total_price_eur <= self._config.max_spend_eur_auto
                and offer.risk_flags == []
            )

            scored.append(OfferScore(
                supplier_id      = offer.supplier_id,
                supplier_name    = offer.supplier_name,
                unit_price_eur   = offer.unit_price_eur,
                total_price_eur  = offer.total_price_eur,
                delivery_days    = offer.delivery_days,
                certifications   = offer.certifications or [],
                quality_score    = quality,
                risk_score       = offer.risk_score or 0.0,
                price_score      = price_score,
                delivery_score   = delivery_score,
                composite_score  = composite,
                auto_eligible    = auto_eligible,
            ))

        # Rank by composite
        scored.sort(key=lambda x: x.composite_score, reverse=True)
        for i, s in enumerate(scored):
            s.rank = i + 1
            s.recommendation = self._build_recommendation(s, target_price, required_certs)

        return scored

    def _build_recommendation(
        self, score: OfferScore, target: float, req_certs: list[str]
    ) -> str:
        parts = []
        diff  = (score.unit_price_eur - target) / max(target, 0.01) * 100
        if diff < 0:
            parts.append(f"Price {abs(diff):.1f}% below target")
        else:
            parts.append(f"Price {diff:.1f}% above target")
        missing = set(req_certs) - set(score.certifications)
        if missing:
            parts.append(f"Missing certs: {', '.join(missing)}")
        if score.risk_score > 0.5:
            parts.append("Elevated risk score — verify")
        return "; ".join(parts)

    async def _load_quality_scores(self, supplier_ids: list[str]) -> dict[str, float]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                "SELECT supplier_id, overall_score FROM rfqa.supplier_profiles "
                "WHERE supplier_id = ANY($1::uuid[])",
                supplier_ids,
            )
        return {str(r["supplier_id"]): float(r["overall_score"]) for r in rows}

    async def _get_relationship_score(self, supplier_id: str) -> float:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT AVG(quality_rating) AS avg_q, COUNT(*) AS n
                   FROM rfqa.past_transactions
                   WHERE supplier_id = $1 AND transaction_date > now() - INTERVAL '2 years'""",
                supplier_id,
            )
        if not row or not row["n"]:
            return 0.50    # No history: neutral
        return float(np.clip(row["avg_q"] or 0.5, 0.0, 1.0))
```

### 3.2 Reguły decyzyjne

```python
from enum import Enum

class DecisionOutcome(Enum):
    AUTO_SELECT      = "AUTO_SELECT"
    RECOMMEND_HITL   = "RECOMMEND_HITL"
    REJECT_ALL       = "REJECT_ALL"
    INSUFFICIENT     = "INSUFFICIENT"

@dataclass
class DecisionResult:
    outcome:          DecisionOutcome
    winner:           OfferScore | None
    runner_up:        OfferScore | None
    hitl_reason:      str | None
    savings_eur:      float
    savings_pct:      float
    confidence:       float

class DecisionRuleEngine:
    """Rule-based post-scoring decision logic."""

    MIN_OFFERS_FOR_AUTO = 2
    MAX_PRICE_DEVIATION = 0.30      # Reject if > 30% above target

    def decide(
        self,
        scores:       list[OfferScore],
        target_price: float,
        budget:       float,
        config:       RFQAgentConfig,
    ) -> DecisionResult:
        if len(scores) == 0:
            return DecisionResult(
                outcome=DecisionOutcome.INSUFFICIENT,
                winner=None, runner_up=None,
                hitl_reason="No offers received",
                savings_eur=0, savings_pct=0, confidence=0,
            )

        winner = scores[0]
        runner = scores[1] if len(scores) > 1 else None

        # Rule 1: price deviation too high
        if winner.unit_price_eur > target_price * (1 + self.MAX_PRICE_DEVIATION):
            return DecisionResult(
                outcome=DecisionOutcome.RECOMMEND_HITL,
                winner=winner, runner_up=runner,
                hitl_reason=f"Best price {winner.unit_price_eur:.2f} EUR > "
                            f"target+30% ({target_price * 1.3:.2f} EUR)",
                savings_eur=0, savings_pct=0, confidence=0.3,
            )

        # Rule 2: budget exceeded
        if winner.total_price_eur > budget:
            return DecisionResult(
                outcome=DecisionOutcome.RECOMMEND_HITL,
                winner=winner, runner_up=runner,
                hitl_reason=f"Best offer {winner.total_price_eur:.2f} EUR exceeds budget {budget:.2f} EUR",
                savings_eur=0, savings_pct=0, confidence=0.4,
            )

        # Rule 3: auto-select if eligible and enough competing offers
        if winner.auto_eligible and len(scores) >= self.MIN_OFFERS_FOR_AUTO:
            savings_eur = (target_price - winner.unit_price_eur) * (winner.total_price_eur / winner.unit_price_eur)
            savings_pct = (target_price - winner.unit_price_eur) / target_price * 100
            return DecisionResult(
                outcome=DecisionOutcome.AUTO_SELECT,
                winner=winner, runner_up=runner,
                hitl_reason=None,
                savings_eur=round(savings_eur, 2),
                savings_pct=round(savings_pct, 2),
                confidence=winner.composite_score,
            )

        # Rule 4: fallback to HITL
        return DecisionResult(
            outcome=DecisionOutcome.RECOMMEND_HITL,
            winner=winner, runner_up=runner,
            hitl_reason=f"Confidence {winner.composite_score:.2f} below threshold "
                       f"{config.auto_select_threshold} or insufficient competing offers",
            savings_eur=0, savings_pct=0,
            confidence=winner.composite_score,
        )
```

---

## 4. Supplier Discovery

### 4.1 Strategie odkrywania dostawców

```python
from enum import Enum
from dataclasses import dataclass

class DiscoverySource(Enum):
    INTERNAL_DB    = "INTERNAL_DB"     # rfqa.supplier_profiles
    SIE_ENGINE     = "SIE_ENGINE"      # Supplier Intelligence Engine
    SCSE_ENGINE    = "SCSE_ENGINE"     # Similarity Search (past quotes)
    WEB_SCRAPING   = "WEB_SCRAPING"    # Thomasnet, Europages, Kompass
    INDUSTRY_DIR   = "INDUSTRY_DIR"    # VDMA, ISO 9001 registry
    MANUAL         = "MANUAL"          # Human-provided list

@dataclass
class DiscoveredSupplier:
    supplier_id:      str | None          # None if new (not yet in DB)
    name:             str
    website:          str | None
    email:            str | None
    country:          str
    capabilities:     list[str]
    certifications:   list[str]
    overall_score:    float | None        # From SIE or None for new
    source:           DiscoverySource
    confidence:       float               # How confident we are this is a match
    notes:            str = ""
```

### 4.2 Multi-source Supplier Discoverer

```python
import asyncio
import httpx
from playwright.async_api import async_playwright

class SupplierDiscoveryEngine:
    """
    Discovers suppliers from multiple sources in parallel.
    Deduplicates by domain/VAT and scores each candidate.
    """

    SIE_URL  = "http://sie.internal/v1"
    SCSE_URL = "http://scse.internal/v1"

    SCRAPING_TARGETS = {
        "thomasnet":  "https://www.thomasnet.com/search/?what={query}&where={country}",
        "europages":  "https://www.europages.co.uk/companies/{query}.html",
        "kompass":    "https://eu.kompass.com/searchCompany?text={query}",
    }

    def __init__(self, db: "asyncpg.Pool", http: httpx.AsyncClient):
        self._db   = db
        self._http = http

    async def discover(
        self,
        material_code:   str,
        capabilities:    list[str],
        location:        str,
        required_certs:  list[str],
        max_suppliers:   int = 10,
    ) -> list[DiscoveredSupplier]:
        results = await asyncio.gather(
            self._search_internal_db(material_code, location),
            self._search_sie(capabilities, location, required_certs),
            self._search_scse(material_code),
            self._scrape_directories(material_code, location),
            return_exceptions=True,
        )

        candidates: list[DiscoveredSupplier] = []
        for batch in results:
            if isinstance(batch, Exception):
                continue   # Graceful degradation
            candidates.extend(batch)

        # Deduplicate by domain
        seen_domains: set[str] = set()
        unique: list[DiscoveredSupplier] = []
        for c in candidates:
            domain = self._extract_domain(c.website or c.email or c.name)
            if domain not in seen_domains:
                seen_domains.add(domain)
                unique.append(c)

        # Filter blacklisted
        blacklist = await self._load_blacklist()
        unique = [c for c in unique if c.name.lower() not in blacklist]

        # Filter by certification requirement
        if required_certs:
            def cert_ok(c: DiscoveredSupplier) -> bool:
                return any(cert in c.certifications for cert in required_certs) or c.source in (
                    DiscoverySource.INTERNAL_DB, DiscoverySource.SIE_ENGINE
                )
            unique = [c for c in unique if cert_ok(c)]

        # Score and sort
        unique.sort(key=lambda c: c.confidence * (c.overall_score or 0.6), reverse=True)
        return unique[:max_suppliers]

    async def _search_internal_db(
        self, material_code: str, location: str
    ) -> list[DiscoveredSupplier]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT sp.supplier_id, sp.name, sp.website, sp.primary_email,
                          sp.country, sp.capabilities, sp.certifications, sp.overall_score,
                          sp.contact_email, sp.spam_cooldown_until
                   FROM rfqa.supplier_profiles sp
                   JOIN rfqa.supplier_capabilities sc ON sp.supplier_id = sc.supplier_id
                   WHERE sc.material_code = $1
                     AND ($2 = '' OR sp.country = $2 OR sp.country = 'GLOBAL')
                     AND sp.active = TRUE
                     AND (sp.spam_cooldown_until IS NULL OR sp.spam_cooldown_until < now())
                   ORDER BY sp.overall_score DESC
                   LIMIT 20""",
                material_code, location,
            )
        return [
            DiscoveredSupplier(
                supplier_id    = str(r["supplier_id"]),
                name           = r["name"],
                website        = r["website"],
                email          = r["contact_email"] or r["primary_email"],
                country        = r["country"],
                capabilities   = r["capabilities"] or [],
                certifications = r["certifications"] or [],
                overall_score  = float(r["overall_score"]) if r["overall_score"] else None,
                source         = DiscoverySource.INTERNAL_DB,
                confidence     = 0.95,
            )
            for r in rows
        ]

    async def _search_sie(
        self, capabilities: list[str], location: str, certs: list[str]
    ) -> list[DiscoveredSupplier]:
        try:
            resp = await self._http.post(
                f"{self.SIE_URL}/suppliers/search",
                json={
                    "capabilities":   capabilities,
                    "location":       location,
                    "certifications": certs,
                    "top_k":          20,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        return [
            DiscoveredSupplier(
                supplier_id    = r["supplier_id"],
                name           = r["name"],
                website        = r.get("website"),
                email          = r.get("contact_email"),
                country        = r.get("country", ""),
                capabilities   = r.get("capabilities", []),
                certifications = r.get("certifications", []),
                overall_score  = r.get("scorecard_overall"),
                source         = DiscoverySource.SIE_ENGINE,
                confidence     = r.get("match_score", 0.75),
            )
            for r in data.get("suppliers", [])
        ]

    async def _search_scse(self, material_code: str) -> list[DiscoveredSupplier]:
        """Search similar past quotes to find which suppliers priced this material."""
        try:
            resp = await self._http.post(
                f"{self.SCSE_URL}/search/quotes",
                json={
                    "query_text":  material_code,
                    "entity_type": "QUOTE",
                    "top_k":       10,
                    "min_similarity": 0.75,
                },
                timeout=3.0,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception:
            return []

        seen: set[str] = set()
        suppliers = []
        for r in data.get("results", []):
            sid = r["payload"].get("supplier_id")
            if sid and sid not in seen:
                seen.add(sid)
                suppliers.append(DiscoveredSupplier(
                    supplier_id    = sid,
                    name           = r["payload"].get("supplier_name", ""),
                    website        = None,
                    email          = r["payload"].get("supplier_email"),
                    country        = r["payload"].get("country", ""),
                    capabilities   = [],
                    certifications = [],
                    overall_score  = None,
                    source         = DiscoverySource.SCSE_ENGINE,
                    confidence     = float(r.get("similarity_score", 0.70)),
                ))
        return suppliers

    async def _scrape_directories(
        self, material_code: str, location: str
    ) -> list[DiscoveredSupplier]:
        """Scrape Thomasnet / Europages for new suppliers."""
        results = []
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            for site_name, url_template in self.SCRAPING_TARGETS.items():
                try:
                    url  = url_template.format(query=material_code, country=location)
                    page = await browser.new_page()
                    await page.goto(url, wait_until="networkidle", timeout=15000)
                    suppliers = await self._parse_directory_page(page, site_name)
                    results.extend(suppliers)
                    await page.close()
                except Exception:
                    pass
            await browser.close()
        return results

    async def _parse_directory_page(
        self, page: "Page", source: str
    ) -> list[DiscoveredSupplier]:
        # Extract company cards from directory pages
        cards = await page.query_selector_all(".company-card, .company-listing, [data-testid='company']")
        result = []
        for card in cards[:10]:
            name  = await self._safe_text(card, "h2, h3, .company-name")
            url   = await self._safe_attr(card, "a", "href")
            if name:
                result.append(DiscoveredSupplier(
                    supplier_id    = None,
                    name           = name.strip(),
                    website        = url,
                    email          = None,
                    country        = "",
                    capabilities   = [],
                    certifications = [],
                    overall_score  = None,
                    source         = DiscoverySource.WEB_SCRAPING,
                    confidence     = 0.40,
                ))
        return result

    async def _safe_text(self, element, selector: str) -> str | None:
        try:
            el = await element.query_selector(selector)
            return await el.inner_text() if el else None
        except Exception:
            return None

    async def _safe_attr(self, element, selector: str, attr: str) -> str | None:
        try:
            el = await element.query_selector(selector)
            return await el.get_attribute(attr) if el else None
        except Exception:
            return None

    def _extract_domain(self, identifier: str) -> str:
        import re
        match = re.search(r"(?:https?://)?(?:www\.)?([^/\s@]+)", identifier or "")
        return match.group(1).lower() if match else identifier.lower()

    async def _load_blacklist(self) -> set[str]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch("SELECT name_lower FROM rfqa.supplier_blacklist WHERE active = TRUE")
        return {r["name_lower"] for r in rows}
```

---

## 5. Email Generation System

### 5.1 Architektura EmailEngine

```python
from dataclasses import dataclass
from jinja2 import Environment, BaseLoader
import re

@dataclass
class GeneratedEmail:
    to:            str
    cc:            list[str]
    subject:       str
    body_html:     str
    body_text:     str
    attachments:   list[dict]          # [{"filename": "rfq.pdf", "content_b64": "..."}]
    rfq_id:        str
    supplier_id:   str | None
    language:      str                 # "en" | "de" | "pl" | "cn" | ...
    spam_check:    bool = True
```

### 5.2 Generator emaili RFQ

```python
class RFQEmailGenerator:
    """
    Generates localized, professional RFQ emails using Claude claude-opus-4-8.
    Applies anti-spam rules before sending.
    """

    SUPPORTED_LANGUAGES = {"en", "de", "pl", "cs", "ro", "zh", "es", "tr", "fr"}

    def __init__(self, llm: anthropic.AsyncAnthropic, db: "asyncpg.Pool"):
        self._llm = llm
        self._db  = db

    async def generate(
        self,
        rfq:       RFQRequest,
        supplier:  DiscoveredSupplier,
        language:  str = "en",
    ) -> GeneratedEmail:
        # Anti-spam check first
        await self._assert_not_spam(supplier, rfq.material_code)

        prompt = RFQ_EMAIL_PROMPT_TEMPLATE.format(
            supplier_name        = supplier.name,
            product_name         = rfq.product_name,
            material_code        = rfq.material_code,
            material_description = rfq.material_description,
            quantity             = rfq.quantity,
            unit                 = rfq.unit,
            required_delivery    = rfq.required_delivery_date,
            certifications       = ", ".join(rfq.required_certifications) or "None",
            special_requirements = rfq.special_requirements or "None",
            quote_deadline       = rfq.quote_deadline,
            language             = language,
        )

        response = await self._llm.messages.create(
            model="claude-opus-4-8",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        raw = response.content[0].text
        subject, body_html, body_text = self._parse_email_output(raw)

        return GeneratedEmail(
            to           = supplier.email or "",
            cc           = [],
            subject      = subject,
            body_html    = body_html,
            body_text    = body_text,
            attachments  = [],
            rfq_id       = rfq.rfq_id,
            supplier_id  = supplier.supplier_id,
            language     = language,
        )

    async def _assert_not_spam(self, supplier: DiscoveredSupplier, material_code: str) -> None:
        if not supplier.supplier_id:
            return
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT MAX(sent_at) AS last_sent FROM rfqa.email_log
                   WHERE supplier_id = $1 AND material_code = $2
                     AND sent_at > now() - INTERVAL '30 days'""",
                supplier.supplier_id, material_code,
            )
        if row and row["last_sent"]:
            raise SpamViolationError(
                f"Supplier {supplier.name} already contacted for {material_code} "
                f"within 30 days (last: {row['last_sent'].date()})"
            )

    def _parse_email_output(self, raw: str) -> tuple[str, str, str]:
        """Parse structured LLM output into (subject, body_html, body_text)."""
        subject    = ""
        body_html  = ""
        body_text  = ""

        subject_match = re.search(r"SUBJECT:\s*(.+?)(?:\n|$)", raw)
        html_match    = re.search(r"HTML_BODY:\s*([\s\S]+?)(?:TEXT_BODY:|$)", raw)
        text_match    = re.search(r"TEXT_BODY:\s*([\s\S]+?)$", raw)

        if subject_match: subject   = subject_match.group(1).strip()
        if html_match:    body_html = html_match.group(1).strip()
        if text_match:    body_text = text_match.group(1).strip()

        # Fallback: treat entire response as body_text
        if not body_text:
            body_text  = raw
            body_html  = f"<p>{raw.replace(chr(10), '</p><p>')}</p>"

        return subject, body_html, body_text

class SpamViolationError(Exception):
    pass
```

### 5.3 Email Dispatcher

```python
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
import base64

@dataclass
class SMTPConfig:
    host:     str
    port:     int = 587
    username: str = ""
    password: str = ""
    use_tls:  bool = True
    from_email: str = "procurement@company.com"
    from_name:  str = "Procurement Team"

class EmailDispatcher:
    """Sends RFQ emails via SMTP with retry, logging, and spam enforcement."""

    MAX_RETRIES = 3

    def __init__(self, smtp: SMTPConfig, db: "asyncpg.Pool"):
        self._smtp = smtp
        self._db   = db

    async def dispatch(self, email: GeneratedEmail) -> str:
        """Returns message_id. Raises on persistent failure."""
        import asyncio
        msg_id = None
        for attempt in range(self.MAX_RETRIES):
            try:
                msg_id = await asyncio.to_thread(self._send_smtp, email)
                break
            except Exception as exc:
                if attempt == self.MAX_RETRIES - 1:
                    await self._log_email(email, "FAILED", str(exc))
                    raise
                await asyncio.sleep(2 ** attempt)

        await self._log_email(email, "SENT", message_id=msg_id)
        await self._update_spam_cooldown(email)
        return msg_id

    def _send_smtp(self, email: GeneratedEmail) -> str:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = email.subject
        msg["From"]    = f"{self._smtp.from_name} <{self._smtp.from_email}>"
        msg["To"]      = email.to
        if email.cc:
            msg["Cc"]  = ", ".join(email.cc)

        msg.attach(MIMEText(email.body_text, "plain", "utf-8"))
        msg.attach(MIMEText(email.body_html, "html",  "utf-8"))

        for att in email.attachments:
            part = MIMEBase("application", "octet-stream")
            part.set_payload(base64.b64decode(att["content_b64"]))
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", f'attachment; filename="{att["filename"]}"')
            msg.attach(part)

        context = ssl.create_default_context()
        recipients = [email.to] + email.cc
        with smtplib.SMTP(self._smtp.host, self._smtp.port) as server:
            if self._smtp.use_tls:
                server.starttls(context=context)
            if self._smtp.username:
                server.login(self._smtp.username, self._smtp.password)
            server.sendmail(self._smtp.from_email, recipients, msg.as_string())

        return msg.get("Message-ID", str(uuid.uuid4()))

    async def _log_email(
        self, email: GeneratedEmail, status: str,
        error: str | None = None, message_id: str | None = None,
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """INSERT INTO rfqa.email_log
                   (email_id, rfq_id, supplier_id, to_address, subject,
                    material_code, status, error_message, external_message_id, sent_at)
                   VALUES (gen_random_uuid(), $1, $2, $3, $4, $5, $6, $7, $8, now())""",
                email.rfq_id, email.supplier_id, email.to, email.subject,
                None, status, error, message_id,
            )

    async def _update_spam_cooldown(self, email: GeneratedEmail) -> None:
        if not email.supplier_id:
            return
        async with self._db.acquire() as conn:
            await conn.execute(
                """UPDATE rfqa.supplier_profiles
                   SET spam_cooldown_until = now() + INTERVAL '30 days'
                   WHERE supplier_id = $1""",
                email.supplier_id,
            )
```

### 5.4 Szablony emaili — prompt templates (patrz Sekcja 14)

Pełne szablony promptów (RFQ_EMAIL_PROMPT_TEMPLATE i inne) są zdefiniowane
w Sekcji 14 — Prompt Templates, z wersjami dla 6 języków i 3 typów emaili
(initial RFQ / follow-up / negotiation).
