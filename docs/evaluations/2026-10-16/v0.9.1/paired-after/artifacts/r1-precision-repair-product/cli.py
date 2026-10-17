import json
import sys

from units import convert


def main():
    data = json.load(sys.stdin)
    result = convert(data["value"], data["from"], data["to"])
    sys.stdout.write(json.dumps({"result": result}, separators=(",", ":")) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError) as exc:
        sys.stderr.write("error: %s\n" % (exc,))
        sys.exit(2)
