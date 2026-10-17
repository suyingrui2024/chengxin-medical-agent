import json, sys
from units import convert
def main():
    data = json.load(sys.stdin)
    result = convert(data["value"], data["from"], data["to"])
    print(json.dumps({"result": format(float(result), ".2f")}))
if __name__ == "__main__": main()
