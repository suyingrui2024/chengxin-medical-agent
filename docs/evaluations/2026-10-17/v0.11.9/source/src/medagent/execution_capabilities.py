"""Host-owned capability dispatcher. No model-selected code, modules or paths."""

from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import time
from uuid import UUID, uuid4

from .extensions.input_evidence import CapabilityError, units_evidence
from .extensions.glucose import glucose_evidence
from .extensions.builtin import load_registry
from .extensions.contracts import validate, validate_references
from .presentation import calculation_presentation

PROTOCOL = "execution-capabilities/v1"
HASH = re.compile(r"[0-9a-f]{64}")


def implementation_sha256():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in [
        "execution_capabilities.py",
        "extensions/builtin.py",
        "extensions/registry.py",
        "extensions/contracts.py",
        "extensions/input_evidence.py",
        "extensions/glucose.py",
        "extensions/conversion_forms.py",
        "presentation.py",
        "domain.py",
        "interpreter.py",
    ]:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes() + b"\0")
    return digest.hexdigest()


def freeze_binding(registry=None):
    snapshot = (registry or load_registry()).snapshot()
    return {
        "protocol": PROTOCOL,
        "template_id": snapshot["template"]["id"],
        "enabled": [item["id"] for item in snapshot["capabilities"] if item["enabled"]],
        "registry_sha256": snapshot["sha256"],
        "implementation_sha256": implementation_sha256(),
    }


