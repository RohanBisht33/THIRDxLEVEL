"""
Unit tests for agents/builder_agent.py
Tests owner conversational flow: intake -> extraction -> review gate -> inline edits -> deployment -> QA report -> publish.
"""

import pytest
from core.store import MemoryStore
from agents.builder_agent import BuilderLogic, STATE_IDLE, STATE_WAITING_FOR_MENU, STATE_REVIEW, STATE_PUBLISHED


SAMPLE_MENU = """Golden Crust Bakery
Hours: 7am - 3pm
- Sourdough Loaf: $6.50 (In stock: 15)
- Croissant: $3.50 (In stock: 25)
- Almond Danish: $4.75 (Stock: 0 - SOLD OUT)
"""


class TestBuilderOwnerFlow:
    @pytest.fixture
    def store(self) -> MemoryStore:
        return MemoryStore()

    def test_full_owner_conversation_lifecycle(self, store):
        logic = BuilderLogic(store, storefront_address="agent1q_mock_storefront")
        owner_id = "owner_alice"

        # 1. Intake: Owner starts conversation
        msg1 = logic.process_message(owner_id, "make an agent for my bakery Golden Crust")
        assert "Welcome to AgentForge" in msg1
        assert "Golden Crust" in msg1
        session = logic.get_session(owner_id)
        assert session["stage"] == STATE_WAITING_FOR_MENU

        # 2. Intake: Owner supplies menu text
        msg2 = logic.process_message(owner_id, SAMPLE_MENU)
        assert "Extracted Catalog Review" in msg2
        assert "Sourdough Loaf" in msg2
        assert "$6.50" in msg2
        assert "Croissant" in msg2
        assert "$3.50" in msg2
        assert "MANDATORY GATE" in msg2

        session = logic.get_session(owner_id)
        assert session["stage"] == STATE_REVIEW
        assert session["pending_catalog"] is not None

        # 3. Review Gate: Nothing is published before confirmation
        # Verify storefront has not stored the business yet
        assert store.get_business("golden_crust") is None

        # 4. Inline Edit: Owner adjusts Croissant price to $4.00
        msg_edit = logic.process_message(owner_id, "edit: Croissant, price, 4.00")
        assert "Successfully updated 'Croissant'" in msg_edit
        assert "Version is now 2" in msg_edit

        # 5. Mandatory Gate: Owner confirms deployment
        msg_deploy = logic.process_message(owner_id, "CONFIRM")
        assert "DEPLOYMENT COMPLETED" in msg_deploy
        assert "AGENTFORGE QA REPORT" in msg_deploy
        assert "ALL TESTS PASSED [PASS]" in msg_deploy
        assert "agent1q_mock_storefront" in msg_deploy

        # 6. Verify Storefront has the confirmed catalog
        confirmed_biz = store.get_business("golden_crust")
        assert confirmed_biz is not None
        assert "Golden Crust" in confirmed_biz.name
        croissant = confirmed_biz.get_item("Croissant")
        assert croissant.price == 4.00  # Edited price retained
        assert confirmed_biz.version == 2

        # 7. Check session state is published
        session = logic.get_session(owner_id)
        assert session["stage"] == STATE_PUBLISHED
        assert session["confirmed_business_id"] == "golden_crust"
        assert session["pending_catalog"] is None

    def test_direct_menu_paste_intake(self, store):
        logic = BuilderLogic(store)
        owner_id = "owner_bob"

        # Owner pastes menu immediately in first message
        resp = logic.process_message(owner_id, SAMPLE_MENU)
        assert "Extracted Catalog Review" in resp
        assert "MANDATORY GATE" in resp
        session = logic.get_session(owner_id)
        assert session["stage"] == STATE_REVIEW

    def test_cancel_and_reset(self, store):
        logic = BuilderLogic(store)
        owner_id = "owner_carol"

        logic.process_message(owner_id, "make an agent for my salon")
        reset_resp = logic.process_message(owner_id, "cancel")
        assert "Session reset" in reset_resp
        session = logic.get_session(owner_id)
        assert session["stage"] == STATE_IDLE
        assert session["pending_catalog"] is None

    def test_builder_agent_creation(self):
        from agents.builder_agent import create_builder_agent
        agent = create_builder_agent(port=8097)
        assert agent.name == "builder_agent"
        assert agent.address.startswith("agent1")
