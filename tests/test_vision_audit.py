"""Vision audit batch (phase 3-D): bounded retry + usage telemetry.

Covers only the new behaviour in DeepSeekVisionRecognizer.recognize:
one bounded retry on transient transport failure, no retry on provider
or contract errors, and observation-only usage/duration attributes.
The recognition contract itself is unchanged.
"""

import binascii
import json
import struct
import unittest
import zlib
from unittest import mock
from urllib.error import HTTPError, URLError

from medagent.medical_images import (
    DeepSeekVisionRecognizer,
    RecognitionError,
    inspect_image,
)


def png():
    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind, value):
        return (
            struct.pack(">I", len(value))
            + kind
            + value
            + struct.pack(">I", binascii.crc32(kind + value) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * 64 for _ in range(64))
    return signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels)) + chunk(b"IEND", b"")


def recognition_payload():
    body = {
        "schema": "medical-image-recognition/v1",
        "document_type": "laboratory_report",
        "numbers": [
            {
                "number_id": "n-1",
                "value_text": "76",
                "context": "血红蛋白 76 g/L",
                "clarity": "clear",
                "alternatives": [],
                "uncertainty_reason": None,
                "page_index": 1,
            }
        ],
        "content": {
            "description": "血常规报告",
            "clarity": "clear",
            "unclear_parts": [],
        },
        "warnings": [],
    }
    return {
        "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 1200, "completion_tokens": 80, "total_tokens": 1280},
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class VisionAuditTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = DeepSeekVisionRecognizer(
            "https://api.deepseek.com", "test-key", retry_backoff_seconds=0
        )
        self.image = inspect_image(png())

    def test_success_records_usage_and_duration(self):
        with mock.patch(
            "medagent.medical_images.urlopen",
            return_value=FakeResponse(recognition_payload()),
        ) as urlopen:
            result = self.recognizer.recognize(self.image)
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(result["numbers"][0]["value_text"], "76")
        self.assertEqual(self.recognizer.last_usage["total_tokens"], 1280)
        self.assertFalse(self.recognizer.last_retried)
        self.assertIsInstance(self.recognizer.last_duration_ms, int)

    def test_transient_network_failure_retried_once(self):
        calls = {"n": 0}

        def flaky(request, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise URLError("connection reset")
            return FakeResponse(recognition_payload())

        with mock.patch("medagent.medical_images.urlopen", side_effect=flaky):
            result = self.recognizer.recognize(self.image)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.recognizer.last_retried)
        self.assertEqual(result["numbers"][0]["value_text"], "76")

    def test_persistent_network_failure_fails_after_two_attempts(self):
        with mock.patch(
            "medagent.medical_images.urlopen", side_effect=URLError("down")
        ) as urlopen:
            with self.assertRaises(RecognitionError):
                self.recognizer.recognize(self.image)
        self.assertEqual(urlopen.call_count, 2)

    def test_provider_error_is_not_retried(self):
        error = HTTPError("https://x", 429, "rate limited", {}, None)
        with mock.patch(
            "medagent.medical_images.urlopen", side_effect=error
        ) as urlopen:
            with self.assertRaises(RecognitionError) as ctx:
                self.recognizer.recognize(self.image)
        self.assertEqual(urlopen.call_count, 1)
        self.assertIn("429", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
