"""
Unit tests for core/store.py
Validates in-memory storage, JSON persistence, entity helpers, and AgentverseStore adapter.
"""

import json
import os
import tempfile
import pytest

from core.catalog import Business, Item
from core.orders import CartItem, Order, OrderState
from core.store import MemoryStore, AgentverseStore


class TestMemoryStore:
    def test_basic_crud(self):
        store = MemoryStore()
        assert store.get("key1") is None
        assert store.has("key1") is False

        store.set("key1", "val1")
        assert store.get("key1") == "val1"
        assert store.has("key1") is True

        assert store.delete("key1") is True
        assert store.get("key1") is None
        assert store.delete("key1") is False

    def test_business_persistence(self):
        store = MemoryStore()
        biz = Business(
            id="craft_bakery",
            name="Craft Bakery",
            version=2,
            items=[Item(name="Baguette", price=3.50, currency="USD")],
        )
        store.save_business(biz)

        loaded = store.get_business("craft_bakery")
        assert loaded is not None
        assert loaded.id == "craft_bakery"
        assert loaded.version == 2
        assert len(loaded.items) == 1
        assert loaded.items[0].name == "Baguette"

    def test_order_and_idempotency_persistence(self):
        store = MemoryStore()
        items = [CartItem(item_name="Baguette", unit_price=3.50, currency="USD", quantity=2)]
        order = Order(
            order_id="ord_12345",
            business_id="craft_bakery",
            catalog_version=1,
            customer_id="cust_sam",
            items=items,
            state=OrderState.CONFIRMED,
        )
        store.save_order(order)

        # Retrieve by order ID
        loaded_order = store.get_order("ord_12345")
        assert loaded_order is not None
        assert loaded_order.order_id == "ord_12345"
        assert loaded_order.total_amount == 7.00

        # Retrieve by idempotency key
        by_idemp = store.get_order_by_idempotency(order.idempotency_key)
        assert by_idemp is not None
        assert by_idemp.order_id == "ord_12345"

    def test_session_state_persistence(self):
        store = MemoryStore()
        session_id = "sess_user_001"
        state = {"step": "review", "pending_catalog": {"name": "Test"}}

        store.save_session_state(session_id, state)
        loaded = store.get_session_state(session_id)
        assert loaded == state

        store.clear_session_state(session_id)
        assert store.get_session_state(session_id) is None

    def test_json_file_backup_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = os.path.join(tmpdir, "store.json")
            store1 = MemoryStore(file_path=file_path)
            store1.set("persisted_key", "persisted_value")

            # Check file exists and has content
            assert os.path.exists(file_path)
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            assert data["persisted_key"] == "persisted_value"

            # Create second store pointing to same file
            store2 = MemoryStore(file_path=file_path)
            assert store2.get("persisted_key") == "persisted_value"


class MockAgentverseCtxStorage:
    """Mock simulating ctx.storage in an Agentverse agent."""
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = value

    def delete(self, key):
        return self.data.pop(key, None) is not None

    def has(self, key):
        return key in self.data


class TestAgentverseStoreAdapter:
    def test_agentverse_store_delegation(self):
        mock_ctx = MockAgentverseCtxStorage()
        store = AgentverseStore(mock_ctx)

        store.set("test_key", {"nested": "value"})
        assert store.get("test_key") == {"nested": "value"}
        assert store.has("test_key") is True

        # Check entity saving works seamlessly through adapter
        biz = Business(id="av_biz", name="AV Bakery")
        store.save_business(biz)

        retrieved = store.get_business("av_biz")
        assert retrieved is not None
        assert retrieved.name == "AV Bakery"
