"""
Section 7 — Multi-Agent Negotiation Framework

Coordinator orchestrates five specialized sub-agents using Anthropic tool_use:

  ┌─────────────────────────────────────────────────────────────────────┐
  │                    NegotiationCoordinator                           │
  │  (LLM: claude-sonnet-4-6 as meta-planner)                          │
  │                           │                                         │
  │  ┌────────────┐  ┌───────────────┐  ┌────────────┐  ┌──────────┐  │
  │  │  Research  │  │   Analyst     │  │Negotiation │  │  Risk    │  │
  │  │   Agent    │  │   Agent       │  │  Agent     │  │  Agent   │  │
  │  │ (gather    │  │ (model price/ │  │ (generate  │  │ (gate    │  │
  │  │  market +  │  │  elasticity/  │  │  offers +  │  │  offers  │  │
  │  │  supplier  │  │  history)     │  │  messages) │  │  + stop) │  │
  │  │  data)     │  │               │  │            │  │          │  │
  │  └────────────┘  └───────────────┘  └────────────┘  └──────────┘  │
  └─────────────────────────────────────────────────────────────────────┘

Execution model:
  1. ANALYZING phase: Research + Analyst run in parallel
  2. STRATEGY_SET phase: Coordinator selects strategy from Analyst output
  3. Each ROUND_ACTIVE phase:
       a. Risk Agent validates proposed offer (STOP gate)
       b. Negotiation Agent generates offer + message
       c. Approval gate (human or auto)
       d. Offer sent → await response
       e. Analyst updates behavior model from response
       f. Coordinator decides: continue / accept / walk away

Message protocol:
  Agents communicate via AgentMessage dataclass (typed, serializable).
  All messages are logged to Redis stream ici:negotiations:{session_id}.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

import structlog

from .counter_proposal import CounterProposalEngine, ConcessionSchedule
from .historical_model import HistoricalNegotiationModel, HistoricalInsight
from .models import (
    NegotiationOffer, NegotiationStrategy, RoundOutcome,
    SessionState, SupplierBehaviorProfile,
)
from .price_elasticity import PriceElasticityService
from .risk_controls import RiskControlEngine, RiskDecision
from .strategy import StrategySelectionEngine, StrategySelectionResult
from .supplier_behavior import (
    AcceptanceProbabilityEstimator, RoundSignalDetector, SupplierBehaviorAnalyzer,
    SupplierBehaviorProfile as BehaviorProfile,
)

logger = structlog.get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Agent message protocol
# ─────────────────────────────────────────────────────────────────────────────

class AgentRole(str, Enum):
    COORDINATOR  = "coordinator"
    RESEARCH     = "research"
    ANALYST      = "analyst"
    NEGOTIATION  = "negotiation"
    RISK         = "risk"
    APPROVAL     = "approval"


@dataclass
class AgentMessage:
    message_id:  str = field(default_factory=lambda: str(uuid.uuid4()))
    from_agent:  AgentRole = AgentRole.COORDINATOR
    to_agent:    AgentRole = AgentRole.COORDINATOR
    session_id:  str = ""
    round_number: int = 0
    msg_type:    str = ""   # "request" | "response" | "broadcast"
    payload:     dict[str, Any] = field(default_factory=dict)
    ts:          float = field(default_factory=lambda: datetime.now(timezone.utc).timestamp())

    def to_json(self) -> str:
        d = asdict(self)
        d["from_agent"] = self.from_agent.value
        d["to_agent"]   = self.to_agent.value
        return json.dumps(d)


# ─────────────────────────────────────────────────────────────────────────────
# Research Agent
# ─────────────────────────────────────────────────────────────────────────────

class ResearchAgent:
    """
    Gathers market data and supplier information before first offer.
    In production: calls ML service, external price APIs, supplier DB.
    """

    async def gather(
        self,
        session_id:      str,
        supplier_id:     str,
        material_class:  str,
        sub_class:       str,
        grade:           str,
        quantity:        float,
        ml_service_url:  str | None = None,
        http_client:     Any = None,
    ) -> dict[str, Any]:
        logger.info("research_agent.gathering", session_id=session_id, material_class=material_class)

        results: dict[str, Any] = {
            "market_price":       None,
            "substitute_count":   0,
            "relationship_score": 0.3,
            "market_trend":       "stable",
            "supplier_capacity_pct": 0.7,
        }

        if http_client and ml_service_url:
            try:
                resp = await http_client.post(
                    f"{ml_service_url}/predict",
                    json={
                        "material_class": material_class,
                        "sub_class":      sub_class,
                        "grade":          grade,
                        "quantity_kg":    quantity,
                    },
                    timeout=10,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    results["market_price"] = data.get("predicted_price_eur_kg")
                    results["confidence"]   = data.get("confidence_score", 0.5)
            except Exception as exc:
                logger.warning("research_agent.ml_fetch_failed", error=str(exc))

        return results


# ─────────────────────────────────────────────────────────────────────────────
# Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────

class AnalystAgent:
    """
    Runs historical model, price elasticity, and behavior analysis.
    Produces structured insights consumed by the Coordinator.
    """

    def __init__(self) -> None:
        self._hist      = HistoricalNegotiationModel()
        self._elasticity = PriceElasticityService()
        self._behavior   = SupplierBehaviorAnalyzer()
        self._acceptance = AcceptanceProbabilityEstimator()
        self._signals    = RoundSignalDetector()

    async def analyze_pre_negotiation(
        self,
        session_id:      str,
        material_class:  str,
        sub_class:       str,
        grade:           str,
        quantity:        float,
        supplier_country: str,
        initial_ask:     float,
        target_price:    float,
        walk_away:       float,
        market_price:    float | None,
        behavior_profile: BehaviorProfile,
        month:           int = 1,
    ) -> dict[str, Any]:
        logger.info("analyst_agent.pre_negotiation", session_id=session_id)

        historical = self._hist.analyze(
            material_class   = material_class,
            sub_class        = sub_class,
            grade            = grade,
            quantity         = quantity,
            supplier_country = supplier_country,
            market_price     = market_price,
            month            = month,
        )

        elasticity_data = self._elasticity.analyze(
            initial_ask    = initial_ask,
            target_price   = target_price,
            walk_away      = walk_away,
            quantity       = quantity,
            material_class = material_class,
            month          = month,
        )

        return {
            "historical_insight":     historical,
            "elasticity":             elasticity_data,
            "recommended_anchor":     historical.recommended_anchor or walk_away * 0.88,
            "recommended_target":     historical.recommended_target or target_price,
            "recommended_walk_away":  historical.recommended_walk_away or walk_away,
            "suggested_strategy":     historical.suggested_strategy,
            "zopa":                   historical.zopa,
        }

    async def analyze_round_response(
        self,
        session_id:    str,
        round_history: list[dict[str, Any]],
        behavior_profile: BehaviorProfile,
        initial_ask:   float,
        target_price:  float,
        walk_away:     float,
        round_number:  int,
        max_rounds:    int,
        days_to_deadline: int | None,
    ) -> dict[str, Any]:
        logger.debug("analyst_agent.round_analysis", session_id=session_id, round=round_number)

        signals = self._signals.detect(round_history, behavior_profile)

        current_supplier_price = (
            round_history[-1].get("supplier_price") if round_history else None
        )

        acceptance_prob = self._acceptance.predict(
            our_price               = walk_away,
            initial_ask             = initial_ask,
            target_price            = target_price,
            round_number            = round_number,
            max_rounds              = max_rounds,
            profile                 = behavior_profile,
            days_to_deadline        = days_to_deadline,
            current_supplier_price  = current_supplier_price,
        )

        return {
            "behavioral_signals":   signals,
            "acceptance_probability": acceptance_prob,
            "current_supplier_price": current_supplier_price,
            "signal_names":         [s.signal for s in signals],
        }


# ─────────────────────────────────────────────────────────────────────────────
# Negotiation Agent
# ─────────────────────────────────────────────────────────────────────────────

class NegotiationSubAgent:
    """
    Generates and refines offers. One per active session.
    """

    def __init__(self, anthropic_client: Any = None) -> None:
        self._engine   = CounterProposalEngine(anthropic_client)
        self._schedule: ConcessionSchedule | None = None

    def initialize_schedule(
        self,
        anchor_price:  float,
        target_price:  float,
        walk_away:     float,
        max_rounds:    int,
        strategy:      NegotiationStrategy,
        urgency_score: float = 0.5,
    ) -> ConcessionSchedule:
        self._schedule = self._engine.build_schedule(
            anchor_price, target_price, walk_away, max_rounds, strategy, urgency_score
        )
        return self._schedule

    async def generate_offer(
        self,
        round_number:      int,
        quantity:          int,
        profile:           BehaviorProfile,
        strategy:          NegotiationStrategy,
        supplier_response: dict[str, Any] | None,
        market_price:      float | None,
        session_context:   dict[str, Any],
    ) -> NegotiationOffer:
        if not self._schedule:
            raise RuntimeError("Call initialize_schedule before generate_offer")
        return await self._engine.generate_offer(
            schedule           = self._schedule,
            round_number       = round_number,
            quantity           = quantity,
            profile            = profile,
            strategy           = strategy,
            supplier_response  = supplier_response,
            market_price       = market_price,
            session_context    = session_context,
        )

    def evaluate_auto_accept(
        self,
        supplier_price:  float,
        acceptance_prob: float,
        round_number:    int,
        max_rounds:      int,
    ) -> tuple[bool, str]:
        if not self._schedule:
            return False, "no schedule"
        return self._engine.should_accept_supplier_counter(
            supplier_price   = supplier_price,
            walk_away        = self._schedule.walk_away,
            target_price     = self._schedule.target_price,
            acceptance_prob  = acceptance_prob,
            round_number     = round_number,
            max_rounds       = max_rounds,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Negotiation Coordinator
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CoordinatorState:
    """Full in-memory state for one active negotiation session."""
    session_id:      str
    tenant_id:       str
    supplier_id:     str
    state:           SessionState = SessionState.INITIATED
    strategy:        NegotiationStrategy | None = None
    round_number:    int = 0
    max_rounds:      int = 8
    initial_ask:     float = 0.0
    anchor_price:    float = 0.0
    target_price:    float = 0.0
    walk_away:       float = 0.0
    quantity:        float = 0.0
    market_price:    float | None = None
    behavior_profile: BehaviorProfile | None = None
    round_history:   list[dict[str, Any]] = field(default_factory=list)
    messages:        list[AgentMessage]   = field(default_factory=list)
    context:         dict[str, Any]       = field(default_factory=dict)
    days_to_deadline: int | None = None


class NegotiationCoordinator:
    """
    Top-level orchestrator for a single negotiation session.
    Called from the FastAPI API layer; persists state to PostgreSQL via DB callbacks.

    Lifecycle:
      __init__ → start_session → [process_supplier_response]* → close_session
    """

    def __init__(
        self,
        anthropic_client:  Any = None,
        ml_service_url:    str | None = None,
        http_client:       Any = None,
        redis_client:      Any = None,
        on_state_change:   Any = None,    # async callback(session_id, state, data)
        on_approval_needed: Any = None,   # async callback(session_id, request)
    ) -> None:
        self._research    = ResearchAgent()
        self._analyst     = AnalystAgent()
        self._strategy    = StrategySelectionEngine()
        self._risk        = RiskControlEngine()
        self._negotiation = NegotiationSubAgent(anthropic_client)
        self._ml_url      = ml_service_url
        self._http        = http_client
        self._redis       = redis_client
        self._on_state_change    = on_state_change
        self._on_approval_needed = on_approval_needed

    async def start_session(
        self,
        state: CoordinatorState,
        initial_ask: float,
        behavior_profile: BehaviorProfile,
        urgency_score: float = 0.5,
        substitute_count: int = 0,
        relationship_score: float = 0.3,
        is_sole_source: bool = False,
        volume_strategic: bool = False,
    ) -> dict[str, Any]:
        """
        Phase 1: ANALYZING — gather data and select strategy.
        Phase 2: STRATEGY_SET — build concession schedule.
        Returns the first offer to send.
        """
        state.state           = SessionState.ANALYZING
        state.initial_ask     = initial_ask
        state.behavior_profile = behavior_profile

        await self._emit_state(state, {"phase": "analyzing"})

        # Parallel: research + analysis
        research_task = asyncio.create_task(self._research.gather(
            session_id      = state.session_id,
            supplier_id     = state.supplier_id,
            material_class  = state.context.get("material_class", ""),
            sub_class       = state.context.get("sub_class", ""),
            grade           = state.context.get("grade", ""),
            quantity        = state.quantity,
            ml_service_url  = self._ml_url,
            http_client     = self._http,
        ))

        analyst_task = asyncio.create_task(self._analyst.analyze_pre_negotiation(
            session_id       = state.session_id,
            material_class   = state.context.get("material_class", ""),
            sub_class        = state.context.get("sub_class", ""),
            grade            = state.context.get("grade", ""),
            quantity         = state.quantity,
            supplier_country = state.context.get("supplier_country", ""),
            initial_ask      = initial_ask,
            target_price     = state.target_price,
            walk_away        = state.walk_away,
            market_price     = state.market_price,
            behavior_profile = behavior_profile,
            month            = state.context.get("month", 1),
        ))

        research_data, analyst_data = await asyncio.gather(research_task, analyst_task, return_exceptions=True)

        if isinstance(research_data, Exception):
            logger.warning("research_agent_failed", error=str(research_data))
            research_data = {}
        if isinstance(analyst_data, Exception):
            logger.warning("analyst_agent_failed", error=str(analyst_data))
            analyst_data = {}

        market_price     = (research_data or {}).get("market_price") or state.market_price
        state.market_price = market_price

        # Override targets from historical model if more confident
        hi = (analyst_data or {}).get("historical_insight")
        if hi and getattr(hi, "confidence", 0) > 0.5:
            state.anchor_price = hi.recommended_anchor or state.anchor_price
            if not state.target_price:
                state.target_price = hi.recommended_target or (initial_ask * 0.88)
            if not state.walk_away:
                state.walk_away = hi.recommended_walk_away or (initial_ask * 1.10)

        # Select strategy
        price_gap_pct = (initial_ask - state.target_price) / initial_ask if initial_ask else 0.10
        strategy_result = self._strategy.select(
            price_gap_pct       = price_gap_pct,
            urgency_score       = urgency_score,
            substitute_count    = substitute_count or (research_data or {}).get("substitute_count", 0),
            relationship_score  = relationship_score,
            supplier_profile    = behavior_profile,
            is_sole_source      = is_sole_source,
            volume_strategic    = volume_strategic,
            days_to_deadline    = state.days_to_deadline,
        )
        state.strategy = strategy_result.strategy
        state.state    = SessionState.STRATEGY_SET

        await self._emit_state(state, {
            "strategy":         state.strategy.value,
            "reasoning":        strategy_result.reasoning,
            "expected_rounds":  strategy_result.expected_rounds,
        })

        # Initialize concession schedule
        self._negotiation.initialize_schedule(
            anchor_price  = state.anchor_price or initial_ask * 0.80,
            target_price  = state.target_price or initial_ask * 0.88,
            walk_away     = state.walk_away    or initial_ask * 1.05,
            max_rounds    = state.max_rounds,
            strategy      = state.strategy,
            urgency_score = urgency_score,
        )

        # Generate first offer
        offer = await self._generate_next_offer(state, supplier_response=None)

        return {
            "offer":             offer,
            "strategy":          state.strategy.value,
            "strategy_reasoning": strategy_result.reasoning,
            "market_price":      market_price,
            "analyst_summary":   getattr(hi, "summary", "") if hi else "",
        }

    async def process_supplier_response(
        self,
        state: CoordinatorState,
        supplier_price:   float,
        supplier_terms:   dict[str, Any],
        supplier_message: str = "",
        is_final:         bool = False,
    ) -> dict[str, Any]:
        """
        Called when a supplier counter-offer arrives.
        Returns the next action: "send_offer" | "auto_accept" | "walk_away" | "human_approval"
        """
        state.round_history.append({
            "round_number":    state.round_number,
            "supplier_price":  supplier_price,
            "supplier_terms":  supplier_terms,
            "supplier_message": supplier_message,
        })

        # Analyst: detect signals + acceptance probability
        analyst_data = await self._analyst.analyze_round_response(
            session_id       = state.session_id,
            round_history    = state.round_history,
            behavior_profile = state.behavior_profile,
            initial_ask      = state.initial_ask,
            target_price     = state.target_price,
            walk_away        = state.walk_away,
            round_number     = state.round_number,
            max_rounds       = state.max_rounds,
            days_to_deadline = state.days_to_deadline,
        )

        signals        = analyst_data.get("behavioral_signals", [])
        accept_prob    = analyst_data.get("acceptance_probability", 0.5)

        # Check auto-accept
        should_accept, accept_reason = self._negotiation.evaluate_auto_accept(
            supplier_price  = supplier_price,
            acceptance_prob = accept_prob,
            round_number    = state.round_number,
            max_rounds      = state.max_rounds,
        )

        if should_accept:
            state.state = SessionState.AGREEMENT
            await self._emit_state(state, {"settled_price": supplier_price, "reason": accept_reason})
            return {"action": "auto_accept", "settled_price": supplier_price, "reason": accept_reason}

        # Walk-away check
        if supplier_price > state.walk_away * 1.02 and state.round_number >= state.max_rounds:
            state.state = SessionState.DEADLOCK
            await self._emit_state(state, {"reason": "max_rounds_exceeded_above_walk_away"})
            return {"action": "walk_away", "reason": "max rounds exceeded"}

        # Strategy adaptation
        adaptation = self._strategy.should_adapt(
            current_strategy = state.strategy,
            round_number     = state.round_number,
            max_rounds       = state.max_rounds,
            signals          = signals,
            acceptance_prob  = accept_prob,
        )
        if adaptation:
            new_strategy, adapt_reason = adaptation
            logger.info("strategy_adapted", old=state.strategy.value, new=new_strategy.value, reason=adapt_reason)
            state.strategy = new_strategy

        # Generate next offer
        offer = await self._generate_next_offer(state, supplier_response={
            "price_per_unit": supplier_price,
            "terms":          supplier_terms,
            "message":        supplier_message,
        })

        # Risk gate
        risk = self._risk.evaluate_offer(
            session_id    = state.session_id,
            offer_price   = float(offer.price_per_unit),
            walk_away     = state.walk_away,
            initial_ask   = state.initial_ask,
            round_number  = state.round_number,
            max_rounds    = state.max_rounds,
            accept_prob   = accept_prob,
        )

        if risk.action == "stop":
            state.state = SessionState.ESCALATED
            await self._emit_state(state, {"risk_reason": risk.reason})
            return {"action": "escalate", "reason": risk.reason, "risk": risk}

        if risk.requires_approval:
            state.state = SessionState.AWAITING_APPROVAL
            if self._on_approval_needed:
                await self._on_approval_needed(state.session_id, {
                    "offer":        offer,
                    "risk":         risk,
                    "signals":      [s.signal for s in signals],
                    "accept_prob":  accept_prob,
                })
            return {"action": "human_approval", "offer": offer, "risk": risk}

        state.state = SessionState.ROUND_ACTIVE
        await self._emit_state(state, {
            "round":        state.round_number,
            "offer_price":  float(offer.price_per_unit),
            "accept_prob":  accept_prob,
        })
        await self._publish_to_redis(state.session_id, "offer_generated", {
            "round":  state.round_number,
            "price":  float(offer.price_per_unit),
        })

        return {"action": "send_offer", "offer": offer, "acceptance_probability": accept_prob}

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _generate_next_offer(
        self,
        state: CoordinatorState,
        supplier_response: dict[str, Any] | None,
    ) -> NegotiationOffer:
        state.round_number += 1
        return await self._negotiation.generate_offer(
            round_number      = state.round_number,
            quantity          = int(state.quantity),
            profile           = state.behavior_profile,
            strategy          = state.strategy,
            supplier_response = supplier_response,
            market_price      = state.market_price,
            session_context   = state.context,
        )

    async def _emit_state(self, state: CoordinatorState, data: dict[str, Any]) -> None:
        if self._on_state_change:
            try:
                await self._on_state_change(state.session_id, state.state, data)
            except Exception as exc:
                logger.warning("state_callback_failed", error=str(exc))

    async def _publish_to_redis(self, session_id: str, event: str, data: dict[str, Any]) -> None:
        if not self._redis:
            return
        try:
            await self._redis.xadd(
                f"ici:negotiations:{session_id}",
                {"event": event, "data": json.dumps(data)},
                maxlen=1000,
                approximate=True,
            )
        except Exception as exc:
            logger.warning("redis_publish_failed", error=str(exc))
