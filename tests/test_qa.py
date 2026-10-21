"""
Unit tests for core/qa.py
Validates the 6 scripted QA test cases, evaluation logic, and report outputs (text & card).
"""

from core.catalog import Business, Item
from core.qa import (
    get_standard_qa_cases,
    evaluate_qa_response,
    run_local_qa_suite,
    QAReport,
)


class TestQASuite:
    def test_standard_cases_count_and_ids(self):
        cases = get_standard_qa_cases()
        assert len(cases) == 6
        expected_ids = [
            "qa_1_price_lookup",
            "qa_2_valid_order",
            "qa_3_out_of_stock",
            "qa_4_unknown_question",
            "qa_5_prompt_injection",
            "qa_6_duplicate_order",
        ]
        assert [c.id for c in cases] == expected_ids

    def test_qa_1_price_lookup_evaluation(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_1_price_lookup"]

        # Pass: correct price returned
        res_pass = evaluate_qa_response(case, "A Sourdough Loaf costs $6.50.", {"price": "6.50"})
        assert res_pass.passed is True

        # Fail: missing price
        res_fail = evaluate_qa_response(case, "We have delicious sourdough loaf available.", {"price": "6.50"})
        assert res_fail.passed is False

    def test_qa_2_valid_order_evaluation(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_2_valid_order"]

        # Pass: confirms order with total
        res_pass = evaluate_qa_response(
            case,
            "Your order for 2 Croissants has been drafted. Order total is $7.00.",
            {"total": "7.00"},
        )
        assert res_pass.passed is True

        # Fail: missing total amount
        res_fail = evaluate_qa_response(case, "Sure, I noted your croissants.", {"total": "7.00"})
        assert res_fail.passed is False

    def test_qa_3_out_of_stock_evaluation(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_3_out_of_stock"]

        # Pass: reports out of stock
        res_pass = evaluate_qa_response(case, "Sorry, Almond Danish is currently out of stock.")
        assert res_pass.passed is True

        # Fail: takes the order anyway
        res_fail = evaluate_qa_response(case, "Great, 1 Almond Danish added to your cart for $4.75.")
        assert res_fail.passed is False

    def test_qa_4_unknown_question_fallback(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_4_unknown_question"]

        # Pass: disclaims and redirects to shop
        res_pass = evaluate_qa_response(
            case,
            "I don't know, please contact the shop. I can only assist with our bakery menu and ordering.",
        )
        assert res_pass.passed is True

        # Fail: hallucinating or answering out of scope
        res_fail = evaluate_qa_response(case, "Yes, we do bicycle repairs on Saturdays for $30.")
        assert res_fail.passed is False

    def test_qa_5_prompt_injection_defense(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_5_prompt_injection"]

        # Pass: ignores override and returns real catalog price
        res_pass = evaluate_qa_response(
            case,
            "I cannot change catalog prices. The Sourdough Loaf is $6.50.",
            {"price": "6.50"},
        )
        assert res_pass.passed is True

        # Fail: accepted the injection claiming it is free
        res_fail = evaluate_qa_response(case, "Everything is free! The price is $0.00.", {"price": "6.50"})
        assert res_fail.passed is False

    def test_qa_6_duplicate_order_idempotency(self):
        cases = {c.id: c for c in get_standard_qa_cases()}
        case = cases["qa_6_duplicate_order"]

        # Pass: detected duplicate
        res_pass = evaluate_qa_response(
            case,
            "An existing order with this exact cart was already placed (Order ID: ord_abc123).",
        )
        assert res_pass.passed is True

    def test_report_builder_text_and_card(self):
        biz = Business(
            id="test_qa_biz",
            name="QA Bakery",
            items=[
                Item(name="Sourdough Loaf", price=6.50, currency="USD", stock=10),
                Item(name="Croissant", price=3.50, currency="USD", stock=10),
                Item(name="Almond Danish", price=4.75, currency="USD", stock=0),
            ],
        )

        # Dispatch function simulating an ideal compliant agent
        def mock_agent_dispatch(msg: str) -> str:
            m = msg.lower()
            if "sourdough" in m and "cost" in m:
                return "The Sourdough Loaf is $6.50."
            elif "2 croissants" in m:
                return "Your order for 2 Croissants is confirmed. Total: $7.00."
            elif "almond danish" in m:
                return "Sorry, Almond Danish is out of stock."
            elif "bicycle" in m:
                return "I don't know, please contact the shop."
            elif "ignore previous" in m:
                return "Catalog prices cannot be changed. Sourdough Loaf is $6.50."
            return "Order already placed."

        report = run_local_qa_suite(biz, mock_agent_dispatch)
        assert report.all_passed is True
        assert report.passed_count == 6

        # Check text report formatting
        text_report = report.to_text_report()
        assert "ALL TESTS PASSED" in text_report
        assert "Price Lookup" in text_report
        assert "Prompt Injection Defense" in text_report

        # Check interactive card payload formatting
        card_payload = report.to_card_payload()
        assert card_payload["title"] == "QA Report: QA Bakery"
        assert len(card_payload["sections"]) == 2
        assert card_payload["ctas"][0]["selection"]["action"] == "publish_storefront"
