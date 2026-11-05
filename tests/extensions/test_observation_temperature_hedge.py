"""体温任务的模糊读数（使用体验评测 R3-02）。

"大概38度"曾经被当成确定的 38.0℃：38 能直接决定分诊级别，而"大概"
"大约""差不多""左右""估计"这些说法在口语里比"可能"更常见。反方向
更危险——真实高热被说成"大概37度"就是一次假阴性。

这里从任务解释入口 ``observation`` 验证：模糊读数落成 UNKNOWN +
INADEQUATE（不写确定值），精确读数仍然照常记录。
"""

import unittest

from medagent.core.domain import Assertion, ObservationQuality
from medagent.extensions.builtin import observation


class HedgedReadingTests(unittest.TestCase):
    def test_common_hedges_do_not_become_a_definite_value(self):
        for text in ("大概38度", "大约38度", "差不多38度", "38度左右", "估计38度", "可能38度"):
            with self.subTest(text=text):
                result = observation(text, None, "temperature")
                self.assertIsNone(result.value)
                self.assertEqual(result.assertion, Assertion.UNKNOWN)
                self.assertEqual(result.quality, ObservationQuality.INADEQUATE)

    def test_a_precise_reading_is_still_recorded(self):
        for text, expected in (("38.2℃", 38.2), ("腋温 39.1℃", 39.1), ("38.2", 38.2)):
            with self.subTest(text=text):
                result = observation(text, None, "temperature")
                self.assertEqual(result.value, expected)
                self.assertEqual(result.assertion, Assertion.PRESENT)
                self.assertEqual(result.quality, ObservationQuality.NOT_ASSESSED)

    def test_a_hedge_before_a_precise_number_is_still_a_hedge(self):
        result = observation("大概38.2℃", None, "temperature")
        self.assertEqual(result.quality, ObservationQuality.INADEQUATE)


if __name__ == "__main__":
    unittest.main()
