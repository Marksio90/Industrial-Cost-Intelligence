"""
Integration-light tests for the agent ReAct loop.
Uses monkeypatching to avoid real LLM / DB / email calls.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..config import AgentSettings
from ..parsing.response_parser import ParsedQuote


@pytest.fixture
def settings(monkeypatch) -> AgentSettings:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-0000")
    monkeypatch.setenv("RFQ_ANTHROPIC_API_KEY", "sk-test-0000")
    return AgentSettings()  # type: ignore[call-arg]


@pytest.fixture
def mock_session():
    session = AsyncMock()
    session.commit = AsyncMock()
    session.flush = AsyncMock()
    return session


@pytest.fixture
def mock_repo():
    repo = AsyncMock()
    run = MagicMock()
    run.id = uuid.uuid4()
    repo.create_run = AsyncMock(return_value=run)
    repo.update_run = AsyncMock()
    repo.save_step = AsyncMock()
    repo.list_suppliers = AsyncMock(return_value=[])
    repo.create_job = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
    repo.increment_emails_sent = AsyncMock()
    repo.record_sent_email = AsyncMock()
    repo.list_quotes_for_job = AsyncMock(return_value=[])
    return repo


class TestAgentToolDispatch:
    """Test individual tool methods without a real LLM loop."""

    def _make_agent(self, settings, mock_session, mock_repo):
        from ..agent import RFQAgent
        agent = RFQAgent(settings, mock_session, redis_client=None)
        agent._repo = mock_repo
        agent._publisher = AsyncMock()
        agent._publisher.publish = AsyncMock()
        return agent

    @pytest.mark.asyncio
    async def test_tool_generate_rfq(self, settings, mock_session, mock_repo):
        agent = self._make_agent(settings, mock_session, mock_repo)
        mock_spec = MagicMock()
        mock_spec.rfq_number = "RFQ-TEST-202506-ABCD"
        mock_spec.title = "Aluminium Brackets"
        mock_spec.quantity = 500
        mock_spec.unit = "pcs"
        mock_spec.delivery_date = "2025-09-30"
        mock_spec.keywords = ["aluminium", "CNC"]
        mock_spec.specifications = "EN AW-6082 T6, tolerance ±0.1mm"
        mock_spec.to_dict = MagicMock(return_value={"rfq_number": mock_spec.rfq_number})

        with patch.object(agent._rfq_gen, "from_natural_language", AsyncMock(return_value=mock_spec)):
            result = await agent._tool_generate_rfq("Buy 500 aluminium CNC brackets")

        assert "RFQ-TEST" in result
        assert "Aluminium Brackets" in result
        mock_repo.create_job.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_discover_suppliers_empty(self, settings, mock_session, mock_repo):
        agent = self._make_agent(settings, mock_session, mock_repo)
        mock_repo.list_suppliers.return_value = []

        with patch(
            "rfq_agent.agent.SupplierFinder"
        ) as MockFinder:
            instance = MockFinder.return_value
            instance.find = AsyncMock(return_value=[])
            instance.find_from_db = AsyncMock(return_value=[])
            result = await agent._tool_discover_suppliers(keywords=["casting"])

        assert "No suppliers" in result

    @pytest.mark.asyncio
    async def test_tool_send_rfq_emails_no_active_rfq(self, settings, mock_session, mock_repo):
        agent = self._make_agent(settings, mock_session, mock_repo)
        result = await agent._tool_send_rfq_emails(
            rfq_number="RFQ-NONE", supplier_emails=["a@b.com"]
        )
        assert "No active RFQ" in result

    @pytest.mark.asyncio
    async def test_tool_blacklist_supplier(self, settings, mock_session, mock_repo):
        agent = self._make_agent(settings, mock_session, mock_repo)
        agent._tenant_id = "test"

        supplier = MagicMock()
        supplier.id = uuid.uuid4()
        supplier.domain = "spammer.com"
        mock_repo.list_suppliers.return_value = [supplier]
        mock_repo.blacklist_supplier = AsyncMock()

        result = await agent._tool_blacklist_supplier(
            email="contact@spammer.com", reason="Unsolicited contact"
        )
        assert "spammer.com" in result
        mock_repo.blacklist_supplier.assert_called_once_with(supplier.id)

    @pytest.mark.asyncio
    async def test_tool_get_rfq_status_no_job(self, settings, mock_session, mock_repo):
        agent = self._make_agent(settings, mock_session, mock_repo)
        result = await agent._tool_get_rfq_status(rfq_number="RFQ-X")
        assert "No active job" in result


class TestAgentSafetyIntegration:
    """Verify safety checks are invoked during email sending."""

    @pytest.mark.asyncio
    async def test_compliance_blocks_example_com(self, settings, mock_session, mock_repo):
        from ..agent import RFQAgent
        agent = RFQAgent(settings, mock_session, redis_client=None)
        agent._repo = mock_repo
        agent._publisher = AsyncMock()
        agent._publisher.publish = AsyncMock()

        # Set up active RFQ
        mock_spec = MagicMock()
        mock_spec.rfq_number = "RFQ-TEST"
        mock_spec.title = "Brackets"
        mock_spec.to_dict = MagicMock(return_value={})
        agent._active_rfq = mock_spec
        agent._active_job = MagicMock(id=uuid.uuid4())

        result = await agent._tool_send_rfq_emails(
            rfq_number="RFQ-TEST",
            supplier_emails=["contact@example.com"],
        )
        assert "Skipped" in result or "compliance" in result.lower()
        # Email sender must NOT have been called
        assert mock_repo.record_sent_email.call_count == 0