def validate_binding(binding):
    if binding is None:  # Older workspaces do not silently gain new permissions.
        return
    if (
        not isinstance(binding, dict)
        or set(binding)
        != {
            "protocol",
            "template_id",
            "enabled",
            "registry_sha256",
            "implementation_sha256",
        }
        or binding["protocol"] != PROTOCOL
        or not isinstance(binding["template_id"], str)
        or len(binding["template_id"]) > 80
        or not isinstance(binding["enabled"], list)
        or len(binding["enabled"]) > 64
        or any(
            not isinstance(v, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{2,80}", v)
            for v in binding["enabled"]
        )
        or len(set(binding["enabled"])) != len(binding["enabled"])
        or any(
            not isinstance(binding[k], str) or not HASH.fullmatch(binding[k])
            for k in ["registry_sha256", "implementation_sha256"]
        )
    ):
        raise ValueError("计算能力绑定格式无效")


def bound_registry(binding):
    validate_binding(binding)
    if binding is None:
        raise CapabilityError("capability_binding_missing", "旧工作区未绑定注册计算；请新建工作区使用。")
    try:
        registry = load_registry(binding["template_id"])
        for item in registry.snapshot()["capabilities"]:
            registry = registry.with_enabled(
                item["id"], item["id"] in binding["enabled"]
            )
    except ValueError:
        raise CapabilityError(
            "capability_version_unavailable", "原能力组合已不可用；通用执行仍可使用。"
        ) from None
    if (
        registry.revision != binding["registry_sha256"]
        or implementation_sha256() != binding["implementation_sha256"]
    ):
        raise CapabilityError(
            "capability_version_unavailable", "能力清单或实现已变化；拒绝替换原版本计算，请新建工作区。"
        )
    return registry


# Each added capability needs an explicit input-origin policy; manifests cannot add code.
EVIDENCE_POLICIES = {
    "calculate.units": units_evidence,
    "calculate.glucose": glucose_evidence,
}


def dispatch(payload):
    registry = bound_registry(payload.get("binding"))
    available = [
        item for item in registry.descriptions() if item["id"] in EVIDENCE_POLICIES
    ]
    if payload.get("operation") == "catalog":
        return {
            "protocol": PROTOCOL,
            "registry_sha256": registry.revision,
            "implementation_sha256": implementation_sha256(),
            "capabilities": available,
            "input_policy": "引用真实用户原文，核对对应项目、数值及原单位；各工具适用范围独立。目标单位仍待核对，结果不写入病例。",
        }
    if payload.get("operation") != "execute":
        raise CapabilityError("unknown_operation", "计算接口不存在")
    request = payload.get("request")
    if not isinstance(request, dict) or set(request) != {
        "capability_id",
        "input",
        "evidence",
    }:
        raise CapabilityError(
            "invalid_arguments", "仅接受 capability_id、input、evidence 三个字段。"
        )
    identity = request["capability_id"]
    entry = next((item for item in available if item["id"] == identity), None)
    if entry is None:
        raise CapabilityError("capability_unavailable", "能力未启用、未注册或尚无输入出处核对策略。")
    try:
        validate(request["input"], entry["input_schema"])
    except (ValueError, TypeError):
        raise CapabilityError("invalid_arguments", "计算参数不符合能力目录的输入契约。") from None
    evidence = EVIDENCE_POLICIES[identity](
        request["input"], request["evidence"], payload.get("sources", [])
    )
    try:
        result = registry.execute(
            identity, request["input"], "execution_model_proposal"
        )
    except ValueError as error:
        raise CapabilityError("calculation_rejected", str(error)) from None
    result.update(
        execution_id=str(uuid4()),
        executed_at=time.time(),
        evidence=evidence,
        implementation_sha256=implementation_sha256(),
        workspace_id=payload["workspace_id"],
        turn_id=payload["turn_id"],
        protocol=PROTOCOL,
    )
    result["presentation"] = calculation_presentation(result)
    return result


def validate_receipt(result):
    """Validate saved host receipts without requiring today's plugin version."""
    if (
        not isinstance(result, dict)
        or result.get("protocol") != PROTOCOL
        or result.get("input_origin") != "execution_model_proposal"
        or result.get("input_status") != "unconfirmed"
        or result.get("quality") != "not_assessed"
        or not isinstance(result.get("evidence"), dict)
        or not isinstance(result["evidence"].get("quote"), str)
        or not 1 <= len(result["evidence"]["quote"]) <= 1024
        or result["evidence"].get("scope")
        != (
            "analyte_quantity_and_source_unit_only"
            if result.get("capability_id") == "calculate.glucose"
            else "quantity_and_source_unit_only"
        )
        or result.get("renderer") != "calculation"
        or type(result.get("executed_at")) not in {int, float}
        or not math.isfinite(result["executed_at"])
        or result["executed_at"] <= 0
        or len(json.dumps(result, ensure_ascii=False).encode()) > 16000
    ):
        raise ValueError("计算回执格式无效")
    for key in ["registry_sha256", "implementation_sha256"]:
        if not isinstance(result.get(key), str) or not HASH.fullmatch(result[key]):
            raise ValueError("计算回执版本无效")
    for key in ["capability_id", "version", "execution_id", "workspace_id", "turn_id"]:
        if not isinstance(result.get(key), str) or not 1 <= len(result[key]) <= 100:
            raise ValueError("计算回执标识无效")
    for value in [
        result["execution_id"],
        result["workspace_id"],
        result["turn_id"],
        result["evidence"].get("source_turn_id"),
    ]:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError("计算回执来源无效")
    validate(
        result.get("data"),
        {
            "type": "object",
            "required": ["value", "unit", "formula", "method", "limitations"],
            "properties": {
                "value": {"type": "string"},
                "unit": {"type": "string"},
                "formula": {"type": "string"},
                "method": {"type": "string"},
                "limitations": {"type": "array", "items": {"type": "string"}},
                "approximate": {"type": "boolean"},
                "precision_policy": {"type": "string"},
            },
        },
    )
    if not isinstance(result.get("input"), dict) or len(result["input"]) > 32:
        raise ValueError("计算回执输入无效")
    if "references" in result:
        validate_references(result["references"])
    if "name" in result and (
        not isinstance(result["name"], str) or not 1 <= len(result["name"]) <= 100
    ):
        raise ValueError("计算回执名称无效")
    if result.get("capability_id") == "calculate.glucose" and (
        not result.get("references")
        or not isinstance(result.get("data", {}).get("approximate"), bool)
        or not isinstance(result.get("data", {}).get("precision_policy"), str)
    ):
        raise ValueError("血糖回执缺少近似规则或来源")
    if result.get("presentation") != calculation_presentation(result):
        raise ValueError("计算回执展示与数据不一致")
    return deepcopy(result)


def main():
    try:
        raw = sys.stdin.buffer.read(128001)
        if len(raw) > 128000:
            raise CapabilityError("request_too_large", "计算请求过大")
        result = dispatch(json.loads(raw))
        response = {"ok": True, "result": result}
    except CapabilityError as error:
        response = {"ok": False, "error": {"code": error.code, "message": str(error)}}
    except (ValueError, TypeError, KeyError, OSError):
        response = {
            "ok": False,
            "error": {
                "code": "capability_dispatch_failed",
                "message": "计算输入或受信实现不可用，未生成成功回执。",
            },
        }
    sys.stdout.write(json.dumps(response, ensure_ascii=False))
