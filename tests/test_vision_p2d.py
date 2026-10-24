"""Healthcheck P2-D fixes for visual recognition (medical_images only).

Pins, one per item:
1. A truncated PNG (IHDR only, no IDAT/IEND) is rejected, and so are a
   PNG cut short mid-chunk and a PNG missing its IEND tail; a complete
   PNG still passes.
2. page_index=True/False are rejected (bool is an int subclass).
3. A contract rejection keeps its original rule category on the
   RecognitionError (validation_rule) and on the recognizer
   (last_validation_rule), while the message stays the single generic
   patient-visible wording; distinct rules yield distinct categories.

Red lines pinned here too: the P0/P1 behaviour this worker must not
touch -- exactly one retry on transport failure, and a failure on a
reused instance leaves usage/duration cleared (not stale).
"""

import binascii
import json
import struct
import unittest
import zlib
from unittest import mock
from urllib.error import URLError

from medagent.medical_images import (
    DeepSeekVisionRecognizer,
    MedicalImageError,
    RecognitionError,
    inspect_image,
    validate_recognition,
)


def _chunk(kind, value):
    return (
        struct.pack(">I", len(value))
        + kind
        + value
        + struct.pack(">I", binascii.crc32(kind + value) & 0xFFFFFFFF)
    )


def _png_bytes(*chunks):
    return b"\x89PNG\r\n\x1a\n" + b"".join(chunks)


def _complete_png(width=64, height=64):
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    pixels = b"".join(b"\x00" + b"\xff\xff\xff" * width for _ in range(height))
    return _png_bytes(
        _chunk(b"IHDR", header),
        _chunk(b"IDAT", zlib.compress(pixels)),
        _chunk(b"IEND", b""),
    )


def _recognition(**overrides):
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
    for key, value in overrides.items():
        body[key] = value
    return body


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _provider_payload(body):
    return {"choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}], "usage": {}}


class P2DTests(unittest.TestCase):
    def setUp(self):
        self.recognizer = DeepSeekVisionRecognizer(
            "https://api.deepseek.com", "test-key", retry_backoff_seconds=0
        )
        self.image = inspect_image(_complete_png())

    # --- 1. Truncated PNG is fail-closed -------------------------------
    def test_complete_png_still_accepted(self):
        inspected = inspect_image(_complete_png())
        self.assertEqual((inspected.width, inspected.height), (64, 64))

    def test_ihdr_only_png_rejected(self):
        header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
        with self.assertRaises(MedicalImageError):
            inspect_image(_png_bytes(_chunk(b"IHDR", header)))

    def test_png_missing_iend_rejected(self):
        header = struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0)
        pixels = b"".join(b"\x00" + b"\xff\xff\xff" * 64 for _ in range(64))
        with self.assertRaises(MedicalImageError):
            inspect_image(
                _png_bytes(_chunk(b"IHDR", header), _chunk(b"IDAT", zlib.compress(pixels)))
            )

    def test_png_cut_mid_chunk_rejected(self):
        complete = _complete_png()
        with self.assertRaises(MedicalImageError):
            inspect_image(complete[:-10])

    def test_png_with_trailing_bytes_rejected(self):
        with self.assertRaises(MedicalImageError):
            inspect_image(_complete_png() + b"junk")

    # --- 2. page_index bool rejected ------------------------------------
    def test_page_index_true_rejected(self):
        body = _recognition()
        body["numbers"][0]["page_index"] = True
        with self.assertRaises(MedicalImageError):
            validate_recognition(body)

    def test_page_index_false_rejected(self):
        body = _recognition()
        body["numbers"][0]["page_index"] = False
        with self.assertRaises(MedicalImageError):
            validate_recognition(body)

    def test_page_index_int_one_still_accepted(self):
        self.assertEqual(validate_recognition(_recognition())["numbers"][0]["page_index"], 1)

    # --- 3. Rejection category retained ---------------------------------
    def _recognize_body(self, body):
        with mock.patch(
            "medagent.medical_images.urlopen",
            return_value=FakeResponse(_provider_payload(body)),
        ):
            return self.recognizer.recognize(self.image)

    def test_rejection_keeps_rule_category_and_generic_message(self):
        body = _recognition()
        body["numbers"][0]["clarity"] = "maybe"
        with self.assertRaises(RecognitionError) as ctx:
            self._recognize_body(body)
        self.assertEqual(str(ctx.exception), "视觉模型没有返回有效的识别协议")
        self.assertEqual(ctx.exception.validation_rule, "图片识别清晰度无效")
        self.assertEqual(self.recognizer.last_validation_rule, "图片识别清晰度无效")

    def test_distinct_rules_yield_distinct_categories(self):
        body_a = _recognition()
        body_a["numbers"][0]["clarity"] = "maybe"
        body_b = _recognition()
        body_b["numbers"][0]["page_index"] = 2
        categories = []
        for body in (body_a, body_b):
            with self.assertRaises(RecognitionError) as ctx:
                self._recognize_body(body)
            categories.append(ctx.exception.validation_rule)
        self.assertEqual(categories[0], "图片识别清晰度无效")
        self.assertEqual(categories[1], "第一版只支持单页图片")
        self.assertNotEqual(categories[0], categories[1])

    def test_malformed_envelope_gets_parse_category(self):
        with mock.patch(
            "medagent.medical_images.urlopen",
            return_value=FakeResponse({"choices": []}),
        ):
            with self.assertRaises(RecognitionError) as ctx:
                self.recognizer.recognize(self.image)
        self.assertEqual(str(ctx.exception), "视觉模型没有返回有效的识别协议")
        self.assertEqual(ctx.exception.validation_rule, "识别结果解析失败")

    def test_success_clears_validation_rule(self):
        body = _recognition()
        body["numbers"][0]["clarity"] = "maybe"
        with self.assertRaises(RecognitionError):
            self._recognize_body(body)
        self.assertIsNotNone(self.recognizer.last_validation_rule)
        self._recognize_body(_recognition())
        self.assertIsNone(self.recognizer.last_validation_rule)

    # --- Red lines: P0/P1 behaviour untouched ---------------------------
    def test_transport_failure_still_retried_exactly_once(self):
        calls = {"n": 0}

        def flaky(request, timeout):
            calls["n"] += 1
            if calls["n"] == 1:
                raise URLError("connection reset")
            return FakeResponse(_provider_payload(_recognition()))

        with mock.patch("medagent.medical_images.urlopen", side_effect=flaky):
            result = self.recognizer.recognize(self.image)
        self.assertEqual(calls["n"], 2)
        self.assertTrue(self.recognizer.last_retried)
        self.assertEqual(result["numbers"][0]["value_text"], "76")

    def test_failed_call_on_reused_instance_clears_telemetry(self):
        with mock.patch(
            "medagent.medical_images.urlopen",
            return_value=FakeResponse(
                {**_provider_payload(_recognition()), "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}
            ),
        ):
            self.recognizer.recognize(self.image)
        self.assertIsNotNone(self.recognizer.last_usage)
        with mock.patch(
            "medagent.medical_images.urlopen", side_effect=URLError("down")
        ):
            with self.assertRaises(RecognitionError):
                self.recognizer.recognize(self.image)
        self.assertIsNone(self.recognizer.last_usage)
        self.assertIsNone(self.recognizer.last_duration_ms)


if __name__ == "__main__":
    unittest.main()
