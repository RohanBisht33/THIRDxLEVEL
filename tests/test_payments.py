"""
Unit tests for core/payments.py
Validates Payment Protocol seller flow, Mock Rail, Stripe test rail, and FET testnet rail.
Never uses live money.
"""

import pytest
from core.catalog import Business, Item
from core.orders import CartItem, Order, OrderState
from core.payments import PaymentManager, PaymentMode, attach_seller_payment_protocol
from core.store import MemoryStore
from uagents import Agent
from uagents_core.contrib.protocols.payment import (
    CommitPayment,
    CompletePayment,
    CancelPayment,
    Funds,
    RejectPayment,
    RequestPayment,
)


@pytest.fixture
def sample_order() -> Order:
    return Order(
        order_id="ord_pay_001",
        business_id="golden_crust",
        catalog_version=1,
        customer_id="cust_buyer_001",
        items=[
            CartItem(item_name="Sourdough Loaf", unit_price=6.50, currency="USD", quantity=2)
        ],
        state=OrderState.CONFIRMED,
    )


class TestPaymentProtocolFlow:
    def test_mock_rail_successful_settlement(self, sample_order):
        mgr = PaymentManager(mode=PaymentMode.MOCK)
        seller_addr = "agent1q_seller_address"

        # 1. Seller issues RequestPayment
        req = mgr.create_payment_request(sample_order, seller_address=seller_addr)
        assert isinstance(req, RequestPayment)
        assert req.reference == sample_order.order_id
        assert len(req.accepted_funds) == 1
        assert float(req.accepted_funds[0].amount) == 13.00
        assert req.accepted_funds[0].currency == "USD"
        assert sample_order.state == OrderState.PAYMENT_REQUESTED

        # 2. Buyer issues CommitPayment
        commit = CommitPayment(
            funds=Funds(amount=13.00, currency="USD"),
            recipient=seller_addr,
            transaction_id="mock_tx_abc_123",
            reference=sample_order.order_id,
        )

        # 3. Seller verifies and completes
        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(commit, sample_order)
        assert success is True
        assert isinstance(complete_msg, CompletePayment)
        assert complete_msg.transaction_id == "mock_tx_abc_123"
        assert cancel_msg is None

        # 4. Check order finalized
        assert sample_order.state == OrderState.PAID
        assert sample_order.payment_transaction_id == "mock_tx_abc_123"

    def test_mock_rail_buyer_rejection(self, sample_order):
        mgr = PaymentManager(mode=PaymentMode.MOCK)
        mgr.create_payment_request(sample_order, seller_address="agent1q_seller")

        reject = RejectPayment(reason="Buyer cancelled payment drawer")
        mgr.handle_rejection(reject, sample_order)

        assert sample_order.state == OrderState.CANCELLED
        assert "Buyer cancelled" in sample_order.notes

    def test_stripe_test_rail_valid_and_invalid(self, sample_order):
        mgr = PaymentManager(mode=PaymentMode.STRIPE_TEST)
        seller_addr = "agent1q_seller"

        mgr.create_payment_request(sample_order, seller_address=seller_addr)

        # Valid Stripe test intent
        valid_commit = CommitPayment(
            funds=Funds(amount=13.00, currency="USD"),
            recipient=seller_addr,
            transaction_id="pi_test_3N8cABCDEF123456",
            reference=sample_order.order_id,
        )
        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(valid_commit, sample_order)
        assert success is True
        assert sample_order.state == OrderState.PAID
        assert complete_msg.transaction_id == "pi_test_3N8cABCDEF123456"

        # Invalid Stripe transaction
        sample_order.state = OrderState.PAYMENT_REQUESTED
        invalid_commit = CommitPayment(
            funds=Funds(amount=13.00, currency="USD"),
            recipient=seller_addr,
            transaction_id="live_pi_unsafe_123",
            reference=sample_order.order_id,
        )
        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(invalid_commit, sample_order)
        assert success is False
        assert sample_order.state == OrderState.FAILED
        assert isinstance(cancel_msg, CancelPayment)

    def test_fet_testnet_rail_valid_and_invalid(self, sample_order):
        mgr = PaymentManager(mode=PaymentMode.FET_TESTNET)
        seller_addr = "agent1q_seller"

        mgr.create_payment_request(sample_order, seller_address=seller_addr)

        # Valid 64-char hex Dorado testnet hash
        valid_hash = "a" * 64
        valid_commit = CommitPayment(
            funds=Funds(amount=13.00, currency="USD"),
            recipient=seller_addr,
            transaction_id=valid_hash,
            reference=sample_order.order_id,
        )
        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(valid_commit, sample_order)
        assert success is True
        assert sample_order.state == OrderState.PAID
        assert complete_msg.transaction_id == valid_hash

        # Invalid testnet hash
        sample_order.state = OrderState.PAYMENT_REQUESTED
        invalid_commit = CommitPayment(
            funds=Funds(amount=13.00, currency="USD"),
            recipient=seller_addr,
            transaction_id="invalid_hash_too_short",
            reference=sample_order.order_id,
        )
        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(invalid_commit, sample_order)
        assert success is False
        assert sample_order.state == OrderState.FAILED

    def test_attach_seller_payment_protocol_to_agent(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        store = MemoryStore()
        agent = Agent(name="test_seller_agent", port=8094, loop=loop)
        proto = attach_seller_payment_protocol(agent, store)
        assert proto.name == "StorefrontPaymentProtocol"
        assert proto in agent.protocols.values()
