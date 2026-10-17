import json
import sys

from units import convert


def main():
    try:
        data = json.load(sys.stdin)
        result = convert(data["value"], data["from"], data["to"])
    except (ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps({"result": result}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
