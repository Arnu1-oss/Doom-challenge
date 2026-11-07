import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import requests

from main import (
    calculate_score, create_prompt, extract_json, parse_transcript,
    query_openrouter, validate_evaluation,
)

TRANSCRIPT = [
    {"timestamp": "00:01", "speaker": "client", "text": "Help?"},
    {"timestamp": "00:02", "speaker": "operator", "text": "Hello, I'm Alex."},
    {"timestamp": "00:03", "speaker": "operator", "text": "Here are the steps."},
]


def valid_evaluation():
    points = {1: 10, 2: None, 3: None, 4: 20, 5: 15, 6: 10, 7: 5}
    return {
        "criteria": [
            {
                "id": ident,
                "points": score,
                "explanation": "Reason",
                "evidence": ([TRANSCRIPT[1]] if score is not None and score > 0 else []),
            }
            for ident, score in points.items()
        ],
        "gross_errors": {"detected": False, "explanation": "", "evidence": []},
    }


class TestEvaluator(unittest.TestCase):
    def test_transcript_parsing(self):
        self.assertEqual(parse_transcript(json.dumps(TRANSCRIPT)), TRANSCRIPT)
        with self.assertRaises(ValueError):
            parse_transcript('[{"speaker": "operator"}]')

    def test_prompt_keeps_operator_and_context(self):
        row = pd.Series({"conversation_id": "abc", "channel": "chat", "language": "EN"})
        prompt = create_prompt(row, TRANSCRIPT)
        self.assertIn("abc", prompt)
        self.assertIn("[00:01] client: Help?", prompt)
        self.assertIn("[00:02] operator: Hello, I'm Alex.", prompt)

    def test_json_code_fence(self):
        self.assertEqual(extract_json('```json\n{"criteria":[]}\n```'), {"criteria": []})

    def test_perfect_score_with_na(self):
        data = validate_evaluation(valid_evaluation(), TRANSCRIPT)
        self.assertEqual(calculate_score(data["criteria"]), 100.0)

    def test_weighted_score_redistributes_na(self):
        data = valid_evaluation()
        data["criteria"][0]["points"] = 0  # loses 10 out of remaining 65
        result = validate_evaluation(data, TRANSCRIPT)
        self.assertEqual(calculate_score(result["criteria"]), round(100 * 55 / 65, 2))

    def test_reject_fabricated_quote(self):
        data = valid_evaluation()
        data["criteria"][0]["evidence"][0] = {
            "timestamp": "00:02", "speaker": "operator", "text": "Fabricated"
        }
        with self.assertRaisesRegex(ValueError, "Evidence"):
            validate_evaluation(data, TRANSCRIPT)

    def test_reject_wrong_points(self):
        data = valid_evaluation()
        data["criteria"][5]["points"] = 15  # criterion 6 only permits 0,4,7,10
        with self.assertRaisesRegex(ValueError, "Invalid points"):
            validate_evaluation(data, TRANSCRIPT)

    def test_reject_missing_or_repeated_criteria(self):
        data = valid_evaluation()
        data["criteria"].pop()
        with self.assertRaisesRegex(ValueError, "exactly 7"):
            validate_evaluation(data, TRANSCRIPT)

    def test_gross_error_requires_evidence(self):
        data = valid_evaluation()
        data["gross_errors"]["detected"] = True
        with self.assertRaisesRegex(ValueError, "Gross error needs"):
            validate_evaluation(data, TRANSCRIPT)

    def test_empty_api_key_not_sent(self):
        with self.assertRaisesRegex(ValueError, "OPENROUTER_API_KEY"):
            query_openrouter("hello", "")

    def test_api_mock_success(self):
        fake = Mock()
        fake.status_code = 200
        fake.json.return_value = {"choices": [{"message": {"content": '{"ok": true}'}}]}
        fake.raise_for_status.return_value = None
        session = Mock()
        session.post.return_value = fake
        result = query_openrouter("hello", "test-not-real", session=session)
        self.assertEqual(result, {"ok": True})
        self.assertEqual(session.post.call_args.kwargs["timeout"], 90)


if __name__ == "__main__":
    unittest.main()
