import json
from decimal import Decimal, getcontext

getcontext().prec = 28

a = Decimal("1.25")
b = Decimal("1000")
product = a * b

# 归一化去尾零后输出整数形式的字符串
result_str = format(product.normalize(), "f")
if "." in result_str:
    result_str = result_str.rstrip("0").rstrip(".")

with open("/workspace/result.json", "w", encoding="utf-8") as f:
    json.dump({"result": result_str}, f, ensure_ascii=False)

print(json.dumps({"result": result_str}, ensure_ascii=False))
