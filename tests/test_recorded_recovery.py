import importlib.util
from pathlib import Path
import unittest


class RecordedRecoveryTests(unittest.TestCase):
    def test_observed_empty_response_and_invalid_batch_recover_without_extra_model_decision(
        self,
    ):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location(
            "recorded_recovery",
            root / "scripts/evaluation/medical/verify_recorded_recovery.py",
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        report = module.verify(root)
        self.assertEqual(report["live_api_calls"], 0)
        self.assertEqual(report["recorded_decisions"], 33)
        self.assertTrue(report["passed"], report["result"]["checks"])


if __name__ == "__main__":
    unittest.main()
