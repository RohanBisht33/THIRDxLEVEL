"""
Storage interface and implementations for AgentForge.
Supports in-memory/JSON storage (local tests) and Agentverse ctx.storage (deployment).
Pure-Python, zero external dependencies.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
import copy
import json
import os
from typing import Any, Dict, List, Optional

from .catalog import Business
from .orders import Order


class StoreBackend(ABC):
    """Abstract interface for key-value and entity persistence."""

    @abstractmethod
    def get(self, key: str) -> Any:
        """Retrieve a value by key. Returns None if missing."""
        pass

    @abstractmethod
    def set(self, key: str, value: Any) -> None:
        """Store a key-value pair."""
        pass

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove a key if present. Returns True if removed."""
        pass

    @abstractmethod
    def has(self, key: str) -> bool:
        """Check if key exists."""
        pass

    # --- Domain entity helpers ---

    def save_business(self, business: Business) -> None:
        """Persist a business catalog under 'biz:<business_id>'."""
        self.set(f"biz:{business.id}", business.to_dict())

    def get_business(self, business_id: str) -> Optional[Business]:
        """Fetch a business catalog by ID."""
        data = self.get(f"biz:{business_id.strip().lower()}")
        if not data:
            return None
        if isinstance(data, str):
            return Business.from_json(data)
        return Business.from_dict(data)

    def save_order(self, order: Order) -> None:
        """Persist an order and maintain idempotency index."""
        order_dict = order.to_dict()
        self.set(f"order:{order.order_id}", order_dict)
        if order.idempotency_key:
            self.set(f"idemp:{order.idempotency_key}", order.order_id)

    def get_order(self, order_id: str) -> Optional[Order]:
        """Fetch an order by its order_id."""
        data = self.get(f"order:{order_id}")
        if not data:
            return None
        if isinstance(data, str):
            data = json.loads(data)
        return Order.from_dict(data)

    def get_order_by_idempotency(self, idempotency_key: str) -> Optional[Order]:
        """Retrieve an order by its idempotency key if already processed."""
        order_id = self.get(f"idemp:{idempotency_key}")
        if not order_id:
            return None
        return self.get_order(str(order_id))

    def save_session_state(self, session_id: str, state: Dict[str, Any]) -> None:
        """Store owner or customer conversational session state."""
        self.set(f"session:{session_id}", copy.deepcopy(state))

    def get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve conversational session state."""
        data = self.get(f"session:{session_id}")
        if not data:
            return None
        if isinstance(data, str):
            return json.loads(data)
        return copy.deepcopy(data)

    def clear_session_state(self, session_id: str) -> bool:
        """Clear conversational session state."""
        return self.delete(f"session:{session_id}")


class MemoryStore(StoreBackend):
    """In-memory key-value store with optional JSON file backup for local testing."""

    def __init__(self, file_path: Optional[str] = None):
        self._data: Dict[str, Any] = {}
        self._file_path = file_path
        if file_path and os.path.exists(file_path):
            self.load_from_file(file_path)

    def get(self, key: str) -> Any:
        return copy.deepcopy(self._data.get(key))

    def set(self, key: str, value: Any) -> None:
        self._data[key] = copy.deepcopy(value)
        if self._file_path:
            self.save_to_file(self._file_path)

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            if self._file_path:
                self.save_to_file(self._file_path)
            return True
        return False

    def has(self, key: str) -> bool:
        return key in self._data

    def load_from_file(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as f:
            self._data = json.load(f)

    def save_to_file(self, path: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def clear(self) -> None:
        self._data.clear()


class AgentverseStore(StoreBackend):
    """Store adapter backed by Agentverse context storage (`ctx.storage`).
    
    Compatible with the Agentverse runtime interface (get/set).
    """

    def __init__(self, ctx_storage: Any):
        self._storage = ctx_storage

    def get(self, key: str) -> Any:
        val = self._storage.get(key)
        if isinstance(val, str):
            try:
                return json.loads(val)
            except Exception:
                return val
        return val

    def set(self, key: str, value: Any) -> None:
        # Agentverse ctx.storage handles native dicts or json strings
        if isinstance(value, (dict, list)):
            try:
                self._storage.set(key, value)
            except TypeError:
                self._storage.set(key, json.dumps(value))
        else:
            self._storage.set(key, value)

    def delete(self, key: str) -> bool:
        if hasattr(self._storage, "delete"):
            return bool(self._storage.delete(key))
        # Fallback for storage implementations without explicit delete: set to None
        self._storage.set(key, None)
        return True

    def has(self, key: str) -> bool:
        if hasattr(self._storage, "has"):
            return bool(self._storage.has(key))
        return self._storage.get(key) is not None
