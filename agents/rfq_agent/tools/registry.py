from __future__ import annotations

import uuid
from typing import Any, Callable, Coroutine

import structlog

log = structlog.get_logger(__name__)

ToolFn = Callable[..., Coroutine[Any, Any, str]]


class ToolRegistry:
    """Registry of callable tools available to the agent."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolFn, str]] = {}

    def register(self, name: str, fn: ToolFn, description: str) -> None:
        self._tools[name] = (fn, description)
        log.debug("tool_registered", name=name)

    async def call(self, name: str, inputs: dict[str, Any]) -> str:
        if name not in self._tools:
            return f"ERROR: Unknown tool '{name}'. Available: {self.list_names()}"
        fn, _ = self._tools[name]
        try:
            return await fn(**inputs)
        except Exception as exc:
            log.error("tool_error", tool=name, error=str(exc))
            return f"ERROR calling {name}: {exc}"

    def list_names(self) -> list[str]:
        return list(self._tools.keys())

    def descriptions(self) -> str:
        lines = []
        for name, (_, desc) in self._tools.items():
            lines.append(f"- **{name}**: {desc}")
        return "\n".join(lines)


def build_tool_schema(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": parameters,
            "required": [k for k, v in parameters.items() if v.get("required", True)],
        },
    }


# Tool parameter schema definitions for Anthropic tool_use API
TOOL_SCHEMAS: list[dict[str, Any]] = [
    build_tool_schema(
        "discover_suppliers",
        "Search for and discover suppliers matching given keywords and country.",
        {
            "keywords": {"type": "array", "items": {"type": "string"}, "description": "Capability keywords e.g. ['aluminium casting', 'CNC machining']"},
            "country_code": {"type": "string", "description": "ISO 3166-1 alpha-2 country code (optional)", "required": False},
            "max_results": {"type": "integer", "default": 10, "required": False},
        },
    ),
    build_tool_schema(
        "generate_rfq",
        "Generate a structured RFQ from a natural-language procurement description.",
        {
            "description": {"type": "string", "description": "Natural language description of what needs to be purchased"},
        },
    ),
    build_tool_schema(
        "send_rfq_emails",
        "Send RFQ emails to a list of suppliers.",
        {
            "rfq_number": {"type": "string"},
            "supplier_emails": {"type": "array", "items": {"type": "string"}},
        },
    ),
    build_tool_schema(
        "check_responses",
        "Check the mailbox for supplier RFQ responses for a given RFQ number.",
        {
            "rfq_number": {"type": "string"},
        },
    ),
    build_tool_schema(
        "parse_and_rank_quotes",
        "Parse all received email responses for an RFQ and return a ranked comparison.",
        {
            "rfq_number": {"type": "string"},
            "reference_price_eur": {"type": "number", "description": "Expected/budget price for scoring", "required": False},
        },
    ),
    build_tool_schema(
        "get_rfq_status",
        "Get current status and statistics for an RFQ job.",
        {
            "rfq_number": {"type": "string"},
        },
    ),
    build_tool_schema(
        "blacklist_supplier",
        "Add a supplier email domain to the blacklist.",
        {
            "email": {"type": "string"},
            "reason": {"type": "string"},
        },
    ),
    build_tool_schema(
        "finish",
        "Signal that the agent has completed its goal. Provide a summary.",
        {
            "summary": {"type": "string"},
            "result": {"type": "object", "description": "Structured result data", "required": False},
        },
    ),
]
