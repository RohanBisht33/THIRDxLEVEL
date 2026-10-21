"""
Unit tests for agents/extractor_agent.py
Tests CSV parsing, heuristic extraction, schema validation, and confidence scoring.
"""

import pytest
from core.catalog import Business, Item
from agents.extractor_agent import extract_catalog, parse_csv_catalog, parse_text_catalog_heuristic


SAMPLE_CSV = """name,price,currency,stock,options,description
Sourdough Loaf,6.50,USD,15,Whole,"Crispy artisan bread"
Croissant,3.50,USD,25,Butter,"Flaky layers"
Almond Danish,4.75,USD,0,Marzipan,"SOLD OUT"
"""

SAMPLE_MENU_TEXT = """Golden Crust Artisan Bakery
Hours: Tuesday - Sunday: 7:00 AM - 3:00 PM
Policies: All sourdough contains gluten.

ARTISAN BREADS:
- Sourdough Loaf: $6.50 (Naturally leavened crisp crust. In stock: 15)
- Butter Croissant: $3.50 (Flaky 100% butter. In stock: 25)
- Almond Danish: $4.75 (Marzipan cream. Stock: 0 - SOLD OUT)
"""


class TestCatalogExtraction:
    def test_parse_csv_catalog(self):
        result = parse_csv_catalog(SAMPLE_CSV, "Golden Crust")
        assert result["name"] == "Golden Crust"
        assert len(result["items"]) == 3

        # Sourdough Loaf
        loaf = result["items"][0]
        assert loaf["name"] == "Sourdough Loaf"
        assert loaf["price"] == 6.50
        assert loaf["stock"] == 15
        assert loaf["available"] is True

        # Almond Danish (stock 0)
        danish = result["items"][2]
        assert danish["name"] == "Almond Danish"
        assert danish["price"] == 4.75
        assert danish["stock"] == 0
        assert danish["available"] is False

    def test_parse_text_catalog_heuristic(self):
        result = parse_text_catalog_heuristic(SAMPLE_MENU_TEXT)
        assert "Golden Crust" in result["name"]
        assert "7:00 AM - 3:00 PM" in result["hours"]
        assert len(result["items"]) == 3

        loaf = result["items"][0]
        assert loaf["name"] == "Sourdough Loaf"
        assert loaf["price"] == 6.50
        assert loaf["stock"] == 15
        assert loaf["available"] is True

        danish = result["items"][2]
        assert danish["name"] == "Almond Danish"
        assert danish["price"] == 4.75
        assert danish["available"] is False

    def test_extract_catalog_dispatcher_csv(self):
        success, data, err = extract_catalog(SAMPLE_CSV, business_name="CSV Shop", format_hint="csv")
        assert success is True
        assert err is None
        assert len(data["items"]) == 3
        # Ensure it passes strict Business validation
        biz = Business.from_dict(data)
        assert biz.name == "CSV Shop"

    def test_extract_catalog_dispatcher_text(self):
        success, data, err = extract_catalog(SAMPLE_MENU_TEXT)
        assert success is True
        assert err is None
        assert len(data["items"]) == 3
        biz = Business.from_dict(data)
        assert len(biz.items) == 3

    def test_extract_empty_content_returns_error(self):
        success, data, err = extract_catalog("")
        assert success is False
        assert "Empty" in err

    def test_extractor_agent_creation(self):
        from agents.extractor_agent import create_extractor_agent
        agent = create_extractor_agent(port=8098)
        assert agent.name == "extractor_agent"
        assert agent.address.startswith("agent1")
