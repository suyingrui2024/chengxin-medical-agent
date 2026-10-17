"""计算 1.25 x 1000，使用 Decimal 保证精度，并写出 result.json。"""

import json
from decimal import Decimal
from pathlib import Path


def main() -> None:
    result = Decimal("1.25") * Decimal("1000")
    payload = {"result": str(result.normalize().quantize(Decimal("1")))}
    out = Path(__file__).resolve().parent / "result.json"
    out.write_text(json.dumps(payload), encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
