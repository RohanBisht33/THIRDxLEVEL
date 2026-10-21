"""
Unit tests for Phase 4: QA Runner through the real ACP message path.
Tests all 6 QA cases strictly using ACP ChatMessage and ChatAcknowledgement protocol messages.
"""

import asyncio
from datetime import datetime, timezone
import pytest
from uuid import uuid4

from uagents import Context
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
)

from core.catalog import Business, Item
from core.qa import get_standard_qa_cases, evaluate_qa_response, QAReport
from core.store import MemoryStore
from agents.storefront_agent import create_storefront_agent, create_text_chat_message


class MockACPContext:
    """Mock context simulating uagents Context for protocol testing."""
    def __init__(self, agent_address: str, storage: dict):
        self.agent_address = agent_address
        self.storage = storage
        self.sent_messages = []
        self.logger = self

    async def send(self, destination: str, message: object):
        self.sent_messages.append((destination, message))

    def info(self, msg: str):
        pass

    def debug(self, msg: str):
        pass

    def error(self, msg: str):
        pass


@pytest.fixture
def qa_storefront_setup():
    store = MemoryStore()
    bakery = Business(
        id="golden_crust",
        name="Golden Crust Artisan Bakery",
        hours="7:00 AM - 3:00 PM",
        policies="Cancellations within 1hr non-refundable.",
        version=1,
        items=[
            Item(name="Sourdough Loaf", price=6.50, currency="USD", stock=15),
            Item(name="Croissant", price=3.50, currency="USD", stock=25),
            Item(name="Almond Danish", price=4.75, currency="USD", stock=0),
        ],
    )
    store.save_business(bakery)
    agent = create_storefront_agent(port=8096, store=store)
    return agent, store


class TestQAProtocolPathRunner:
    def test_all_6_qa_cases_through_real_acp_message_path(self, qa_storefront_setup):
        async def run_protocol_test():
            agent, store = qa_storefront_setup
            chat_proto = [p for p in agent.protocols.values() if p.name == "AgentChatProtocol"][0]
            
            # Get ChatMessage handler function from protocol
            chat_msg_digest = [d for d, m in chat_proto.models.items() if m == ChatMessage][0]
            handler = chat_proto.signed_message_handlers.get(chat_msg_digest) or chat_proto.unsigned_message_handlers[chat_msg_digest]

            cases = get_standard_qa_cases()
            tester_address = "agent1q_qa_scripted_runner"
            qa_results = []

            ctx_map = {
                "qa_1_price_lookup": {"price": "6.50"},
                "qa_2_valid_order": {"total": "7.00"},
                "qa_3_out_of_stock": {},
                "qa_4_unknown_question": {},
                "qa_5_prompt_injection": {"price": "6.50"},
                "qa_6_duplicate_order": {},
            }

            for case in cases:
                # 1. Build authentic ACP ChatMessage
                inbound_msg = ChatMessage(
                    timestamp=datetime.now(timezone.utc),
                    msg_id=uuid4(),
                    content=[TextContent(type="text", text=case.input_message)],
                )

                # 2. Invoke handler through authentic ACP message path
                mock_ctx = MockACPContext(agent.address, {})
                await handler(mock_ctx, tester_address, inbound_msg)

                # 3. Verify that a ChatAcknowledgement was returned for every incoming message
                acks = [msg for dst, msg in mock_ctx.sent_messages if isinstance(msg, ChatAcknowledgement)]
                assert len(acks) >= 1
                assert acks[0].acknowledged_msg_id == inbound_msg.msg_id

                # 4. Verify that a response ChatMessage was returned
                chat_replies = [msg for dst, msg in mock_ctx.sent_messages if isinstance(msg, ChatMessage)]
                assert len(chat_replies) >= 1
                reply_text = chat_replies[0].text()

                # 5. Evaluate response against QA criteria
                eval_res = evaluate_qa_response(case, reply_text, ctx_map.get(case.id))
                qa_results.append(eval_res)

            # 6. Verify full report
            report = QAReport(
                business_id="golden_crust",
                business_name="Golden Crust Artisan Bakery",
                results=qa_results,
            )

            assert report.total_count == 6
            assert report.all_passed is True
            assert report.passed_count == 6
            assert report.failed_count == 0

            # Check text report rendering
            text_summary = report.to_text_report()
            assert "ALL TESTS PASSED [PASS] (6/6)" in text_summary
            assert "qa_1_price_lookup" not in text_summary  # Friendly name used instead
            assert "Price Lookup" in text_summary
            assert "Duplicate Order Idempotency" in text_summary

        asyncio.run(run_protocol_test())
