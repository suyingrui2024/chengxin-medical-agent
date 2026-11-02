"""The medical-image recognition payload shared by the records API and
measurement suites."""


def recognition():
    return {
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
            },
            {
                "number_id": "n-2",
                "value_text": None,
                "context": "血小板计数 1?6 ×10^9/L",
                "clarity": "unclear",
                "alternatives": ["106", "136", "196"],
                "uncertainty_reason": "中间数字受到反光遮挡",
                "page_index": 1,
            },
        ],
        "content": {
            "description": "这是一份血常规检查报告。",
            "clarity": "clear",
            "unclear_parts": [],
        },
        "warnings": [],
    }

