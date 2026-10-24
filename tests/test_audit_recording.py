import json
import unittest

from medagent.audit_recording import (
    RecordingBackend,
    TokenCounter,
    measure_messages,
    summarize_records,
)


class _StubBackend:
    name = "stub"

    def __init__(self, result=None, error=None):
        self.result = result or {"tool": "ask_patient", "arguments": {"fact_key": "sex"}}
        self.error = error
        self.calls = []

    def decide(self, session_id, messages):
        self.calls.append(messages)
        if self.error is not None:
            raise self.error
        return self.result

    def decide_cancellable(self, session_id, messages, cancelled):
        return self.decide(session_id, messages)


class AuditRecordingTests(unittest.TestCase):
    def test_token_counter_reports_method_next_to_numbers(self):
        counter = TokenCounter()
        counted = counter.count("hello world, this is a test")
        self.assertIn(counted["method"], ("tiktoken_cl100k_proxy", "chars_only"))
        if counted["method"] == "tiktoken_cl100k_proxy":
            self.assertIsInstance(counted["tokens"], int)
            self.assertGreater(counted["tokens"], 0)
        else:
            self.assertIsNone(counted["tokens"])
        self.assertEqual(counted["chars"], 27)

    def test_measure_messages_never_invents_deployment_tokens(self):
        measured = measure_messages(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ]
        )
        self.assertEqual(measured["message_count"], 2)
        self.assertEqual(measured["total_chars"], 5)
        self.assertIsNone(measured["deployment_tokens"])

    def test_recording_wraps_without_changing_decisions(self):
        stub = _StubBackend()
        wrapped = RecordingBackend(stub, call_site="A")
        result = wrapped.decide("s1", [{"role": "user", "content": "x"}])
        self.assertEqual(result["tool"], "ask_patient")
        self.assertEqual(len(wrapped.records), 1)
        self.assertEqual(wrapped.records[0]["result_tool"], "ask_patient")
        self.assertEqual(wrapped.records[0]["result_argument_keys"], ["fact_key"])
        self.assertIsNone(wrapped.records[0]["error"])

    def test_recording_reraises_and_records_errors(self):
        stub = _StubBackend(error=RuntimeError("boom"))
        wrapped = RecordingBackend(stub, call_site="A")
        with self.assertRaises(RuntimeError):
            wrapped.decide("s1", [{"role": "user", "content": "x"}])
        self.assertIn("boom", wrapped.records[0]["error"])

    def test_recording_writes_one_file_per_call(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            stub = _StubBackend()
            wrapped = RecordingBackend(stub, call_site="B", directory=tmp)
            wrapped.decide("s9", [{"role": "user", "content": "y"}])
            wrapped.decide_cancellable("s9", [{"role": "user", "content": "z"}], None)
            import os

            files = sorted(os.listdir(tmp))
            self.assertEqual(len(files), 2)
            with open(os.path.join(tmp, files[0]), encoding="utf-8") as fh:
                saved = json.load(fh)
            self.assertEqual(saved["call_site"], "B")
            self.assertEqual(saved["seq"], 1)

    def test_summarize_aggregates_per_call(self):
        stub = _StubBackend()
        wrapped = RecordingBackend(stub, call_site="A")
        wrapped.decide("s1", [{"role": "system", "content": "abc"}])
        summary = summarize_records(wrapped.records)
        self.assertEqual(summary["count"], 1)
        self.assertEqual(summary["records"][0]["total_chars"], 3)


if __name__ == "__main__":
    unittest.main()
