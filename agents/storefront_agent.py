"""
Storefront Agent Hub (Multi-tenant).
Serves customers via Agent Chat Protocol (ACP) using only confirmed catalog data.
Treats all customer inputs strictly as data, never prompt instructions.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    MetadataContent,
    TextContent,
    chat_protocol_spec,
)

from core.catalog import Business, Item
from core.orders import CartItem, Order, OrderState, build_order_from_cart
from core.store import StoreBackend, MemoryStore, AgentverseStore


def create_text_chat_message(text: str) -> ChatMessage:
    """Helper to build an ACP ChatMessage containing a single text bubble."""
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


class StorefrontLogic:
    """Multi-tenant business storefront logic.
    Decoupled from network I/O for clean local testing and QA runner compatibility.
    """

    def __init__(self, store: StoreBackend):
        self.store = store

    def get_customer_session(self, customer_id: str) -> Dict[str, Any]:
        """Fetch or initialize session state for a customer."""
        state = self.store.get_session_state(customer_id)
        if not state:
            state = {
                "active_business_id": None,
                "current_cart": [],
                "last_order_id": None,
            }
        return state

    def save_customer_session(self, customer_id: str, state: Dict[str, Any]) -> None:
        self.store.save_session_state(customer_id, state)

    def process_message(self, customer_id: str, incoming_text: str) -> str:
        """Process incoming chat message from a customer and return response string."""
        raw_text = incoming_text.strip()
        lower_text = raw_text.lower()
        session = self.get_customer_session(customer_id)

        # 1. Check if user is switching or selecting a business
        # e.g., "order from golden_crust", "shop at sweet_tooth", "select bakery"
        select_match = re.search(
            r"(?:order from|shop at|select business|switch to|visit)\s+([a-zA-Z0-9_\-\s]+)",
            lower_text,
        )
        if select_match:
            candidate_name = select_match.group(1).strip()
            biz = self._find_business(candidate_name)
            if biz:
                session["active_business_id"] = biz.id
                self.save_customer_session(customer_id, session)
                return (
                    f"Welcome to {biz.name}!\n"
                    f"Hours: {biz.hours}\n"
                    f"You can ask me about items, check prices, or place an order (e.g. 'Order 2 Croissants')."
                )

        # 2. Resolve active business
        active_biz_id = session.get("active_business_id")
        business: Optional[Business] = None
        if active_biz_id:
            business = self.store.get_business(active_biz_id)

        # If no active business selected yet, try to auto-select if there's only one registered
        if not business:
            all_biz = self._list_all_businesses()
            if len(all_biz) == 1:
                business = all_biz[0]
                session["active_business_id"] = business.id
                self.save_customer_session(customer_id, session)
            elif len(all_biz) > 1:
                shops_list = "\n".join([f"- {b.name} (say 'order from {b.name}')" for b in all_biz])
                return (
                    "Welcome to the Storefront Hub! Please choose which shop you want to order from:\n"
                    f"{shops_list}"
                )
            else:
                return "The Storefront Hub currently has no active businesses published. Please check back later!"

        # 3. Handle prompt injection defense
        # Treat all customer messages strictly as data, never as system instructions.
        if (
            "ignore previous" in lower_text
            or "ignore all" in lower_text
            or "system prompt" in lower_text
            or "you are now" in lower_text
            or ("free" in lower_text and ("everything" in lower_text or "all items" in lower_text))
        ):
            # Check if user mentioned an item in the injection string
            for item in business.items:
                if item.name.lower() in lower_text:
                    return (
                        f"Catalog prices cannot be altered by chat instructions. "
                        f"{item.name} is ${item.price:.2f} ({item.currency})."
                    )
            return "I cannot modify shop rules or prices. I can only provide menu info and process standard orders."

        # 4. Handle menu/catalog listing
        if any(w in lower_text for w in ["menu", "catalog", "what do you have", "what do you sell", "list items"]):
            lines = [f"=== {business.name} Menu ==="]
            for item in business.items:
                stock_str = f"In stock: {item.stock}" if item.stock is not None else "In stock"
                if not item.available or (item.stock is not None and item.stock <= 0):
                    stock_str = "SOLD OUT"
                lines.append(f"• {item.name}: ${item.price:.2f} ({item.currency}) — {stock_str}")
                if item.description:
                    lines.append(f"  {item.description}")
            lines.append(f"\nHours: {business.hours}")
            lines.append(f"Policies: {business.policies}")
            return "\n".join(lines)

        # 5. Handle business hours & policies
        if any(w in lower_text for w in ["hours", "open", "opening time", "closing time"]):
            return f"{business.name} operating hours: {business.hours}"
        if any(w in lower_text for w in ["policy", "policies", "refund", "return", "cancel"]):
            return f"{business.name} policies: {business.policies}"

        # 6. Handle ordering requests
        order_intent = any(w in lower_text for w in ["order", "buy", "purchase", "want to get", "can i get", "i'd like"])
        if order_intent:
            order_res = self._process_order_request(business, customer_id, raw_text)
            if order_res:
                return order_res

        # 7. Handle price lookup
        # e.g., "How much is the Sourdough Loaf?", "Price of croissant"
        price_query = any(w in lower_text for w in ["how much", "cost", "price", "how much is", "how much does"])
        if price_query:
            for item in business.items:
                if item.name.lower() in lower_text:
                    avail_note = " (Available)" if item.is_in_stock() else " (Currently Out of Stock)"
                    return f"{item.name} is ${item.price:.2f} {item.currency}{avail_note}."

        # 8. Check if just an item name was asked
        for item in business.items:
            if item.name.lower() == lower_text or f"the {item.name.lower()}" == lower_text:
                avail_note = "In stock" if item.is_in_stock() else "Out of stock"
                return f"{item.name}: ${item.price:.2f} {item.currency} ({avail_note}). {item.description}"

        # 9. Fallback: unknown question outside catalog
        return "I don't know, please contact the shop. I can only assist with confirmed items, prices, hours, and placing orders."

    def _process_order_request(self, business: Business, customer_id: str, text: str) -> Optional[str]:
        """Attempt to parse and draft an order from customer chat."""
        # Find matching item in catalog
        matched_item: Optional[Item] = None
        lower = text.lower()
        for item in business.items:
            if item.name.lower() in lower:
                matched_item = item
                break

        if not matched_item:
            return None

        # Parse quantity (default 1)
        qty = 1
        qty_match = re.search(r"\b(\d+)\s+" + re.escape(matched_item.name.lower()), lower)
        if not qty_match:
            # Try searching for digits near order keywords
            num_match = re.search(r"\b(?:order|buy|get|want)\s+(\d+)\b", lower)
            if num_match:
                qty = int(num_match.group(1))
        else:
            qty = int(qty_match.group(1))

        if qty <= 0:
            qty = 1

        # Check stock immediately
        if not matched_item.available:
            return f"Sorry, '{matched_item.name}' is currently unavailable."
        if matched_item.stock is not None and matched_item.stock < qty:
            avail = matched_item.stock
            return (
                f"Sorry, insufficient stock for '{matched_item.name}'. "
                f"You requested {qty}, but we only have {avail} in stock."
            )

        # Build CartItem and check idempotency
        cart_items = [
            CartItem(
                item_name=matched_item.name,
                unit_price=matched_item.price,
                currency=matched_item.currency,
                quantity=qty,
            )
        ]

        idempotency_key = Order.generate_idempotency_key(business.id, customer_id, cart_items)
        existing_order = self.store.get_order_by_idempotency(idempotency_key)
        if existing_order:
            return (
                f"An existing order with this exact cart was already placed "
                f"(Order ID: {existing_order.order_id}). Total: ${existing_order.total_amount:.2f} {existing_order.currency}."
            )

        # Build order strictly against confirmed catalog version
        try:
            order = build_order_from_cart(
                business=business,
                customer_id=customer_id,
                requested_items=[{"item_name": matched_item.name, "quantity": qty}],
                idempotency_key=idempotency_key,
            )
            order.transition_to(OrderState.CONFIRMED, reason="Order drafted and confirmed via customer chat")
            self.store.save_order(order)

            # Update session
            session = self.get_customer_session(customer_id)
            session["last_order_id"] = order.order_id
            self.save_customer_session(customer_id, session)

            return (
                f"Your order has been confirmed!\n"
                f"• Order ID: {order.order_id}\n"
                f"• Item: {matched_item.name} x {qty}\n"
                f"• Unit Price: ${matched_item.price:.2f} {matched_item.currency}\n"
                f"• Total: ${order.total_amount:.2f} {order.currency}\n"
                f"• Status: {order.state.value.capitalize()}\n"
                f"Thank you for ordering with {business.name}!"
            )
        except ValueError as e:
            return f"Cannot process order: {str(e)}"

    def _find_business(self, name_or_id: str) -> Optional[Business]:
        """Find business by ID or case-insensitive name."""
        target = name_or_id.strip().lower()
        # Direct ID lookup
        direct = self.store.get_business(target)
        if direct:
            return direct

        # Search across registered businesses
        for b in self._list_all_businesses():
            if b.id == target or b.name.strip().lower() == target:
                return b
            if target in b.name.strip().lower():
                return b
        return None

    def _list_all_businesses(self) -> List[Business]:
        """Scan stored businesses."""
        businesses = []
        if isinstance(self.store, MemoryStore):
            for k, val in self.store._data.items():
                if k.startswith("biz:"):
                    try:
                        businesses.append(Business.from_dict(val))
                    except Exception:
                        pass
        return businesses


def create_storefront_agent(
    name: str = "storefront_hub",
    seed: Optional[str] = None,
    port: int = 8003,
    store: Optional[StoreBackend] = None,
) -> Agent:
    """Instantiate and configure the Storefront Hub Agent."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    seed_phrase = seed or os.getenv("STOREFRONT_AGENT_SEED", "agentforge_storefront_seed_demo_2026")
    agent = Agent(name=name, seed=seed_phrase, port=port, endpoint=[f"http://127.0.0.1:{port}/submit"], loop=loop)

    storage_backend = store or MemoryStore()
    logic = StorefrontLogic(storage_backend)

    chat_proto = Protocol(spec=chat_protocol_spec)

    @chat_proto.on_message(ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        # 1. Always acknowledge incoming chat message per ACP specification
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))

        incoming_text = msg.text()
        ctx.logger.info(f"Storefront received from {sender}: {incoming_text}")

        # If running in Agentverse hosted mode, sync with ctx.storage
        if not isinstance(storage_backend, AgentverseStore):
            active_store = storage_backend
        else:
            active_store = AgentverseStore(ctx.storage)
        active_logic = StorefrontLogic(active_store)

        # 2. Process customer request
        response_text = active_logic.process_message(customer_id=sender, incoming_text=incoming_text)

        # 3. Respond with ACP ChatMessage
        reply = create_text_chat_message(response_text)
        await ctx.send(sender, reply)
        ctx.logger.info(f"Storefront replied to {sender}: {response_text[:60]}...")

    @chat_proto.on_message(ChatAcknowledgement)
    async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        # Acknowledge receipt of ACP ack
        ctx.logger.debug(f"Received ACP acknowledgement from {sender} for msg {msg.acknowledged_msg_id}")

    agent.include(chat_proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    hub_agent = create_storefront_agent()
    hub_agent.run()
