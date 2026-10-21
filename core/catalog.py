"""
Catalog data models and validation for AgentForge.
Pure Python, zero third-party dependencies, safe for Agentverse hosted runtime.
"""

from __future__ import annotations
import copy
from dataclasses import dataclass, field
import json
from typing import Any, Dict, List, Optional, Union


@dataclass
class Item:
    """Represents a sellable catalog item.
    
    Attributes:
        name: Unique or friendly name of item.
        price: Unit price. Must be numeric and non-negative.
        currency: Currency code (e.g. 'USD', 'FET').
        options: Optional variations or customizations (e.g. ['Gluten-free', 'Extra Shot']).
        stock: Remaining stock count (integer >= 0), or None if stock is unlimited.
        available: Flag indicating if item is actively available for ordering.
        confidence: Extraction confidence score between 0.0 and 1.0 (defaults to 1.0).
        description: Optional item description.
    """
    name: str
    price: float
    currency: str
    options: List[str] = field(default_factory=list)
    stock: Optional[int] = None
    available: bool = True
    confidence: float = 1.0
    description: str = ""

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        if not self.name or not str(self.name).strip():
            raise ValueError("Item name must be a non-empty string.")
        
        # Validate price: numeric and non-negative
        if not isinstance(self.price, (int, float)):
            try:
                self.price = float(self.price)
            except (TypeError, ValueError):
                raise ValueError(f"Item '{self.name}' price must be numeric, got {type(self.price).__name__}.")
        
        if self.price < 0:
            raise ValueError(f"Item '{self.name}' price cannot be negative, got {self.price}.")

        # Normalize price float to 2 decimal places to avoid float representation artifacts
        self.price = round(float(self.price), 2)

        # Validate currency
        if not self.currency or not str(self.currency).strip():
            raise ValueError(f"Item '{self.name}' must have a valid non-empty currency code.")
        self.currency = str(self.currency).strip().upper()

        # Validate stock
        if self.stock is not None:
            if not isinstance(self.stock, int):
                try:
                    self.stock = int(self.stock)
                except (TypeError, ValueError):
                    raise ValueError(f"Item '{self.name}' stock must be an integer, got {self.stock}.")
            if self.stock < 0:
                raise ValueError(f"Item '{self.name}' stock cannot be negative, got {self.stock}.")

        # Validate confidence
        if not isinstance(self.confidence, (int, float)) or not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"Item '{self.name}' confidence must be between 0.0 and 1.0, got {self.confidence}.")
        self.confidence = float(self.confidence)

        if not isinstance(self.options, list):
            if isinstance(self.options, (tuple, set)):
                self.options = list(self.options)
            else:
                self.options = [str(self.options)]

    def is_in_stock(self, quantity: int = 1) -> bool:
        """Check if requested quantity is available."""
        if not self.available:
            return False
        if self.stock is None:
            return True
        return self.stock >= quantity

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "price": self.price,
            "currency": self.currency,
            "options": list(self.options),
            "stock": self.stock,
            "available": self.available,
            "confidence": self.confidence,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Item":
        return cls(
            name=str(data.get("name", "")),
            price=float(data.get("price", 0.0)),
            currency=str(data.get("currency", "USD")),
            options=list(data.get("options", [])),
            stock=data.get("stock") if data.get("stock") is not None else None,
            available=bool(data.get("available", True)),
            confidence=float(data.get("confidence", 1.0)),
            description=str(data.get("description", "")),
        )


@dataclass
class Business:
    """Represents a business catalog profile. Every mutation produces a new version.
    
    Attributes:
        id: Unique business identifier (e.g. 'sweet_tooth_bakery').
        name: Display name of the business.
        hours: Operating hours (freeform string or structured summary).
        policies: Policies regarding delivery, refunds, allergens, etc.
        version: Monotonically increasing version number (integer >= 1).
        items: List of catalog items.
    """
    id: str
    name: str
    hours: str = "9:00 AM - 6:00 PM"
    policies: str = "Standard shop policies apply."
    version: int = 1
    items: List[Item] = field(default_factory=list)

    def __post_init__(self):
        self.validate()

    def validate(self) -> None:
        if not self.id or not str(self.id).strip():
            raise ValueError("Business id must be a non-empty string.")
        self.id = str(self.id).strip().lower().replace(" ", "_")

        if not self.name or not str(self.name).strip():
            raise ValueError("Business name must be a non-empty string.")

        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError(f"Business version must be a positive integer, got {self.version}.")

        # Validate that all items are valid Item instances
        validated_items: List[Item] = []
        for raw_item in self.items:
            if isinstance(raw_item, Item):
                raw_item.validate()
                validated_items.append(raw_item)
            elif isinstance(raw_item, dict):
                item = Item.from_dict(raw_item)
                validated_items.append(item)
            else:
                raise ValueError(f"Invalid item entry in business catalog: {raw_item}")
        self.items = validated_items

    def get_item(self, name: str) -> Optional[Item]:
        """Case-insensitive search for an item by name."""
        target = name.strip().lower()
        for item in self.items:
            if item.name.strip().lower() == target:
                return item
        return None

    def add_item(self, item: Item) -> "Business":
        """Add an item and return a NEW versioned Business instance."""
        item.validate()
        new_items = copy.deepcopy(self.items)
        # If an item with the same name exists, replace it
        replaced = False
        for idx, existing in enumerate(new_items):
            if existing.name.strip().lower() == item.name.strip().lower():
                new_items[idx] = copy.deepcopy(item)
                replaced = True
                break
        if not replaced:
            new_items.append(copy.deepcopy(item))

        return Business(
            id=self.id,
            name=self.name,
            hours=self.hours,
            policies=self.policies,
            version=self.version + 1,
            items=new_items,
        )

    def remove_item(self, item_name: str) -> "Business":
        """Remove an item and return a NEW versioned Business instance."""
        target = item_name.strip().lower()
        new_items = [i for i in self.items if i.name.strip().lower() != target]
        if len(new_items) == len(self.items):
            raise ValueError(f"Item '{item_name}' not found in catalog.")
        return Business(
            id=self.id,
            name=self.name,
            hours=self.hours,
            policies=self.policies,
            version=self.version + 1,
            items=new_items,
        )

    def update_item_stock(self, item_name: str, new_stock: Optional[int]) -> "Business":
        """Update stock for an item and return a NEW versioned Business instance."""
        item = self.get_item(item_name)
        if not item:
            raise ValueError(f"Item '{item_name}' not found in catalog.")
        updated_item = copy.deepcopy(item)
        updated_item.stock = new_stock
        return self.add_item(updated_item)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "hours": self.hours,
            "policies": self.policies,
            "version": self.version,
            "items": [item.to_dict() for item in self.items],
        }

    def to_json(self, indent: Optional[int] = None) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Business":
        raw_items = data.get("items", [])
        items = [Item.from_dict(i) if isinstance(i, dict) else i for i in raw_items]
        raw_name = str(data.get("name") or "My Shop").strip()
        raw_id = str(data.get("id") or raw_name.lower().replace(" ", "_")).strip()
        if not raw_id:
            raw_id = "my_shop"
        return cls(
            id=raw_id,
            name=raw_name,
            hours=str(data.get("hours", "9:00 AM - 6:00 PM")),
            policies=str(data.get("policies", "Standard shop policies apply.")),
            version=int(data.get("version", 1)),
            items=items,
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Business":
        data = json.loads(json_str)
        return cls.from_dict(data)
