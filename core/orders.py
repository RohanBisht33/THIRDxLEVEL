"""
Order state machine, cart, stock checking, and plain-code money math.
Pure-Python, zero external dependencies. The LLM NEVER computes money.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Set
import uuid

from .catalog import Business, Item


class OrderState(str, Enum):
    """Lifecycle states of an order."""
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    PAYMENT_REQUESTED = "payment_requested"
    PAID = "paid"
    FAILED = "failed"
    CANCELLED = "cancelled"


VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.DRAFT: {OrderState.CONFIRMED, OrderState.CANCELLED, OrderState.FAILED},
    OrderState.CONFIRMED: {OrderState.PAYMENT_REQUESTED, OrderState.CANCELLED, OrderState.FAILED},
    OrderState.PAYMENT_REQUESTED: {OrderState.PAID, OrderState.FAILED, OrderState.CANCELLED},
    OrderState.PAID: set(),  # Terminal state
    OrderState.FAILED: set(),  # Terminal state
    OrderState.CANCELLED: set(),  # Terminal state
}


@dataclass
class CartItem:
    """An item line in a cart or order.
    
    Attributes:
        item_name: Exact name of item.
        unit_price: Unit price at time of order creation.
        currency: Currency code.
        quantity: Positive integer quantity.
        selected_options: List of selected options.
    """
    item_name: str
    unit_price: float
    currency: str
    quantity: int = 1
    selected_options: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.item_name or not str(self.item_name).strip():
            raise ValueError("CartItem item_name must be a non-empty string.")
        if self.unit_price < 0:
            raise ValueError(f"CartItem '{self.item_name}' unit_price cannot be negative.")
        self.unit_price = round(float(self.unit_price), 2)
        if not self.currency or not str(self.currency).strip():
            raise ValueError(f"CartItem '{self.item_name}' must have a valid currency.")
        self.currency = str(self.currency).strip().upper()
        if not isinstance(self.quantity, int) or self.quantity <= 0:
            raise ValueError(f"CartItem quantity must be a positive integer, got {self.quantity}.")

    @property
    def subtotal(self) -> float:
        """Calculate line subtotal using exact integer cents math."""
        # Convert to cents to prevent floating point inaccuracies
        cents = int(round(self.unit_price * 100)) * self.quantity
        return round(cents / 100.0, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "item_name": self.item_name,
            "unit_price": self.unit_price,
            "currency": self.currency,
            "quantity": self.quantity,
            "selected_options": list(self.selected_options),
            "subtotal": self.subtotal,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> CartItem:
        return cls(
            item_name=str(data["item_name"]),
            unit_price=float(data["unit_price"]),
            currency=str(data.get("currency", "USD")),
            quantity=int(data.get("quantity", 1)),
            selected_options=list(data.get("selected_options", [])),
        )


@dataclass
class Order:
    """Represents an order tracked by the storefront.
    
    Attributes:
        order_id: Unique deterministic or generated order ID.
        business_id: ID of the business this order was placed with.
        catalog_version: Catalog version number at the time the order was priced.
        customer_id: Address or identifier of customer.
        items: List of line items in order.
        state: Current lifecycle state.
        idempotency_key: Key preventing double-processing.
        created_at: Unix timestamp of creation.
        updated_at: Unix timestamp of last update.
        payment_transaction_id: External or on-chain transaction ID if paid.
        notes: Freeform order notes or failure reason.
    """
    order_id: str
    business_id: str
    catalog_version: int
    customer_id: str
    items: List[CartItem]
    state: OrderState = OrderState.DRAFT
    idempotency_key: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    payment_transaction_id: Optional[str] = None
    notes: str = ""

    def __post_init__(self):
        if not self.order_id:
            self.order_id = f"ord_{uuid.uuid4().hex[:12]}"
        if not self.idempotency_key:
            self.idempotency_key = self.generate_idempotency_key(
                self.business_id, self.customer_id, self.items
            )
        if not isinstance(self.state, OrderState):
            self.state = OrderState(self.state)

    @staticmethod
    def generate_idempotency_key(business_id: str, customer_id: str, items: List[CartItem]) -> str:
        """Create deterministic SHA256 idempotency hash for given business, customer, and items."""
        sorted_items = sorted(
            [f"{i.item_name}:{i.quantity}:{i.unit_price}" for i in items]
        )
        raw = f"{business_id}|{customer_id}|{'|'.join(sorted_items)}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @property
    def currency(self) -> str:
        """Return the currency of the order. Enforces all items use same currency."""
        if not self.items:
            return "USD"
        curr = self.items[0].currency
        for item in self.items[1:]:
            if item.currency != curr:
                raise ValueError(f"Mixed currencies in order: {curr} vs {item.currency}")
        return curr

    @property
    def total_amount(self) -> float:
        """Calculate total amount in exact plain code money math. No LLM math."""
        total_cents = sum(int(round(item.unit_price * 100)) * item.quantity for item in self.items)
        return round(total_cents / 100.0, 2)

    def transition_to(self, new_state: OrderState, reason: str = "") -> None:
        """Transition order to a new state according to the valid state machine.
        
        Raises:
            ValueError: If the transition is not permitted.
        """
        if not isinstance(new_state, OrderState):
            new_state = OrderState(new_state)

        allowed = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in allowed:
            raise ValueError(
                f"Invalid order transition from '{self.state.value}' to '{new_state.value}'. "
                f"Allowed transitions: {[s.value for s in allowed]}"
            )

        self.state = new_state
        self.updated_at = time.time()
        if reason:
            self.notes = f"{self.notes}; {reason}".strip("; ")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "order_id": self.order_id,
            "business_id": self.business_id,
            "catalog_version": self.catalog_version,
            "customer_id": self.customer_id,
            "items": [i.to_dict() for i in self.items],
            "total_amount": self.total_amount,
            "currency": self.currency,
            "state": self.state.value,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "payment_transaction_id": self.payment_transaction_id,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Order:
        items = [CartItem.from_dict(i) if isinstance(i, dict) else i for i in data.get("items", [])]
        return cls(
            order_id=str(data["order_id"]),
            business_id=str(data["business_id"]),
            catalog_version=int(data.get("catalog_version", 1)),
            customer_id=str(data["customer_id"]),
            items=items,
            state=OrderState(data.get("state", OrderState.DRAFT.value)),
            idempotency_key=str(data.get("idempotency_key", "")),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
            payment_transaction_id=data.get("payment_transaction_id"),
            notes=str(data.get("notes", "")),
        )


def validate_stock_for_cart(business: Business, items: List[CartItem]) -> List[str]:
    """Check inventory stock for all items in cart.
    
    Returns:
        List of error strings for out-of-stock or missing items. Empty list means all in stock.
    """
    errors: List[str] = []
    for cart_item in items:
        catalog_item = business.get_item(cart_item.item_name)
        if not catalog_item:
            errors.append(f"Item '{cart_item.item_name}' is not found in the catalog.")
            continue
        if not catalog_item.available:
            errors.append(f"Item '{cart_item.item_name}' is currently unavailable.")
            continue
        if catalog_item.stock is not None and catalog_item.stock < cart_item.quantity:
            errors.append(
                f"Insufficient stock for '{cart_item.item_name}'. "
                f"Requested: {cart_item.quantity}, Available: {catalog_item.stock}."
            )
    return errors


def build_order_from_cart(
    business: Business,
    customer_id: str,
    requested_items: List[Dict[str, Any]],
    idempotency_key: Optional[str] = None,
) -> Order:
    """Build a validated order from raw cart requests against a specific catalog version.
    
    All pricing is extracted strictly from the confirmed catalog.
    Customer input cannot dictate prices.
    
    Raises:
        ValueError: If an item is missing, out of stock, or invalid.
    """
    cart_items: List[CartItem] = []
    for req in requested_items:
        name = str(req.get("item_name") or req.get("name", "")).strip()
        qty = int(req.get("quantity", 1))
        options = list(req.get("options", []))

        item = business.get_item(name)
        if not item:
            raise ValueError(f"Item '{name}' does not exist in catalog '{business.name}'.")

        if not item.is_in_stock(qty):
            avail = item.stock if item.stock is not None else "0"
            raise ValueError(
                f"Item '{name}' is out of stock or insufficient quantity (requested {qty}, available {avail})."
            )

        # Unit price is ALWAYS pulled from the confirmed catalog, NEVER from user input
        cart_items.append(
            CartItem(
                item_name=item.name,
                unit_price=item.price,
                currency=item.currency,
                quantity=qty,
                selected_options=options,
            )
        )

    if not cart_items:
        raise ValueError("Cannot create an order with an empty cart.")

    return Order(
        order_id=f"ord_{uuid.uuid4().hex[:12]}",
        business_id=business.id,
        catalog_version=business.version,
        customer_id=customer_id,
        items=cart_items,
        state=OrderState.DRAFT,
        idempotency_key=idempotency_key or "",
    )
