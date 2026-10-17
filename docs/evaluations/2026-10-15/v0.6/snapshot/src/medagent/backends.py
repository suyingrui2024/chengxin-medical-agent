"""Replaceable decision transports. No transport can mutate the clinical state."""
from __future__ import annotations

import json
import os
import subprocess
import signal
from threading import Lock
from pathlib import Path
from typing import Any, Dict, List, Protocol
from urllib import request
from urllib.parse import urlparse
from time import perf_counter

from .events import Event, EventStore


class DecisionBackend(Protocol):
    name: str

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        ...


def parse_decision(raw: str) -> Dict[str, Any]:
    if not isinstance(raw, str) or len(raw) > 64000:
        raise ValueError("invalid decision size")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or set(parsed) != {"tool", "arguments"}:
        raise ValueError("decision must contain only tool and arguments")
    if not isinstance(parsed["tool"], str) or not isinstance(parsed["arguments"], dict):
        raise ValueError("invalid decision envelope")
    return parsed


class CompatibleBackend:
    name = "compatible"

    def __init__(self, events: EventStore, config: Dict[str, str] = None) -> None:
        self.events = events
        settings = config if config is not None else os.environ
        self.base_url = settings["MEDAGENT_LLM_BASE_URL"].rstrip("/")
        self.api_key = settings["MEDAGENT_LLM_API_KEY"]
        self.model = settings["MEDAGENT_LLM_MODEL"]
        self.max_tokens = int(settings.get("MEDAGENT_LLM_MAX_TOKENS", "2048"))
        if not 128 <= self.max_tokens <= 8192:
            raise ValueError("max output tokens must be between 128 and 8192")
        self.thinking = settings.get("MEDAGENT_LLM_THINKING", "disabled")
        if self.thinking not in {"enabled", "disabled"}:
            raise ValueError("thinking must be enabled or disabled")

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
        }
        if urlparse(self.base_url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": self.thinking}
        self.events.append(
            Event(
                session_id,
                "model.requested",
                {"backend": self.name, "payload": payload},
            )
        )
        req = request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
        )
        started = perf_counter()
        with request.urlopen(req, timeout=45) as response:
            result = json.loads(response.read(256000).decode())
        raw = result["choices"][0]["message"]["content"]
        usage = result.get("usage") or {}
        metrics = {
            key: value
            for key, value in usage.items()
            if key
            in {
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "prompt_cache_hit_tokens",
                "prompt_cache_miss_tokens",
            }
            and type(value) is int
            and value >= 0
        }
        self.events.append(
            Event(
                session_id,
                "model.responded",
                {
                    "backend": self.name,
                    "content": raw,
                    "usage": metrics,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "model": result.get("model"),
                    "finish_reason": result["choices"][0].get("finish_reason"),
                },
            )
        )
        return parse_decision(raw)


class DshBackend:
    """Use the pinned official DSH runtime through its stdio SDK bridge.

    The bridge boots the checked-in medical profile, has no patient-facing
    capabilities, and returns a proposal. Python validates/executes that proposal.
    """

    name = "dsh"

    def __init__(
        self, events: EventStore, project_root: Path, config: Dict[str, str] = None
    ) -> None:
        self.events = events
        self.root = project_root.resolve()
        settings = config if config is not None else os.environ
        self.config = {
            k: settings[k]
            for k in (
                "MEDAGENT_LLM_BASE_URL",
                "MEDAGENT_LLM_API_KEY",
                "MEDAGENT_LLM_MODEL",
            )
        }
        self._process_lock = Lock()
        self._children = set()
        self._closed = False
        self.bridge = self.root / "integrations" / "dsh" / "bridge.mjs"
        if not self.bridge.exists():
            raise RuntimeError("DSH bridge missing; run from the project checkout")
        if not (
            self.root / "node_modules/@deepseek-ai/dsh-sdk-client/package.json"
        ).exists():
            raise RuntimeError(
                "DSH dependencies missing; run npm ci --ignore-scripts in the project checkout"
            )

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        self.events.append(
            Event(
                session_id,
                "model.requested",
                {"backend": self.name, "messages": messages},
            )
        )
        started = perf_counter()
        # JSON is passed via stdin, never interpolated into a command or argv.
        with self._process_lock:
            if self._closed:
                raise RuntimeError("DSH backend is closed")
            child = subprocess.Popen(
                ["node", str(self.bridge)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.root,
                start_new_session=(os.name == "posix"),
                env={**os.environ, **self.config},
            )
            self._children.add(child)
        try:
            stdout, _ = child.communicate(
                json.dumps({"messages": messages}, ensure_ascii=False), timeout=55
            )
        except subprocess.TimeoutExpired:
            # Reap the bridge AND its DSH child on a stuck teardown.
            self._terminate(child)
            child.communicate()
            raise
        finally:
            with self._process_lock:
                self._children.discard(child)
        if child.returncode:
            raise RuntimeError(
                "DSH failed; run npm run dsh:check for installation diagnostics"
            )
        envelope = json.loads(stdout)
        if not isinstance(envelope.get("final_response"), str):
            raise ValueError("DSH did not return a committed response")
        self.events.append(
            Event(
                session_id,
                "model.responded",
                {
                    "backend": self.name,
                    "content": envelope["final_response"],
                    "runtime_events": envelope.get("events", []),
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "usage": dsh_usage(envelope.get("events", [])),
                },
            )
        )
        return parse_decision(envelope["final_response"])

    @staticmethod
    def _terminate(child):
        try:
            if os.name == "posix":
                os.killpg(child.pid, signal.SIGKILL)
            else:
                child.kill()
        except ProcessLookupError:
            pass

    def close(self):
        with self._process_lock:
            self._closed = True
            for child in self._children:
                self._terminate(child)


def dsh_usage(events):
    """DSH TokenUsage input/cache counts are disjoint, unlike prompt_tokens."""
    totals = {}
    for event in events:
        if event.get("type") != "assistant/message":
            continue
        usage = event.get("data", {}).get("usage")
        if not isinstance(usage, dict):
            continue
        values = {
            key: value
            for key, value in usage.items()
            if type(value) is int and value >= 0
        }
        if "inputTokens" not in values:
            continue
        hit = values.get("cacheReadTokens", 0)
        miss = values["inputTokens"] + values.get("cacheWriteTokens", 0)
        for key, value in {
            "prompt_tokens": hit + miss,
            "prompt_cache_hit_tokens": hit,
            "prompt_cache_miss_tokens": miss,
            "completion_tokens": values.get("outputTokens", 0),
            "total_tokens": hit + miss + values.get("outputTokens", 0),
        }.items():
            totals[key] = totals.get(key, 0) + value
    return totals
