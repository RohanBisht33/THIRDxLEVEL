"""
Unit tests for core/catalog.py
Validates Item and Business models, price constraints, versioning, and serialization.
"""

import pytest
from core.catalog import Item, Business


class TestItemModel:
    def test_valid_item_creation(self):
        item = Item(
            name="Sourdough Loaf",
            price=6.50,
            currency="USD",
            options=["Sliced", "Whole"],
            stock=15,
            available=True,
            confidence=0.98,
            description="Crispy sourdough bread.",
        )
        assert item.name == "Sourdough Loaf"
        assert item.price == 6.50
        assert item.currency == "USD"
        assert item.options == ["Sliced", "Whole"]
        assert item.stock == 15
        assert item.available is True
        assert item.confidence == 0.98

    def test_price_must_be_numeric(self):
        # String that converts to float should succeed and normalize
        item = Item(name="Cookie", price="2.50", currency="USD")
        assert item.price == 2.50

        # Non-numeric string should fail
        with pytest.raises(ValueError, match="price must be numeric"):
            Item(name="Cookie", price="free", currency="USD")

    def test_price_cannot_be_negative(self):
        with pytest.raises(ValueError, match="price cannot be negative"):
            Item(name="Discount", price=-5.0, currency="USD")

    def test_currency_validation(self):
        with pytest.raises(ValueError, match="valid non-empty currency"):
            Item(name="Bread", price=5.0, currency="")

        item = Item(name="Bread", price=5.0, currency="usd")
        assert item.currency == "USD"  # normalized to uppercase

    def test_stock_validation(self):
        with pytest.raises(ValueError, match="stock cannot be negative"):
            Item(name="Muffin", price=3.0, currency="USD", stock=-1)

        item = Item(name="Muffin", price=3.0, currency="USD", stock="10")
        assert item.stock == 10

    def test_confidence_range(self):
        with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
            Item(name="Cake", price=20.0, currency="USD", confidence=1.5)

        with pytest.raises(ValueError, match="confidence must be between 0.0 and 1.0"):
            Item(name="Cake", price=20.0, currency="USD", confidence=-0.1)

    def test_item_stock_check(self):
        item = Item(name="Croissant", price=3.5, currency="USD", stock=2)
        assert item.is_in_stock(1) is True
        assert item.is_in_stock(2) is True
        assert item.is_in_stock(3) is False

        # Unavailable item
        item_unavail = Item(name="Croissant", price=3.5, currency="USD", stock=10, available=False)
        assert item_unavail.is_in_stock(1) is False

        # Unlimited stock
        item_unlimited = Item(name="Coffee", price=2.5, currency="USD", stock=None)
        assert item_unlimited.is_in_stock(100) is True

    def test_item_serialization_roundtrip(self):
        item = Item(name="Baguette", price=3.75, currency="EUR", stock=5)
        d = item.to_dict()
        restored = Item.from_dict(d)
        assert restored.name == item.name
        assert restored.price == item.price
        assert restored.currency == item.currency
        assert restored.stock == item.stock


class TestBusinessModel:
    def test_valid_business_creation(self):
        biz = Business(
            id="golden_crust",
            name="Golden Crust Bakery",
            hours="7am - 3pm",
            policies="No outside food",
            version=1,
            items=[Item(name="Sourdough", price=6.5, currency="USD")],
        )
        assert biz.id == "golden_crust"
        assert biz.name == "Golden Crust Bakery"
        assert biz.version == 1
        assert len(biz.items) == 1

    def test_business_id_normalization(self):
        biz = Business(id="Sweet Tooth Bakery", name="Sweet Tooth")
        assert biz.id == "sweet_tooth_bakery"

    def test_version_must_be_positive_int(self):
        with pytest.raises(ValueError, match="version must be a positive integer"):
            Business(id="test", name="Test", version=0)

    def test_case_insensitive_get_item(self):
        item = Item(name="Chocolate Muffin", price=4.0, currency="USD")
        biz = Business(id="test", name="Test", items=[item])

        assert biz.get_item("chocolate muffin") is not None
        assert biz.get_item("CHOCOLATE MUFFIN").price == 4.0
        assert biz.get_item("Nonexistent") is None

    def test_version_increment_on_add_item(self):
        biz_v1 = Business(id="b1", name="Bakery", version=1)
        new_item = Item(name="Donut", price=2.0, currency="USD")

        biz_v2 = biz_v1.add_item(new_item)
        assert biz_v2.version == 2
        assert biz_v1.version == 1  # Original instance is immutable
        assert len(biz_v2.items) == 1
        assert len(biz_v1.items) == 0

        # Adding same item updates it and creates v3
        updated_donut = Item(name="Donut", price=2.5, currency="USD")
        biz_v3 = biz_v2.add_item(updated_donut)
        assert biz_v3.version == 3
        assert len(biz_v3.items) == 1
        assert biz_v3.get_item("Donut").price == 2.5

    def test_version_increment_on_remove_item(self):
        item = Item(name="Bagel", price=1.5, currency="USD")
        biz_v1 = Business(id="b1", name="Bakery", version=1, items=[item])

        biz_v2 = biz_v1.remove_item("Bagel")
        assert biz_v2.version == 2
        assert len(biz_v2.items) == 0

        with pytest.raises(ValueError, match="not found in catalog"):
            biz_v2.remove_item("Nonexistent")

    def test_version_increment_on_stock_update(self):
        item = Item(name="Pretzel", price=3.0, currency="USD", stock=10)
        biz_v1 = Business(id="b1", name="Bakery", version=1, items=[item])

        biz_v2 = biz_v1.update_item_stock("Pretzel", 5)
        assert biz_v2.version == 2
        assert biz_v2.get_item("Pretzel").stock == 5
        assert biz_v1.get_item("Pretzel").stock == 10

    def test_business_json_serialization(self):
        item = Item(name="Loaf", price=5.0, currency="USD", stock=20)
        biz = Business(id="test_biz", name="Test Bakery", items=[item])
        raw_json = biz.to_json()

        restored = Business.from_json(raw_json)
        assert restored.id == biz.id
        assert restored.version == biz.version
        assert len(restored.items) == 1
        assert restored.items[0].name == "Loaf"
        assert restored.items[0].price == 5.0
