from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import anthropic
import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)

_SYSTEM = """You are a procurement engineer. Given a natural-language description of a purchasing need,
extract structured RFQ parameters in JSON.

Output ONLY valid JSON matching this schema:
{
  "title": "string — short material/service name",
  "material_class": "METAL | PLASTIC | COMPOSITE | CERAMIC | ELECTRONIC | SERVICE | OTHER",
  "quantity": number,
  "unit": "string (pcs / kg / m / m2 / m3 / litre / hours)",
  "specifications": "string — technical spec summary",
  "delivery_date": "YYYY-MM-DD",
  "response_deadline": "YYYY-MM-DD",
  "budget_eur": number | null,
  "quality_standard": "string | null (ISO 9001, IATF 16949, AS9100 ...)",
  "incoterms": "string | null (EXW, DAP, DDP ...)",
  "notes": "string | null",
  "required_certifications": ["string"],
  "keywords": ["string — for supplier discovery"]
}"""


@dataclass
class RFQSpec:
    rfq_number: str
    title: str
    material_class: str
    quantity: float
    unit: str
    specifications: str
    delivery_date: str
    response_deadline: str
    budget_eur: float | None
    quality_standard: str | None
    incoterms: str | None
    notes: str | None
    required_certifications: list[str]
    keywords: list[str]
    raw_requirements: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rfq_number": self.rfq_number,
            "title": self.title,
            "material_class": self.material_class,
            "quantity": self.quantity,
            "unit": self.unit,
            "specifications": self.specifications,
            "delivery_date": self.delivery_date,
            "response_deadline": self.response_deadline,
            "budget_eur": self.budget_eur,
            "quality_standard": self.quality_standard,
            "incoterms": self.incoterms,
            "notes": self.notes,
            "required_certifications": self.required_certifications,
            "keywords": self.keywords,
        }


class RFQGenerator:
    def __init__(self, settings: AgentSettings) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._model = settings.llm_model

    async def from_natural_language(self, description: str, tenant_id: str) -> RFQSpec:
        """Parse a free-form procurement description into a structured RFQSpec."""
        log.info("rfq_generation_start", length=len(description))
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=1024,
            system=_SYSTEM,
            messages=[{"role": "user", "content": description}],
        )
        raw = message.content[0].text.strip()
        data = _parse_json(raw)

        rfq_number = _generate_rfq_number(tenant_id)
        spec = RFQSpec(
            rfq_number=rfq_number,
            title=data.get("title", "Unnamed RFQ"),
            material_class=data.get("material_class", "OTHER"),
            quantity=float(data.get("quantity") or 1),
            unit=data.get("unit", "pcs"),
            specifications=data.get("specifications", description[:500]),
            delivery_date=data.get("delivery_date", ""),
            response_deadline=data.get("response_deadline", ""),
            budget_eur=data.get("budget_eur"),
            quality_standard=data.get("quality_standard"),
            incoterms=data.get("incoterms"),
            notes=data.get("notes"),
            required_certifications=data.get("required_certifications", []),
            keywords=data.get("keywords", []),
            raw_requirements=data,
        )
        log.info("rfq_generated", rfq=rfq_number, title=spec.title)
        return spec

    async def from_structured(self, data: dict[str, Any], tenant_id: str) -> RFQSpec:
        """Construct RFQSpec from already-structured input, with LLM-enrichment."""
        description = _dict_to_description(data)
        spec = await self.from_natural_language(description, tenant_id)
        # Preserve any explicitly passed values
        for key in ("rfq_number", "delivery_date", "response_deadline", "budget_eur"):
            if key in data and data[key]:
                setattr(spec, key, data[key])
        return spec


def _parse_json(raw: str) -> dict[str, Any]:
    import json
    import re
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
    raw = re.sub(r"\s*```$", "", raw, flags=re.M)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        log.warning("rfq_json_parse_failed")
        return {}


def _generate_rfq_number(tenant_id: str) -> str:
    now = datetime.now(timezone.utc)
    short_id = str(uuid.uuid4()).split("-")[0].upper()
    prefix = tenant_id[:4].upper() if tenant_id else "ICI"
    return f"RFQ-{prefix}-{now.strftime('%Y%m')}-{short_id}"


def _dict_to_description(data: dict[str, Any]) -> str:
    parts = []
    for k, v in data.items():
        if v is not None:
            parts.append(f"{k}: {v}")
    return "\n".join(parts)
