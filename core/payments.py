"""
Payment Protocol integration for AgentForge.
Implements the seller role in the uAgents payment protocol:
RequestPayment -> CommitPayment -> CompletePayment, plus RejectPayment and CancelPayment.
Supports env-switchable test rails: mock | stripe_test | fet_testnet. Never uses live money.
"""

from __future__ import annotations
from enum import Enum
import os
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from .orders import Order, OrderState
from .store import StoreBackend


class PaymentMode(str, Enum):
    MOCK = "mock"
    STRIPE_TEST = "stripe_test"
    FET_TESTNET = "fet_testnet"


def get_current_payment_mode() -> PaymentMode:
    """Read payment mode from env var PAYMENT_MODE, defaulting to 'mock'."""
    raw = os.getenv("PAYMENT_MODE", "mock").strip().lower()
    try:
        return PaymentMode(raw)
    except ValueError:
        return PaymentMode.MOCK


class PaymentManager:
    """Manages payment invoicing, verification, and settlement across test rails."""

    def __init__(self, mode: Optional[PaymentMode] = None):
        self.mode = mode or get_current_payment_mode()

    def create_payment_request(
        self,
        order: Order,
        seller_address: str,
        deadline_seconds: int = 300,
    ) -> RequestPayment:
        """Create a compliant RequestPayment invoice and transition order state."""
        # Record payment requested in state machine
        if order.state == OrderState.DRAFT:
            order.transition_to(OrderState.CONFIRMED, reason="Order confirmed prior to invoice")
        if order.state == OrderState.CONFIRMED:
            order.transition_to(
                OrderState.PAYMENT_REQUESTED,
                reason=f"Payment requested via {self.mode.value} rail",
            )

        funds = [
            Funds(
                amount=order.total_amount,
                currency=order.currency,
                payment_method=self._get_payment_method_name(),
            )
        ]

        return RequestPayment(
            accepted_funds=funds,
            recipient=seller_address,
            deadline_seconds=deadline_seconds,
            reference=order.order_id,
            description=f"Payment for order {order.order_id} at business {order.business_id}",
            metadata={"payment_mode": self.mode.value, "catalog_version": str(order.catalog_version)},
        )

    def verify_and_settle_payment(
        self,
        commit_msg: CommitPayment,
        order: Order,
    ) -> Tuple[bool, Optional[CompletePayment], Optional[CancelPayment]]:
        """Verify committed payment against configured test rail and finalize order."""
        tx_id = commit_msg.transaction_id.strip() if commit_msg.transaction_id else ""
        if not tx_id:
            return False, None, CancelPayment(transaction_id="unknown", reason="Missing transaction ID in CommitPayment")

        # 1. Mock Rail Verification
        if self.mode == PaymentMode.MOCK:
            order.transition_to(OrderState.PAID, reason=f"Mock payment settled for tx {tx_id}")
            order.payment_transaction_id = tx_id
            return True, CompletePayment(transaction_id=tx_id), None

        # 2. Stripe Test Mode Verification
        elif self.mode == PaymentMode.STRIPE_TEST:
            # Accepts valid Stripe test intent / charge / mock tokens (e.g. pi_test_*, ch_test_*, pm_card_*)
            is_valid_stripe_test = bool(
                tx_id.startswith("pi_test_")
                or tx_id.startswith("ch_test_")
                or tx_id.startswith("pm_")
                or "test" in tx_id.lower()
            )
            if is_valid_stripe_test:
                order.transition_to(OrderState.PAID, reason=f"Stripe test mode settled for tx {tx_id}")
                order.payment_transaction_id = tx_id
                return True, CompletePayment(transaction_id=tx_id), None
            else:
                order.transition_to(OrderState.FAILED, reason=f"Invalid Stripe test transaction format: {tx_id}")
                return False, None, CancelPayment(transaction_id=tx_id, reason="Stripe test transaction verification failed")

        # 3. FET Testnet Verification
        elif self.mode == PaymentMode.FET_TESTNET:
            # Accepts 64-character hex testnet hash or testnet tx identifier
            is_valid_testnet_tx = bool(
                (len(tx_id) == 64 and bool(re.match(r"^[0-9a-fA-F]{64}$", tx_id)))
                or tx_id.startswith("dorado_tx_")
                or "testnet" in tx_id.lower()
            )
            if is_valid_testnet_tx:
                order.transition_to(OrderState.PAID, reason=f"FET testnet settled for tx {tx_id}")
                order.payment_transaction_id = tx_id
                return True, CompletePayment(transaction_id=tx_id), None
            else:
                order.transition_to(OrderState.FAILED, reason=f"Invalid FET testnet transaction hash: {tx_id}")
                return False, None, CancelPayment(transaction_id=tx_id, reason="FET testnet transaction verification failed")

        return False, None, CancelPayment(transaction_id=tx_id, reason=f"Unsupported payment mode {self.mode}")

    def handle_rejection(self, reject_msg: RejectPayment, order: Order) -> None:
        """Handle buyer payment rejection."""
        reason = reject_msg.reason or "Buyer declined payment"
        order.transition_to(OrderState.CANCELLED, reason=f"Payment rejected: {reason}")

    def _get_payment_method_name(self) -> str:
        if self.mode == PaymentMode.STRIPE_TEST:
            return "stripe_test"
        elif self.mode == PaymentMode.FET_TESTNET:
            return "fet_dorado_testnet"
        return "mock_settlement"


def attach_seller_payment_protocol(
    agent: Agent,
    store: StoreBackend,
    payment_manager: Optional[PaymentManager] = None,
) -> Protocol:
    """Bind the Payment Protocol seller role to an agent."""
    mgr = payment_manager or PaymentManager()
    payment_proto = Protocol(name="StorefrontPaymentProtocol", version="0.1.0")

    @payment_proto.on_message(model=CommitPayment, replies={CompletePayment, CancelPayment})
    async def handle_commit_payment(ctx: Context, sender: str, msg: CommitPayment):
        ctx.logger.info(f"Payment received from {sender}: tx_id={msg.transaction_id}, ref={msg.reference}")
        
        # Locate corresponding order
        order: Optional[Order] = None
        if msg.reference:
            order = store.get_order(msg.reference)

        if not order:
            ctx.logger.error(f"Cannot find order for payment reference {msg.reference}")
            await ctx.send(
                sender,
                CancelPayment(transaction_id=msg.transaction_id, reason=f"Order {msg.reference} not found"),
            )
            return

        success, complete_msg, cancel_msg = mgr.verify_and_settle_payment(msg, order)
        store.save_order(order)

        if success and complete_msg:
            ctx.logger.info(f"Payment completed successfully for order {order.order_id}")
            await ctx.send(sender, complete_msg)
        elif cancel_msg:
            ctx.logger.warning(f"Payment cancelled for order {order.order_id}: {cancel_msg.reason}")
            await ctx.send(sender, cancel_msg)

    @payment_proto.on_message(model=RejectPayment, replies=set())
    async def handle_reject_payment(ctx: Context, sender: str, msg: RejectPayment):
        ctx.logger.info(f"Payment rejected by {sender}: reason={msg.reason}")

    agent.include(payment_proto)
    return payment_proto
