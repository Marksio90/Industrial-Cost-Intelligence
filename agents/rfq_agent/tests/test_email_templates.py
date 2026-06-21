"""Tests for email template generator (mocked LLM)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..config import AgentSettings
from ..generation.email_templates import EmailTemplateGenerator, GeneratedEmail, _parse_llm_response
from ..safety.anti_spam import AntiSpamChecker


@pytest.fixture
def settings(monkeypatch) -> AgentSettings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-0000")
    monkeypatch.setenv("RFQ_ANTHROPIC_API_KEY", "sk-test-0000")
    return AgentSettings()  # type: ignore[call-arg]


class TestParseEmailResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "subject": "RFQ-001: Aluminium Brackets",
            "body_text": "Dear Supplier, please quote.",
            "body_html": "<p>Dear Supplier, please quote.</p>",
        })
        result = _parse_llm_response(raw)
        assert result.subject == "RFQ-001: Aluminium Brackets"
        assert "<p>" in result.body_html

    def test_json_in_code_fence(self):
        raw = "```json\n" + json.dumps({
            "subject": "Test Subject",
            "body_text": "Hello",
            "body_html": "<p>Hello</p>",
        }) + "\n```"
        result = _parse_llm_response(raw)
        assert result.subject == "Test Subject"

    def test_fallback_on_invalid_json(self):
        raw = "Subject Line\nBody paragraph one.\nBody paragraph two."
        result = _parse_llm_response(raw)
        assert result.subject == "Subject Line"
        assert "Body paragraph" in result.body_text


class TestEmailTemplateGenerator:
    @pytest.fixture
    def mock_email_response(self):
        return {
            "subject": "RFQ-ICI-202506-TEST: CNC Aluminium Brackets — Request for Quotation",
            "body_text": (
                "Dear ACME Machining,\n\n"
                "We hereby request a quotation for 500 pcs CNC aluminium brackets "
                "to specification EN AW-6082 T6. Please provide unit price, lead time, "
                "and payment terms by 2025-07-01.\n\n"
                "To unsubscribe: {UNSUBSCRIBE_URL} — GDPR compliant.\n\n"
                "Best regards,\nICI Procurement Team"
            ),
            "body_html": (
                "<p>Dear ACME Machining,</p>"
                "<p>We hereby request a quotation for 500 pcs CNC aluminium brackets.</p>"
                "<p><a href='{UNSUBSCRIBE_URL}'>Unsubscribe</a> — GDPR compliant.</p>"
            ),
        }

    @pytest.mark.asyncio
    async def test_generate_returns_email(self, settings, mock_email_response):
        with patch("anthropic.AsyncAnthropic") as MockClient:
            instance = MockClient.return_value
            msg = MagicMock()
            msg.content = [MagicMock(text=json.dumps(mock_email_response))]
            instance.messages.create = AsyncMock(return_value=msg)

            gen = EmailTemplateGenerator(settings)
            gen._client = instance

            result = await gen.generate(
                rfq_number="RFQ-ICI-202506-TEST",
                title="CNC Aluminium Brackets",
                supplier_name="ACME Machining",
                requirements={"quantity": 500, "unit": "pcs"},
            )

        assert isinstance(result, GeneratedEmail)
        assert "RFQ-ICI" in result.subject
        assert "{UNSUBSCRIBE_URL}" in result.body_text  # placeholder still present (sender injects)

    @pytest.mark.asyncio
    async def test_generated_email_passes_spam_check(self, settings, mock_email_response):
        with patch("anthropic.AsyncAnthropic") as MockClient:
            instance = MockClient.return_value
            msg = MagicMock()
            msg.content = [MagicMock(text=json.dumps(mock_email_response))]
            instance.messages.create = AsyncMock(return_value=msg)

            gen = EmailTemplateGenerator(settings)
            gen._client = instance

            result = await gen.generate(
                rfq_number="RFQ-ICI-202506-TEST",
                title="CNC Aluminium Brackets",
                supplier_name="ACME Machining",
                requirements={},
            )

        checker = AntiSpamChecker()
        spam_result = checker.check(result.subject, result.body_text)
        assert spam_result.passed, f"Spam flags: {spam_result.flags}"
