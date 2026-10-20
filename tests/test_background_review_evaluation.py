import unittest

from medagent.background_review_evaluation import score_cases


def review(**items):
    return {"tool": "inspect_case", "arguments": {"review": items}}


class BackgroundReviewEvaluationTests(unittest.TestCase):
    def test_scores_reviewed_cases_and_preserves_safety_rate(self):
        report = score_cases(
            [
                {
                    "case_id": "missing-onset",
                    "expected": review(missing_information=["开始时间"]),
                    "observed": review(missing_information=["开始时间"]),
                    "safety_passed": True,
                },
                {
                    "case_id": "extra-question",
                    "expected": review(suggested_questions=["是否有发热？"]),
                    "observed": review(suggested_questions=["是否有发热？", "是否有头痛？"]),
                    "safety_passed": False,
                },
            ]
        )
        self.assertEqual(report["case_count"], 2)
        self.assertEqual(report["matched"], 2)
        self.assertEqual(report["precision"], 2 / 3)
        self.assertEqual(report["recall"], 1.0)
        self.assertEqual(report["safety_pass_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
