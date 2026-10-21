"""
Unit tests for core/cards.py
Validates ASI:One card schemas, limits (<=64KB), required metadata keys, and USE_CARDS fallback.
"""

import json
import pytest
from core.catalog import Business, Item
from core.orders import CartItem, Order, OrderState
from core.cards import (
    create_acp_message_with_optional_card,
    build_items_carousel_card,
    build_catalog_review_card,
    build_order_review_card,
)
from uagents_core.contrib.protocols.chat import TextContent, MetadataContent


@pytest.fixture
def sample_business() -> Business:
    return Business(
        id="golden_crust",
        name="Golden Crust Artisan Bakery",
        hours="7:00 AM - 3:00 PM",
        policies="Cancellations non-refundable.",
        version=1,
        items=[
            Item(name="Sourdough Loaf", price=6.50, currency="USD", stock=15),
            Item(name="Butter Croissant", price=3.50, currency="USD", stock=4),
            Item(name="Almond Danish", price=4.75, currency="USD", stock=0),
        ],
    )


class TestCardsFeatureFlagAndSchemas:
    def test_text_fallback_when_cards_disabled(self):
        # Explicitly use_cards=False
        msg = create_acp_message_with_optional_card(
            narration="Please review menu",
            card_kind="carousel",
            card_payload={"title": "Test"},
            use_cards=False,
        )
        # MUST contain only TextContent block
        assert len(msg.content) == 1
        assert isinstance(msg.content[0], TextContent)
        assert msg.text() == "Please review menu"
        assert not any(isinstance(c, MetadataContent) for c in msg.content)

    def test_card_declaration_when_cards_enabled(self):
        # Explicitly use_cards=True
        payload = {"title": "Test Carousel", "items": []}
        msg = create_acp_message_with_optional_card(
            narration="Here are your options",
            card_kind="carousel",
            card_payload=payload,
            use_cards=True,
        )
        assert len(msg.content) == 2
        assert isinstance(msg.content[0], TextContent)
        assert isinstance(msg.content[1], MetadataContent)

        meta = msg.content[1].metadata
        assert meta["card_protocol_version"] == "1"
        assert meta["requires_card_interaction"] == "true"
        assert meta["card_kind"] == "carousel"
        assert "card_payload" in meta

        # Verify payload is valid JSON and <= 64KB per spec
        parsed_payload = json.loads(meta["card_payload"])
        assert parsed_payload["title"] == "Test Carousel"
        assert len(meta["card_payload"].encode("utf-8")) <= 64 * 1024

    def test_items_carousel_card_payload(self, sample_business):
        card = build_items_carousel_card(sample_business)
        assert card["title"] == "Golden Crust Artisan Bakery — Menu"
        assert len(card["items"]) == 3

        # Check badges
        loaf_item = card["items"][0]
        assert loaf_item["title"] == "Sourdough Loaf"
        assert loaf_item["badges"][0]["variant"] == "success"

        # Low stock item (< 5) gets warning
        croissant_item = card["items"][1]
        assert croissant_item["badges"][0]["variant"] == "warning"

        # Sold out gets error badge
        danish_item = card["items"][2]
        assert danish_item["badges"][0]["variant"] == "error"

    def test_catalog_review_card_low_confidence_flag(self):
        catalog_data = {
            "id": "b1",
            "name": "Quick Cafe",
            "hours": "8am - 4pm",
            "policies": "Standard",
            "items": [
                {"name": "Espresso", "price": 3.0, "currency": "USD", "stock": 50, "confidence": 0.98},
                {"name": "Mysterious Drink", "price": 5.0, "currency": "USD", "stock": 10, "confidence": 0.65},
            ],
        }
        card = build_catalog_review_card(catalog_data)
        assert "WARNING: Some items have low extraction confidence" in card["subtitle"]
        assert len(card["sections"][1]["rows"]) == 2
        assert "[Review Needed]" in card["sections"][1]["rows"][1]["value"]

    def test_order_review_card_payload(self):
        order = Order(
            order_id="ord_test_card",
            business_id="b1",
            catalog_version=1,
            customer_id="cust_1",
            items=[CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=2)],
            state=OrderState.CONFIRMED,
        )
        card = build_order_review_card(order, "Golden Crust")
        assert card["title"] == "Order Summary: Golden Crust"
        assert len(card["sections"]) == 2
        assert card["ctas"][0]["selection"]["action"] == "pay_order"
        assert card["ctas"][0]["selection"]["order_id"] == "ord_test_card"
