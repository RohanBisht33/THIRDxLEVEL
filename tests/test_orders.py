"""
Unit tests for core/orders.py
Validates CartItem, Order state machine transitions, plain-code money math, stock validation, and idempotency.
"""

import pytest
from core.catalog import Business, Item
from core.orders import (
    CartItem,
    Order,
    OrderState,
    build_order_from_cart,
    validate_stock_for_cart,
)


class TestCartItem:
    def test_cart_item_subtotal_calculation(self):
        # 3 items at $3.50 = $10.50 exactly
        ci = CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=3)
        assert ci.subtotal == 10.50

    def test_float_money_rounding_safety(self):
        # 0.10 + 0.20 float issue: plain cents avoids 0.30000000000000004
        ci = CartItem(item_name="Small Treat", unit_price=0.10, currency="USD", quantity=3)
        assert ci.subtotal == 0.30

    def test_cart_item_negative_price_rejected(self):
        with pytest.raises(ValueError, match="cannot be negative"):
            CartItem(item_name="Illegal", unit_price=-1.0, currency="USD", quantity=1)

    def test_cart_item_non_positive_quantity_rejected(self):
        with pytest.raises(ValueError, match="positive integer"):
            CartItem(item_name="ZeroQty", unit_price=2.0, currency="USD", quantity=0)


class TestOrderStateMachine:
    def test_valid_lifecycle_progression(self):
        items = [CartItem(item_name="Loaf", unit_price=6.50, currency="USD", quantity=2)]
        order = Order(
            order_id="ord_test1",
            business_id="bakery_1",
            catalog_version=1,
            customer_id="cust_alice",
            items=items,
            state=OrderState.DRAFT,
        )

        assert order.state == OrderState.DRAFT
        assert order.total_amount == 13.00
        assert order.currency == "USD"

        # draft -> confirmed
        order.transition_to(OrderState.CONFIRMED, reason="User clicked confirm")
        assert order.state == OrderState.CONFIRMED
        assert "User clicked confirm" in order.notes

        # confirmed -> payment_requested
        order.transition_to(OrderState.PAYMENT_REQUESTED, reason="Invoice sent")
        assert order.state == OrderState.PAYMENT_REQUESTED

        # payment_requested -> paid
        order.transition_to(OrderState.PAID, reason="Tx confirmed")
        assert order.state == OrderState.PAID

    def test_invalid_lifecycle_transition_raises_error(self):
        items = [CartItem(item_name="Loaf", unit_price=6.50, currency="USD", quantity=1)]
        order = Order(
            order_id="ord_test2",
            business_id="bakery_1",
            catalog_version=1,
            customer_id="cust_bob",
            items=items,
            state=OrderState.DRAFT,
        )

        # draft cannot jump straight to paid
        with pytest.raises(ValueError, match="Invalid order transition"):
            order.transition_to(OrderState.PAID)

        # terminal state cannot transition anywhere
        order.transition_to(OrderState.CANCELLED)
        assert order.state == OrderState.CANCELLED

        with pytest.raises(ValueError, match="Invalid order transition"):
            order.transition_to(OrderState.CONFIRMED)

    def test_mixed_currency_rejected(self):
        items = [
            CartItem(item_name="Item A", unit_price=5.0, currency="USD", quantity=1),
            CartItem(item_name="Item B", unit_price=5.0, currency="EUR", quantity=1),
        ]
        order = Order(
            order_id="ord_mixed",
            business_id="b1",
            catalog_version=1,
            customer_id="c1",
            items=items,
        )
        with pytest.raises(ValueError, match="Mixed currencies"):
            _ = order.currency


class TestInventoryAndPricingIntegrity:
    @pytest.fixture
    def sample_business(self) -> Business:
        return Business(
            id="test_bakery",
            name="Test Bakery",
            version=3,
            items=[
                Item(name="Sourdough Loaf", price=6.50, currency="USD", stock=5),
                Item(name="Croissant", price=3.50, currency="USD", stock=2),
                Item(name="Almond Danish", price=4.75, currency="USD", stock=0),
                Item(name="Secret Cake", price=25.00, currency="USD", available=False),
            ],
        )

    def test_stock_validation(self, sample_business):
        # In stock
        ok_items = [CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=2)]
        errors = validate_stock_for_cart(sample_business, ok_items)
        assert len(errors) == 0

        # Insufficient stock
        excess_items = [CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=5)]
        errors = validate_stock_for_cart(sample_business, excess_items)
        assert len(errors) == 1
        assert "Insufficient stock" in errors[0]

        # Sold out (stock 0)
        sold_out = [CartItem(item_name="Almond Danish", unit_price=4.75, currency="USD", quantity=1)]
        errors = validate_stock_for_cart(sample_business, sold_out)
        assert len(errors) == 1
        assert "Insufficient stock" in errors[0]

        # Unavailable
        unavail = [CartItem(item_name="Secret Cake", unit_price=25.00, currency="USD", quantity=1)]
        errors = validate_stock_for_cart(sample_business, unavail)
        assert len(errors) == 1
        assert "unavailable" in errors[0]

    def test_build_order_enforces_confirmed_catalog_price(self, sample_business):
        # Customer attempts to order Sourdough claiming price is $0.50
        malicious_request = [
            {"item_name": "Sourdough Loaf", "quantity": 2, "price": 0.50}
        ]
        order = build_order_from_cart(sample_business, "cust_hacker", malicious_request)

        # The price MUST come from the confirmed catalog ($6.50), NEVER the request
        assert order.items[0].unit_price == 6.50
        assert order.total_amount == 13.00
        assert order.catalog_version == 3

    def test_deterministic_idempotency_key(self, sample_business):
        items = [
            CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=2)
        ]
        key1 = Order.generate_idempotency_key("test_bakery", "cust_123", items)
        key2 = Order.generate_idempotency_key("test_bakery", "cust_123", items)
        assert key1 == key2

        # Different quantity produces different idempotency key
        items_diff = [
            CartItem(item_name="Croissant", unit_price=3.50, currency="USD", quantity=3)
        ]
        key3 = Order.generate_idempotency_key("test_bakery", "cust_123", items_diff)
        assert key1 != key3
