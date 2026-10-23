"""Replaceable decision transports. No transport can mutate the clinical state."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import signal
from http.client import HTTPException
from threading import Lock
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol
from urllib import request
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from time import perf_counter, monotonic, sleep
from tempfile import TemporaryDirectory

from .events import Event, EventStore
from .decision_wait import DecisionInterrupted


class DecisionBackend(Protocol):
    name: str

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        ...


def request_event_payload(
    backend: str, model: Optional[str], messages: List[Dict[str, str]]
) -> Dict[str, Any]:
    """Metadata-only record of an outgoing model request.

    The full prompt used to be persisted verbatim into the event log —
    the static prefix plus the whole case context, duplicated on every
    turn and kept forever. No consumer reads the text back: the
    evaluations only count model.requested events, the subagent token
    ledger reads model.responded usage, and session recovery replays
    turn/fact events (older logs with the text still read fine — the
    readers never assumed a payload shape). What reconciliation
    actually needs is kept: backend/model, message count, per-message
    and total character counts, and a sha256 of the canonical message
    JSON so an externally captured request can be matched against the
    event without storing its content.
    """
    canonical = json.dumps(
        messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    message_chars = []
    for message in messages:
        content = message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        message_chars.append(len(content))
    return {
        "backend": backend,
        "model": model,
        "message_count": len(messages),
        "message_chars": message_chars,
        "total_chars": sum(message_chars),
        "content_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }


def _transient_retry_reason(exc: BaseException) -> Optional[str]:
    """Classify a transport failure for the one bounded retry.

    HTTP 429 and 5xx are the provider saying "not now"; timeouts and
    connection failures are the network saying the same. Other HTTP
    errors (4xx) are deterministic — retrying them would fail twice —
    and anything else is a programming or payload error, not a
    transient.
    """
    if isinstance(exc, HTTPError):
        if exc.code == 429:
            return "http_429"
        if 500 <= exc.code < 600:
            return "http_5xx"
        return None
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, (URLError, ConnectionError, HTTPException)):
        return "network_error"
    return None


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
        # V4-generation models grade thinking as low/high/max
        # (official guidance: simple tasks low, everyday agent work
        # high, hardest cases max). Unset = do not send the parameter.
        self.reasoning_effort = settings.get("MEDAGENT_LLM_REASONING_EFFORT") or None
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "low",
            "high",
            "max",
        }:
            raise ValueError("reasoning effort must be low, high or max")
        # Backoff before the one bounded transient retry (see _decide);
        # configurable so tests and latency-sensitive deployments can
        # shrink it to zero.
        self.retry_backoff_seconds = float(
            settings.get("MEDAGENT_LLM_RETRY_BACKOFF_SECONDS", "0.5")
        )
        if not 0 <= self.retry_backoff_seconds <= 10:
            raise ValueError("retry backoff must be between 0 and 10 seconds")

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        return self._decide(session_id, messages)

    def decide_cancellable(self, session_id, messages, cancelled):
        return self._decide(session_id, messages, cancelled)

    def _decide(self, session_id, messages, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "max_tokens": self.max_tokens,
        }
        if urlparse(self.base_url).hostname == "api.deepseek.com":
            payload["thinking"] = {"type": self.thinking}
            if self.thinking == "enabled" and self.reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
        self.events.append(
            Event(
                session_id,
                "model.requested",
                request_event_payload(self.name, self.model, messages),
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
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
        # Two known transients get one bounded retry (two attempts
        # total): the provider's JSON mode occasionally returns an
        # empty content (a documented quirk), and the transport itself
        # can fail with HTTP 429/5xx or a network timeout/connection
        # error. Other HTTP errors are deterministic and surface
        # immediately instead of failing twice.
        result = None
        raw = ""
        for attempt in (1, 2):
            try:
                with request.urlopen(req, timeout=45) as response:
                    result = json.loads(response.read(256000).decode())
            except Exception as exc:
                reason = _transient_retry_reason(exc) if attempt == 1 else None
                if reason is None:
                    raise
                self.events.append(
                    Event(
                        session_id,
                        "model.retry",
                        {"backend": self.name, "reason": reason},
                    )
                )
                if cancelled is not None and cancelled.is_set():
                    raise DecisionInterrupted()
                if self.retry_backoff_seconds > 0:
                    sleep(self.retry_backoff_seconds)
                continue
            raw = result["choices"][0]["message"]["content"] or ""
            if raw.strip():
                break
            if attempt == 1:
                self.events.append(
                    Event(
                        session_id,
                        "model.retry",
                        {"backend": self.name, "reason": "empty_content"},
                    )
                )
                if cancelled is not None and cancelled.is_set():
                    raise DecisionInterrupted()
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
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
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
        # Generation settings ride the stdin envelope to the bridge;
        # until now the bridge hardcoded maxTokens 2048 and
        # reasoningEffort 'off', silently ignoring the deployment's
        # MEDAGENT_LLM_MAX_TOKENS / MEDAGENT_LLM_THINKING entirely.
        self.max_tokens = int(settings.get("MEDAGENT_LLM_MAX_TOKENS", "2048"))
        if not 128 <= self.max_tokens <= 8192:
            raise ValueError("max output tokens must be between 128 and 8192")
        self.thinking = settings.get("MEDAGENT_LLM_THINKING", "disabled")
        if self.thinking not in {"enabled", "disabled"}:
            raise ValueError("thinking must be enabled or disabled")
        self.reasoning_effort = settings.get("MEDAGENT_LLM_REASONING_EFFORT") or None
        if self.reasoning_effort is not None and self.reasoning_effort not in {
            "low",
            "high",
            "max",
        }:
            raise ValueError("reasoning effort must be low, high or max")
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
        return self._decide(session_id, messages)

    def decide_cancellable(self, session_id, messages, cancelled):
        return self._decide(session_id, messages, cancelled)

    def _decide(self, session_id, messages, cancelled=None):
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
        self.events.append(
            Event(
                session_id,
                "model.requested",
                request_event_payload(
                    self.name, self.config["MEDAGENT_LLM_MODEL"], messages
                ),
            )
        )
        started = perf_counter()
        # The parent owns the disposable root even if the bridge is forcibly
        # interrupted before its Node finally block can delete session logs.
        with TemporaryDirectory(prefix="medical-decision-") as scratch:
            return self._bridge_decision(
                session_id, messages, cancelled, started, scratch
            )

    def _bridge_decision(self, session_id, messages, cancelled, started, scratch):
        # JSON is passed via stdin, never interpolated into a command or argv.
        with self._process_lock:
            if self._closed:
                raise RuntimeError("DSH backend is closed")
            if cancelled is not None and cancelled.is_set():
                raise DecisionInterrupted()
            from .processes import popen
            child = popen(
                ["node", str(self.bridge)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.root,
                env={
                    **os.environ,
                    **self.config,
                    "TMPDIR": scratch,
                    "TMP": scratch,
                    "TEMP": scratch,
                },
            )
            self._children.add(child)
        try:
            data = json.dumps(
                {
                    "messages": messages,
                    "max_tokens": self.max_tokens,
                    "thinking": self.thinking,
                    "reasoning_effort": self.reasoning_effort,
                },
                ensure_ascii=False,
            )
            if cancelled is None:
                stdout, _ = child.communicate(data, timeout=55)
            else:
                deadline = monotonic() + 55
                while True:
                    if cancelled.is_set():
                        self._terminate(child)
                        child.communicate()
                        raise DecisionInterrupted()
                    try:
                        stdout, _ = child.communicate(
                            data, timeout=min(0.2, max(0.001, deadline - monotonic()))
                        )
                        break
                    except subprocess.TimeoutExpired:
                        data = None
                        if monotonic() >= deadline:
                            raise
        except subprocess.TimeoutExpired:
            # Reap the bridge AND its DSH child on a stuck teardown.
            self._terminate(child)
            child.communicate()
            raise
        finally:
            from .processes import release
            release(child)
            with self._process_lock:
                self._children.discard(child)
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
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
            from .processes import terminate
            terminate(child)
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
