import unittest

from webapp.services.custom_rule_similarity import (
    find_similar_custom_rule,
    merchant_referenced_in_rule,
    normalize_rule_text,
    suppress_duplicate_rule_suggestion,
)


class CustomRuleSimilarityTests(unittest.TestCase):
    def test_normalize_rule_text(self):
        a = normalize_rule_text('When Generated Description is "Netflix" set ai_category Entertainment')
        b = normalize_rule_text("When Generated Description is Netflix set ai_category Entertainment")
        self.assertEqual(a, b)

    def test_merchant_referenced(self):
        self.assertTrue(merchant_referenced_in_rule("When Generated Description is *Mercury Ins* set ...", "Mercury Ins Mcc Ppa"))
        self.assertFalse(merchant_referenced_in_rule("When Generated Description is Netflix set ...", "Mercury Ins"))

    def test_find_by_text_similarity(self):
        existing = [
            {
                "rule": "When Generated Description is Netflix set ai_category Entertainment, type Fixed",
                "status": "Active",
            }
        ]
        hit = find_similar_custom_rule(
            suggested_rule="When Generated Description is Netflix set ai_category Entertainment, type Fixed",
            existing_rules=existing,
        )
        self.assertIsNotNone(hit)
        self.assertEqual(hit["status"], "Active")

    def test_find_by_merchant_and_category(self):
        existing = [
            {
                "rule": "When Generated Description is *Mercury Ins* set ai_category Insurance, type Fixed",
                "status": "Pending",
            }
        ]
        hit = find_similar_custom_rule(
            suggested_rule="When Generated Description is Mercury Ins Mcc Ppa set ai_category Insurance, type Fixed",
            merchant_key="Mercury Ins Mcc Ppa",
            after_labels={"ai_category": "Insurance", "expense_type": "Fixed"},
            existing_rules=existing,
        )
        self.assertIsNotNone(hit)

    def test_amount_specific_rule_not_duplicate(self):
        existing = [
            {
                "rule": "When Generated Description is Starbucks and amount is 5.75 set ai_category Food",
                "status": "Active",
            }
        ]
        hit = find_similar_custom_rule(
            suggested_rule="When Generated Description is Starbucks and amount is 12.50 set ai_category Food",
            merchant_key="Starbucks",
            after_labels={"ai_category": "Food"},
            amount=-12.50,
            existing_rules=existing,
        )
        self.assertIsNone(hit)

    def test_suppress_duplicate_clears_recommendation(self):
        result = suppress_duplicate_rule_suggestion(
            {
                "insight": "Pattern detected.",
                "suggested_rule": "When Generated Description is Netflix set ai_category Entertainment",
                "recommend_save_rule": True,
                "data_notes": [],
                "future_note": "",
            },
            merchant_key="Netflix",
            after_labels={"ai_category": "Entertainment"},
            existing_rules=[
                {"rule": "When Generated Description is Netflix set ai_category Entertainment", "status": "Active"}
            ],
        )
        self.assertFalse(result["recommend_save_rule"])
        self.assertTrue(result["duplicate_rule_skipped"])
        self.assertIn("Similar custom rule already exists", result["data_notes"][-1])


if __name__ == "__main__":
    unittest.main()
