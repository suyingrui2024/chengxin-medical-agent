"""Small, deliberately bounded JSON contract dialect used by capability v1."""
import math


def validate(value, schema, path="input"):
    kind = schema.get("type")
    valid = {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "boolean": lambda: type(value) is bool,
        "number": lambda: type(value) in (int, float) and math.isfinite(value),
    }
    if kind not in valid or not valid[kind]():
        raise ValueError(f"{path} 类型应为 {kind}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} 不在允许的选项中")
    if kind == "object":
        fields = schema.get("properties", {})
        if set(schema.get("required", [])) - set(value):
            raise ValueError(f"{path} 缺少必填字段")
        if set(value) - set(fields):
            raise ValueError(f"{path} 包含未注册字段")
        for key, item in value.items():
            validate(item, fields[key], f"{path}.{key}")
    elif kind == "array":
        if len(value) > schema.get("maxItems", 100):
            raise ValueError(f"{path} 项目过多")
        for i, item in enumerate(value):
            validate(item, schema["items"], f"{path}[{i}]")
    elif kind == "string":
        if (
            not schema.get("minLength", 0)
            <= len(value)
            <= schema.get("maxLength", 4096)
        ):
            raise ValueError(f"{path} 长度不符合约定")
    elif kind == "number":
        if (
            not schema.get("minimum", -math.inf)
            <= value
            <= schema.get("maximum", math.inf)
        ):
            raise ValueError(f"{path} 超出范围")


def validate_schema(schema):
    if not isinstance(schema, dict) or schema.get("type") not in {
        "object",
        "array",
        "string",
        "boolean",
        "number",
    }:
        raise ValueError("不支持的能力契约类型")
    allowed = {
        "type",
        "title",
        "description",
        "enum",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "maxItems",
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
    }
    if set(schema) - allowed:
        raise ValueError("能力契约含未支持的关键字")
    if schema["type"] == "object":
        if schema.get("additionalProperties") is not False or not isinstance(
            schema.get("properties"), dict
        ):
            raise ValueError("对象契约必须声明字段并关闭额外属性")
        if set(schema.get("required", [])) - set(schema["properties"]):
            raise ValueError("必填字段未注册")
        for child in schema["properties"].values():
            validate_schema(child)
    elif schema["type"] == "array":
        validate_schema(schema["items"])
