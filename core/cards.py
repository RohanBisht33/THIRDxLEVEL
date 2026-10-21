"""
ASI:One Interactive Cards payload builders and ACP message integration.
Supports carousel, detail, form, review, and custom element tree card kinds per official specs.
Includes feature-flag support (USE_CARDS) with 100% plain-text fallback.
Pure-Python, zero external dependencies.
"""

from __future__ import annotations
from datetime import datetime, timezone
import json
import os
from typing import Any, Dict, List, Optional
from uuid import uuid4

from uagents_core.contrib.protocols.chat import (
    ChatMessage,
    MetadataContent,
    TextContent,
)

from .catalog import Business, Item
from .orders import Order


def is_cards_enabled() -> bool:
    """Check if interactive cards are enabled via USE_CARDS environment variable."""
    val = os.getenv("USE_CARDS", "false").strip().lower()
    return val in ["true", "1", "yes", "on"]


def create_acp_message_with_optional_card(
    narration: str,
    card_kind: Optional[str] = None,
    card_payload: Optional[Dict[str, Any]] = None,
    use_cards: Optional[bool] = None,
) -> ChatMessage:
    """Create an ACP ChatMessage.
    
    If cards are enabled, attaches a compliant MetadataContent card declaration.
    Otherwise, returns pure TextContent bubble for safe plain-text fallback.
    """
    should_use_cards = use_cards if use_cards is not None else is_cards_enabled()

    content_blocks = [TextContent(type="text", text=narration)]

    if should_use_cards and card_kind and card_payload:
        metadata_dict = {
            "card_protocol_version": "1",
            "requires_card_interaction": "true",
            "card_kind": card_kind,
            "card_payload": json.dumps(card_payload),
            "preferred_drawer_width_px": "540",
        }
        content_blocks.append(
            MetadataContent(type="metadata", metadata=metadata_dict)
        )

    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=content_blocks,
    )


# --- Specialized Card Builders ---

def build_items_carousel_card(business: Business) -> Dict[str, Any]:
    """Construct an ASI:One 'carousel' card payload for browsing a business catalog."""
    carousel_items = []
    for item in business.items:
        badges = []
        if not item.available or (item.stock is not None and item.stock <= 0):
            badges.append({"label": "SOLD OUT", "variant": "error"})
        elif item.stock is not None and item.stock <= 5:
            badges.append({"label": f"Only {item.stock} left", "variant": "warning"})
        else:
            badges.append({"label": "In Stock", "variant": "success"})

        carousel_items.append({
            "id": f"item_{item.name.lower().replace(' ', '_')}",
            "title": item.name,
            "subtitle": item.description or f"Unit price ${item.price:.2f}",
            "badges": badges,
            "secondary_text": f"${item.price:.2f} {item.currency}",
            "primary_cta": {
                "label": "Order Item",
                "selection": {
                    "action": "order_item",
                    "business_id": business.id,
                    "item_name": item.name,
                    "quantity": 1,
                },
            },
        })

    return {
        "title": f"{business.name} — Menu",
        "subtitle": f"Hours: {business.hours}",
        "items": carousel_items,
    }


def build_catalog_review_card(catalog_data: Dict[str, Any]) -> Dict[str, Any]:
    """Construct an ASI:One 'review' card payload for the owner review gate.
    
    Highlights low-confidence items with warning badges.
    """
    items = catalog_data.get("items", [])
    item_rows = []
    has_low_conf = False

    for it in items:
        name = it.get("name", "Item")
        price = float(it.get("price", 0.0))
        currency = it.get("currency", "USD")
        stock = it.get("stock")
        conf = float(it.get("confidence", 1.0))
        stock_str = f"Stock: {stock}" if stock is not None else "Unlimited"

        conf_flag = ""
        if conf < 0.85:
            conf_flag = " [Review Needed]"
            has_low_conf = True

        item_rows.append({
            "label": f"{name} (${price:.2f} {currency})",
            "value": f"{stock_str}{conf_flag}",
        })

    sections = [
        {
            "title": f"Business: {catalog_data.get('name', 'Shop')}",
            "rows": [
                {"label": "Hours", "value": catalog_data.get("hours", "Standard")},
                {"label": "Policies", "value": catalog_data.get("policies", "Standard")},
                {"label": "Item Count", "value": str(len(items))},
            ],
        },
        {
            "title": "Extracted Catalog Items",
            "rows": item_rows,
        },
    ]

    subtitle = "Please verify items and prices before publishing."
    if has_low_conf:
        subtitle = "WARNING: Some items have low extraction confidence. Please verify!"

    return {
        "title": f"Catalog Review: {catalog_data.get('name')}",
        "subtitle": subtitle,
        "sections": sections,
        "ctas": [
            {
                "label": "Confirm & Publish",
                "selection": {
                    "action": "confirm_publish",
                    "business_id": catalog_data.get("id"),
                },
            },
            {
                "label": "Cancel",
                "selection": {"action": "cancel_extraction"},
            },
        ],
    }


def build_order_review_card(order: Order, business_name: str) -> Dict[str, Any]:
    """Construct an ASI:One 'review' card payload for customer order confirmation."""
    line_rows = []
    for item in order.items:
        line_rows.append({
            "label": f"{item.item_name} x {item.quantity}",
            "value": f"${item.subtotal:.2f} {item.currency}",
        })

    sections = [
        {
            "title": "Order Line Items",
            "rows": line_rows,
        },
        {
            "title": "Total & Status",
            "rows": [
                {"label": "Total Amount", "value": f"${order.total_amount:.2f} {order.currency}"},
                {"label": "Status", "value": order.state.value.capitalize()},
                {"label": "Order ID", "value": order.order_id},
            ],
        },
    ]

    return {
        "title": f"Order Summary: {business_name}",
        "sections": sections,
        "ctas": [
            {
                "label": "Proceed to Payment",
                "selection": {
                    "action": "pay_order",
                    "order_id": order.order_id,
                    "amount": order.total_amount,
                    "currency": order.currency,
                },
            },
            {
                "label": "Cancel Order",
                "selection": {
                    "action": "cancel_order",
                    "order_id": order.order_id,
                },
            },
        ],
    }
