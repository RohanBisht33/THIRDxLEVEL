"""
Builder Agent for AgentForge.
ASI:One-facing orchestrator guiding business owners through:
Intake -> Extraction -> Review Gate (Mandatory) -> Deploy -> Auto-QA -> Publish Link.
Persists per-session state in store to support seamless resumption.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
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
from core.qa import run_local_qa_suite, QAReport
from core.store import StoreBackend, MemoryStore, AgentverseStore
from .extractor_agent import extract_catalog
from .storefront_agent import StorefrontLogic, create_text_chat_message


# Flow state constants
STATE_IDLE = "idle"
STATE_WAITING_FOR_MENU = "waiting_for_menu"
STATE_REVIEW = "review"
STATE_PUBLISHED = "published"


class BuilderLogic:
    """Conversational orchestration logic for Builder Agent."""

    def __init__(self, store: StoreBackend, storefront_address: Optional[str] = None):
        self.store = store
        self.storefront_address = storefront_address or os.getenv(
            "STOREFRONT_AGENT_ADDRESS", "agent1qf0tawv89l5qymv6aqdvqq79chmaf0gz6zsu8y7djrfymnx3sufmwtcxjzh"
        )

    def get_session(self, owner_id: str) -> Dict[str, Any]:
        """Fetch or initialize owner conversational state."""
        state = self.store.get_session_state(f"builder:{owner_id}")
        if not state:
            state = {
                "stage": STATE_IDLE,
                "business_name": "My Shop",
                "pending_catalog": None,
                "confirmed_business_id": None,
            }
        return state

    def save_session(self, owner_id: str, state: Dict[str, Any]) -> None:
        self.store.save_session_state(f"builder:{owner_id}", state)

    def process_message(self, owner_id: str, message_text: str) -> str:
        """State-machine handler for owner requests."""
        raw = message_text.strip()
        lower = raw.lower()
        session = self.get_session(owner_id)
        stage = session.get("stage", STATE_IDLE)

        # Allow user to reset anytime
        if lower in ["cancel", "reset", "start over"]:
            session["stage"] = STATE_IDLE
            session["pending_catalog"] = None
            self.save_session(owner_id, session)
            return "Session reset. Say 'make an agent for my bakery' or paste a menu to begin."

        # --- STAGE: IDLE ---
        if stage == STATE_IDLE:
            # Check if user passed menu text/CSV directly in the first message
            if "$" in raw or ("," in raw and "\n" in raw) or len(raw.split("\n")) >= 3:
                return self._handle_extraction(owner_id, session, raw)

            # Check for business creation prompt
            # e.g., "make an agent for my bakery Golden Crust", "make an agent for Golden Crust"
            clean_intake = re.sub(r"^(?:make|build|create)\s+(?:an?\s+agent\s+)?(?:for\s+)?(?:my\s+)?(?:bakery|shop|store|restaurant|cafe|salon)?\s*", "", lower).strip()
            if clean_intake and len(clean_intake) > 1:
                biz_name = clean_intake.title()
            else:
                biz_name = "My Artisan Shop"

            session["business_name"] = biz_name
            session["stage"] = STATE_WAITING_FOR_MENU
            self.save_session(owner_id, session)

            return (
                f"Welcome to AgentForge! Let's build a storefront agent for {biz_name}.\n\n"
                f"Please paste your menu text or CSV catalog below.\n"
                f"For example:\n"
                f"• Sourdough Loaf: $6.50 (In stock: 15)\n"
                f"• Butter Croissant: $3.50 (In stock: 25)\n"
                f"• Almond Danish: $4.75 (SOLD OUT)"
            )

        # --- STAGE: WAITING_FOR_MENU ---
        if stage == STATE_WAITING_FOR_MENU:
            return self._handle_extraction(owner_id, session, raw)

        # --- STAGE: REVIEW (MANDATORY GATE) ---
        if stage == STATE_REVIEW:
            pending = session.get("pending_catalog")
            if not pending:
                session["stage"] = STATE_IDLE
                self.save_session(owner_id, session)
                return "No pending catalog found. Please paste your menu again."

            # Check for confirmation
            if lower in ["confirm", "yes", "publish", "looks good", "approve", "deploy"]:
                return self._deploy_and_qa(owner_id, session)

            # Check for edits: e.g. "edit: Croissant, price, 4.00" or "edit Sourdough stock 10"
            if lower.startswith("edit") or lower.startswith("change"):
                edit_res = self._handle_catalog_edit(session, raw)
                self.save_session(owner_id, session)
                return edit_res

            # Prompt user to choose
            return (
                "Your catalog is currently pending confirmation.\n"
                "• Type 'CONFIRM' to deploy and run automated QA.\n"
                "• Type 'EDIT: <item_name>, <field>, <new_value>' to adjust.\n"
                "• Type 'CANCEL' to start over."
            )

        # --- STAGE: PUBLISHED ---
        if stage == STATE_PUBLISHED:
            if "new" in lower or "create" in lower or "again" in lower or "another" in lower:
                session["stage"] = STATE_IDLE
                session["pending_catalog"] = None
                self.save_session(owner_id, session)
                return "Ready to build another storefront agent! What is your business name?"

            biz_id = session.get("confirmed_business_id")
            return (
                f"Your storefront agent for '{biz_id}' is live and active.\n"
                f"Storefront Address: {self.storefront_address}\n"
                f"Say 'create new agent' to build another."
            )

        return "I didn't recognize that command. Type 'CONFIRM', 'EDIT: ...', or 'RESET'."

    def _handle_extraction(self, owner_id: str, session: Dict[str, Any], raw_content: str) -> str:
        """Call extractor and format review table."""
        biz_name = session.get("business_name", "My Artisan Shop")
        success, catalog_dict, err = extract_catalog(raw_content, business_name=biz_name)

        if not success or not catalog_dict:
            return (
                f"Sorry, extraction failed: {err}\n"
                f"Please ensure your text includes item names and prices (e.g. 'Item: $5.00') and try again."
            )

        session["pending_catalog"] = catalog_dict
        session["stage"] = STATE_REVIEW
        self.save_session(owner_id, session)

        # Build clean plain-text review table
        items = catalog_dict.get("items", [])
        lines = [
            f"=== Extracted Catalog Review: {catalog_dict.get('name')} ===",
            f"Operating Hours: {catalog_dict.get('hours', 'Standard hours')}",
            f"Policies: {catalog_dict.get('policies', 'Standard policies')}",
            "",
            "Extracted Items:",
        ]

        low_conf_found = False
        for idx, it in enumerate(items, 1):
            conf = float(it.get("confidence", 1.0))
            conf_flag = ""
            if conf < 0.85:
                conf_flag = " [LOW CONFIDENCE - PLEASE VERIFY]"
                low_conf_found = True

            stock_val = it.get("stock")
            stock_str = f"Stock: {stock_val}" if stock_val is not None else "Stock: Unlimited"
            if not it.get("available") or (stock_val is not None and stock_val <= 0):
                stock_str = "SOLD OUT"

            lines.append(
                f"{idx}. {it.get('name')}: ${float(it.get('price', 0)):.2f} {it.get('currency', 'USD')} "
                f"({stock_str}){conf_flag}"
            )
            if it.get("description"):
                lines.append(f"   Note: {it.get('description')}")

        lines.append("")
        if low_conf_found:
            lines.append("Notice: Some items have low confidence scores highlighted above.")
        lines.append(
            "MANDATORY GATE: Please review the extracted catalog above.\n"
            "• Type 'CONFIRM' to deploy and run automated QA.\n"
            "• Type 'EDIT: <item_name>, <field>, <new_value>' to adjust (e.g. 'EDIT: Sourdough, price, 7.00').\n"
            "• Type 'CANCEL' to discard."
        )

        return "\n".join(lines)

    def _handle_catalog_edit(self, session: Dict[str, Any], raw_edit: str) -> str:
        """Handle inline edits to pending catalog items."""
        # Examples: "edit: Sourdough Loaf, price, 7.00", "edit: Croissant, stock, 10"
        parts = [p.strip() for p in raw_edit.replace("edit:", "").replace("edit", "").split(",")]
        if len(parts) < 3:
            return (
                "Invalid edit format. Please use:\n"
                "'EDIT: <item_name>, <price/stock/available>, <new_value>'\n"
                "Example: 'EDIT: Sourdough Loaf, price, 7.00'"
            )

        item_target, field_name, new_val = parts[0].lower(), parts[1].lower(), parts[2]
        pending = session["pending_catalog"]
        items = pending.get("items", [])

        matched = False
        for it in items:
            if it["name"].lower() == item_target or item_target in it["name"].lower():
                matched = True
                if field_name == "price":
                    it["price"] = float(re.sub(r"[^\d.]", "", new_val))
                elif field_name == "stock":
                    it["stock"] = int(re.sub(r"[^\d]", "", new_val))
                    it["available"] = it["stock"] > 0
                elif field_name in ["available", "in_stock"]:
                    it["available"] = new_val.lower() in ["true", "yes", "available", "1"]
                elif field_name == "name":
                    it["name"] = new_val.strip()
                it["confidence"] = 1.0  # Owner manually verified
                break

        if not matched:
            return f"Item matching '{parts[0]}' not found in catalog. Please check spelling."

        # Increment version
        pending["version"] = pending.get("version", 1) + 1
        return (
            f"Successfully updated '{parts[0]}'. Version is now {pending['version']}.\n"
            f"Type 'CONFIRM' to publish or make further edits."
        )

    def _deploy_and_qa(self, owner_id: str, session: Dict[str, Any]) -> str:
        """Deploy confirmed catalog and run the 6 scripted QA tests through real logic path."""
        pending_dict = session["pending_catalog"]
        business = Business.from_dict(pending_dict)

        # 1. Save confirmed catalog into storefront storage
        self.store.save_business(business)

        # 2. Run automated QA suite against Storefront Hub
        hub_logic = StorefrontLogic(self.store)
        qa_customer_id = f"qa_runner_{uuid4().hex[:6]}"

        def qa_dispatch(prompt: str) -> str:
            # Set active business for QA session
            qa_session = hub_logic.get_customer_session(qa_customer_id)
            qa_session["active_business_id"] = business.id
            hub_logic.save_customer_session(qa_customer_id, qa_session)
            return hub_logic.process_message(qa_customer_id, prompt)

        qa_report = run_local_qa_suite(business, qa_dispatch)

        # 3. Update session to published
        session["stage"] = STATE_PUBLISHED
        session["confirmed_business_id"] = business.id
        session["pending_catalog"] = None
        self.save_session(owner_id, session)

        # 4. Format combined deployment and QA report
        qa_text = qa_report.to_text_report()
        status_msg = (
            f"[SUCCESS] DEPLOYMENT COMPLETED!\n\n"
            f"{qa_text}\n\n"
            f"Storefront Details:\n"
            f"• Business Name: {business.name}\n"
            f"• Business ID: {business.id}\n"
            f"• Catalog Version: {business.version}\n"
            f"• Storefront Agent Address: {self.storefront_address}\n\n"
            f"Your storefront is now live on the Storefront Hub! Customers can say "
            f"'order from {business.name}' in ASI:One to browse and buy."
        )
        return status_msg


def create_builder_agent(
    name: str = "builder_agent",
    seed: Optional[str] = None,
    port: int = 8001,
    store: Optional[StoreBackend] = None,
    storefront_address: Optional[str] = None,
) -> Agent:
    """Instantiate the Builder Agent."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    seed_phrase = seed or os.getenv("BUILDER_AGENT_SEED", "agentforge_builder_seed_demo_2026")
    agent = Agent(name=name, seed=seed_phrase, port=port, endpoint=[f"http://127.0.0.1:{port}/submit"], loop=loop)

    storage_backend = store or MemoryStore()
    builder_logic = BuilderLogic(storage_backend, storefront_address)

    chat_proto = Protocol(spec=chat_protocol_spec)

    @chat_proto.on_message(ChatMessage)
    async def handle_chat_message(ctx: Context, sender: str, msg: ChatMessage):
        await ctx.send(sender, ChatAcknowledgement(acknowledged_msg_id=msg.msg_id))
        incoming_text = msg.text()
        ctx.logger.info(f"Builder received from owner {sender}: {incoming_text}")

        # Sync storage if in Agentverse mode
        if not isinstance(storage_backend, AgentverseStore):
            active_store = storage_backend
        else:
            active_store = AgentverseStore(ctx.storage)
        active_logic = BuilderLogic(active_store, storefront_address)

        response_text = active_logic.process_message(owner_id=sender, message_text=incoming_text)
        reply = create_text_chat_message(response_text)
        await ctx.send(sender, reply)

    @chat_proto.on_message(ChatAcknowledgement)
    async def handle_chat_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
        pass

    agent.include(chat_proto, publish_manifest=True)
    return agent


if __name__ == "__main__":
    builder = create_builder_agent()
    builder.run()
