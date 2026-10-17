from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import re
from importlib import resources
from typing import Callable, Optional

from .contracts import validate, validate_schema
from ..presentation import calculation_presentation

API_VERSION = "medical-capability/v1"


@dataclass(frozen=True)
class Capability:
    manifest: dict
    execute: Optional[Callable] = None
    interpret: Optional[Callable] = None


class CapabilityRegistry:
    """An immutable composition. Reconfiguration builds a new registry/engine."""

    def __init__(self, capabilities, template, enabled=None):
        self._entries = {}
        self._template = deepcopy(template)
        for capability in capabilities:
            item = deepcopy(capability.manifest)
            required_manifest = {
                "id",
                "name",
                "version",
                "api_version",
                "kind",
                "description",
                "permissions",
                "review_status",
                "ui",
                "validation",
            }
            if required_manifest - set(item):
                raise ValueError("能力清单缺少必填字段")
            identity = item.get("id", "")
            if (
                not re.fullmatch(r"[a-z][a-z0-9_.-]{2,80}", identity)
                or identity in self._entries
            ):
                raise ValueError("能力 ID 无效或重复")
            if item.get("api_version") != API_VERSION or not re.fullmatch(
                r"\d+\.\d+\.\d+", item.get("version", "")
            ):
                raise ValueError("能力接口或版本不兼容")
            if item.get("permissions") != []:
                raise ValueError("当前受信注册表只接受无需外部权限的能力")
            if item.get("kind") not in {"calculation", "self_check"}:
                raise ValueError("能力类别未注册")
            if item["kind"] == "calculation":
                if capability.execute is None or item.get("ui") != "calculation":
                    raise ValueError("计算能力缺少实现或标准展示组件")
                validate_schema(item["input_schema"])
                validate_schema(item["output_schema"])
                form = item.get("patient_form")
                if form is not None and (
                    form != "unit_conversion/v1"
                    or set(item["input_schema"]["properties"])
                    != {"value", "from_unit", "to_unit"}
                    or any(
                        item["input_schema"]["properties"][key]["type"] != "string"
                        for key in ["value", "from_unit", "to_unit"]
                    )
                    or any(
                        set(item["input_schema"]["properties"][key].get("enum", []))
                        != {"kg", "g", "mg", "L", "mL"}
                        for key in ["from_unit", "to_unit"]
                    )
                ):
                    raise ValueError("患者输入组件或参数契约不兼容")
            else:
                task = item.get("task", {})
                required = {
                    "id",
                    "result_key",
                    "input_kind",
                    "title",
                    "purpose",
                    "applies_when",
                    "equipment",
                    "asset",
                    "steps",
                    "stop_conditions",
                    "response_prompt",
                    "options",
                    "clinical_limits",
                    "review_status",
                }
                if (
                    required - set(task)
                    or not task["steps"]
                    or capability.interpret is None
                ):
                    raise ValueError("图示内容包缺少任务字段或解释器")
                if item.get("ui") != "step_task" or not re.fullmatch(
                    r"/[a-z0-9-]+\.svg", task["asset"]
                ):
                    raise ValueError("任务只能使用已打包的图示组件")
                if (
                    not resources.files("medagent")
                    .joinpath("web", task["asset"].lstrip("/"))
                    .is_file()
                ):
                    raise ValueError("任务引用的图示未打包")
                assets = task.get("step_assets")
                for field in ("request_phrases", "cancel_phrases"):
                    phrases = task.get(field, [])
                    if (
                        not isinstance(phrases, list)
                        or len(phrases) > 16
                        or any(
                            not isinstance(phrase, str) or not 2 <= len(phrase) <= 40
                            for phrase in phrases
                        )
                        or len(set(phrases)) != len(phrases)
                    ):
                        raise ValueError("任务请求/取消短语必须是最多16条、各2至40字的去重列表")
                invalidators = task.get("invalidate_on", ["chief_complaint"])
                if (
                    not isinstance(invalidators, list)
                    or any(
                        not isinstance(key, str)
                        or key not in {"chief_complaint", "age_years"}
                        for key in invalidators
                    )
                    or len(set(invalidators)) != len(invalidators)
                ):
                    raise ValueError("任务失效依赖必须是去重的主诉或年龄字段列表")
                if assets is not None:
                    if not isinstance(assets, list) or len(assets) != len(
                        task["steps"]
                    ):
                        raise ValueError("分步图示必须与步骤一一对应")
                    for asset in assets:
                        if not isinstance(asset, str) or not re.fullmatch(
                            r"/[a-z0-9-]+\.svg(?:#step-[1-9][0-9]?)?", asset
                        ):
                            raise ValueError("分步图示只能使用已打包的本地 SVG")
                        if (
                            not resources.files("medagent")
                            .joinpath("web", asset.split("#")[0].lstrip("/"))
                            .is_file()
                        ):
                            raise ValueError("分步图示未打包")
            self._entries[identity] = Capability(
                item, capability.execute, capability.interpret
            )
        selected = set(template["enabled"] if enabled is None else enabled)
        if selected - self._entries.keys():
            raise ValueError("模板引用了未注册能力")
        self._enabled = frozenset(selected)
        self._tasks = {}
        priority = {
            identity: index for index, identity in enumerate(template["enabled"])
        }
        for identity in sorted(
            selected, key=lambda key: (priority.get(key, len(priority)), key)
        ):
            item = self._entries[identity].manifest
            if item["kind"] != "self_check":
                continue
            task = deepcopy(item["task"])
            if task["id"] in self._tasks or any(
                t["result_key"] == task["result_key"] for t in self._tasks.values()
            ):
                raise ValueError("任务 ID 或结果字段冲突")
            task["provider"] = {"id": identity, "version": item["version"]}
            self._tasks[task["id"]] = task
        self.revision = self.snapshot()["sha256"]

    @property
    def tasks(self):
        return deepcopy(self._tasks)

    def snapshot(self):
        data = {
            "api_version": API_VERSION,
            "template": deepcopy(self._template),
            "capabilities": [
                {**deepcopy(entry.manifest), "enabled": identity in self._enabled}
                for identity, entry in sorted(self._entries.items())
            ],
        }
        data["sha256"] = hashlib.sha256(
            json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        return data

    def catalog(self):
        return {**self.snapshot(), "revision": self.revision}

    def with_enabled(self, identity, enabled):
        if identity not in self._entries or type(enabled) is not bool:
            raise ValueError("能力 ID 或启用状态无效")
        selected = set(self._enabled)
        (selected.add if enabled else selected.discard)(identity)
        return CapabilityRegistry(self._entries.values(), self._template, selected)

    def descriptions(self):
        return [
            deepcopy(self._entries[key].manifest)
            for key in sorted(self._enabled)
            if self._entries[key].execute is not None
        ]

    def execute(self, identity, arguments, origin):
        if identity not in self._enabled:
            raise ValueError("能力未启用或未注册")
        entry = self._entries[identity]
        if entry.execute is None:
            raise ValueError("该内容包通过图示任务执行，不接受计算调用")
        validate(arguments, entry.manifest["input_schema"])
        # Implementations receive a copy, with no case object or service registry.
        result = entry.execute(deepcopy(arguments))
        validate(result, entry.manifest["output_schema"], "output")
        payload = {
            "capability_id": identity,
            "version": entry.manifest["version"],
            "registry_sha256": self.revision,
            "input": deepcopy(arguments),
            "data": result,
            "input_origin": origin,
            "input_status": "unconfirmed",
            "quality": "not_assessed",
            "review_status": entry.manifest["review_status"],
            "renderer": entry.manifest["ui"],
        }
        payload["presentation"] = calculation_presentation(
            payload,
            {
                key: schema.get("title", key)
                for key, schema in entry.manifest["input_schema"]
                .get("properties", {})
                .items()
            },
        )
        return payload

    def interpret_task(self, task_id, text, choice):
        from ..domain import Assertion, ObservationQuality, Source
        from ..interpreter import Interpretation

        task = self._tasks[task_id]
        entry = self._entries[task["provider"]["id"]]
        if choice in {"not_available", "unable", "skip"}:
            result = Interpretation(
                {"reason": choice},
                Assertion.UNKNOWN,
                Source.PATIENT,
                ObservationQuality.NOT_DONE,
            )
        else:
            result = entry.interpret(text, choice)
        if (
            not isinstance(result, Interpretation)
            or not isinstance(result.source, Source)
            or not isinstance(result.assertion, Assertion)
            or not isinstance(result.quality, ObservationQuality)
            or result.source != Source.PATIENT
            or result.assertion not in {Assertion.PRESENT, Assertion.UNKNOWN}
            or result.quality
            not in {
                ObservationQuality.NOT_ASSESSED,
                ObservationQuality.INADEQUATE,
                ObservationQuality.NOT_DONE,
            }
            or (
                result.quality
                in {ObservationQuality.INADEQUATE, ObservationQuality.NOT_DONE}
                and result.assertion != Assertion.UNKNOWN
            )
        ):
            raise ValueError("图示反馈不能升级来源、宣称质量已验证或补造阴性")
        json.dumps(result.value, allow_nan=False)
        if (
            result.assertion == Assertion.UNKNOWN
            and result.quality != ObservationQuality.NOT_DONE
            and result.value is not None
        ):
            raise ValueError("无法确认的观察应保留空值")
        return task["result_key"], result
