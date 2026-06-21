from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import anthropic
import structlog

from ..config import AgentSettings

log = structlog.get_logger(__name__)

_SYSTEM_PROMPT = """You are a senior procurement specialist at an industrial manufacturing company.
You write professional, concise RFQ (Request for Quotation) emails to suppliers.

Style rules:
- Formal but direct B2B tone
- No marketing language, no exclamation marks
- Always include: RFQ number, deadline, technical specifications, required fields in response
- End with a professional signature block
- Include an unsubscribe line in the footer for GDPR compliance
- Maximum 400 words in the body
- Plain text paragraphs only — no bullet lists in the HTML version
- Output ONLY the email. No explanations before or after.

Output format (strict JSON):
{
  "subject": "<email subject>",
  "body_text": "<plain-text version>",
  "body_html": "<HTML version with basic formatting>"
}"""

_USER_TEMPLATE = """Generate an RFQ email with the following parameters:

RFQ Number: {rfq_number}
Material/Service: {title}
Quantity: {quantity} {unit}
Required delivery date: {deadline}
Specifications: {specs}
Supplier name: {supplier_name}
Our company: {company_name}
Contact person: {contact_name}
Response deadline: {response_deadline}
Additional notes: {notes}

Ensure the HTML body includes an unsubscribe link placeholder: {{UNSUBSCRIBE_URL}}
"""


@dataclass
class GeneratedEmail:
    subject: str
    body_text: str
    body_html: str


class EmailTemplateGenerator:
    def __init__(self, settings: AgentSettings) -> None:
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value()
        )
        self._model = settings.llm_model
        self._max_tokens = settings.llm_max_tokens

    async def generate(
        self,
        rfq_number: str,
        title: str,
        supplier_name: str,
        requirements: dict[str, Any],
        company_name: str = "ICI Procurement",
        contact_name: str = "Procurement Team",
    ) -> GeneratedEmail:
        prompt = _USER_TEMPLATE.format(
            rfq_number=rfq_number,
            title=title,
            quantity=requirements.get("quantity", "TBD"),
            unit=requirements.get("unit", "units"),
            deadline=requirements.get("delivery_date", "as soon as possible"),
            specs=requirements.get("specifications", "See attached technical drawing"),
            supplier_name=supplier_name,
            company_name=company_name,
            contact_name=contact_name,
            response_deadline=requirements.get(
                "response_deadline",
                _default_deadline(),
            ),
            notes=requirements.get("notes", "None"),
        )

        log.info("generating_email", rfq=rfq_number, supplier=supplier_name)
        message = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return _parse_llm_response(raw)

    async def generate_follow_up(
        self,
        original_rfq_number: str,
        supplier_name: str,
        days_since_sent: int,
    ) -> GeneratedEmail:
        prompt = f"""Generate a polite follow-up email for RFQ {original_rfq_number} sent {days_since_sent} days ago to {supplier_name}.
Keep it under 100 words. Reference the original RFQ number. Ask if they need any clarifications.
Output JSON with subject, body_text, body_html keys."""

        message = await self._client.messages.create(
            model=self._model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        return _parse_llm_response(raw)

    async def generate_batch(
        self,
        rfq_number: str,
        title: str,
        suppliers: list[dict[str, Any]],
        requirements: dict[str, Any],
    ) -> list[tuple[dict[str, Any], GeneratedEmail]]:
        """Generate personalised emails for multiple suppliers concurrently."""
        import asyncio

        tasks = [
            self.generate(
                rfq_number=rfq_number,
                title=title,
                supplier_name=s.get("name", "Supplier"),
                requirements=requirements,
            )
            for s in suppliers
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        out = []
        for supplier, result in zip(suppliers, results):
            if isinstance(result, Exception):
                log.error("email_gen_failed", supplier=supplier.get("name"), error=str(result))
            else:
                out.append((supplier, result))
        return out


def _parse_llm_response(raw: str) -> GeneratedEmail:
    import json

    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.M)
    raw = re.sub(r"\s*```$", "", raw, flags=re.M)

    try:
        data = json.loads(raw)
        return GeneratedEmail(
            subject=data["subject"],
            body_text=data["body_text"],
            body_html=data["body_html"],
        )
    except (json.JSONDecodeError, KeyError) as exc:
        log.warning("llm_parse_fallback", error=str(exc))
        # Fallback: treat raw output as plain text body
        lines = raw.strip().splitlines()
        subject = lines[0] if lines else "RFQ Request"
        body = "\n".join(lines[1:]).strip()
        return GeneratedEmail(
            subject=subject,
            body_text=body,
            body_html=f"<p>{body.replace(chr(10), '</p><p>')}</p>",
        )


def _default_deadline() -> str:
    from datetime import timedelta
    d = datetime.now(timezone.utc) + timedelta(days=7)
    return d.strftime("%Y-%m-%d")
