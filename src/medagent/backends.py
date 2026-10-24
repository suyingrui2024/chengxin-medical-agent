"""Replaceable decision transports. No transport can mutate the clinical state."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
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


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def require_https_or_loopback_endpoint(base_url: str) -> None:
    """Fail closed on a plaintext model endpoint.

    Bearer keys must not travel over http to a non-local host, where any
    hop can read them (and where a 302 could carry them further). https
    is always allowed; http is allowed only for a loopback stub
    (localhost / 127.0.0.1 / ::1), the channel the offline test servers
    in this repo use. Anything else — missing scheme, non-loopback http,
    a bare hostname — raises instead of sending.
    """
    parsed = urlparse(base_url)
    # E-P2-6: a query string or embedded userinfo on the base URL
    # corrupts the "/chat/completions" join (the query would be
    # carried or dropped unpredictably) and lets URL credentials sit
    # next to the Bearer key. Reject both fail-fast, for every scheme
    # and host — including loopback stubs, which never need them.
    if (
        "?" in base_url.split("#", 1)[0]
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(
            "model endpoint must not carry a query string or embedded credentials"
        )
    scheme = (parsed.scheme or "").lower()
    if scheme == "https" and parsed.hostname:
        return
    if scheme == "http" and (parsed.hostname or "").lower() in _LOOPBACK_HOSTS:
        return
    raise ValueError(
        "model endpoint must be HTTPS, except http loopback "
        "(localhost/127.0.0.1/::1) for local test stubs"
    )


class _NoRedirectHandler(request.HTTPRedirectHandler):
    """Never follow a redirect from the model endpoint.

    The default urllib opener follows 301/302/303 and re-sends the
    request — including its Authorization header — to whatever host the
    Location header names, so a compromised or malicious responder can
    exfiltrate the API key with one redirect. API endpoints do not
    legitimately redirect a chat-completions POST, so every redirect
    code (301/302/303/307/308) surfaces as an explicit HTTPError
    instead: no silent success, no silent key loss, no retry (3xx is
    not transient). Mirrors web_tools._NoRedirectHandler.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102
        return None


def _open_request(req, timeout):
    """The single network exit for CompatibleBackend.

    A per-call opener (rather than request.urlopen's shared default
    opener) so the no-redirect handler above cannot be bypassed and
    cannot leak into other urllib callers.
    """
    return request.build_opener(_NoRedirectHandler()).open(req, timeout=timeout)


def parse_decision(raw: str) -> Dict[str, Any]:
    if not isinstance(raw, str) or len(raw) > 64000:
        raise ValueError("invalid decision size")
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or set(parsed) != {"tool", "arguments"}:
        raise ValueError("decision must contain only tool and arguments")
    if not isinstance(parsed["tool"], str) or not isinstance(parsed["arguments"], dict):
        raise ValueError("invalid decision envelope")
    return parsed


# E-P2-2: total-length rejection, not a read window. _decide consumes
# the whole body in one read (there is no streaming consumer), so
# reading one byte past the limit detects overflow at bounded cost
# (256001 bytes, never the full body). A body longer than the limit
# therefore fails with this distinct ValueError even when its first
# 256000 bytes happen to parse (e.g. valid JSON plus trailing
# whitespace) — previously such a body silently succeeded.
_RESPONSE_SIZE_LIMIT = 256000


def _extract_compatible_response(result: Any):
    """Validate a Compatible HTTP envelope, raising ValueError only.

    E-P2-1: before this helper the extraction below raised whatever
    the shape happened to trigger — TypeError for a top-level list,
    KeyError for a missing choices/message key, IndexError for empty
    choices, AttributeError for a string usage or message — so a
    caller catching ValueError (the repo's validation idiom, as in
    parse_decision) mistook malformed envelopes for unexpected
    crashes. Every malformed shape here is a ValueError with a
    specific message; the whitelist usage filter itself is unchanged.
    Returns (content, usage_metrics, model, finish_reason).
    """
    if not isinstance(result, dict):
        raise ValueError("model response must be a JSON object")
    choices = result.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("model response must contain a non-empty choices list")
    first = choices[0]
    if not isinstance(first, dict):
        raise ValueError("model response choice must be an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise ValueError("model response choice must contain a message object")
    content = message.get("content")
    if content is None:
        content = ""
    if not isinstance(content, str):
        raise ValueError("model response content must be a string")
    usage = result.get("usage") or {}
    if not isinstance(usage, dict):
        raise ValueError("model response usage must be an object")
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
    return content, metrics, result.get("model"), first.get("finish_reason")


class CompatibleBackend:
    name = "compatible"

    def __init__(self, events: EventStore, config: Dict[str, str] = None) -> None:
        self.events = events
        settings = config if config is not None else os.environ
        self.base_url = settings["MEDAGENT_LLM_BASE_URL"].rstrip("/")
        require_https_or_loopback_endpoint(self.base_url)
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
        # E-P2-3: thinking/reasoning_effort are deepseek-only payload
        # fields (see _decide). On any other host they are dropped —
        # which is the correct wire behaviour for an incompatible
        # provider — but dropping them silently made operators believe
        # thinking was enabled. Chosen handling is explicit-ignore
        # with a log trace, not construction rejection: rejecting
        # would break deployments that share one config across
        # providers, and the request itself still succeeds. The log
        # fires once at construction, where the config is known, and
        # the send decision below uses the same flag so the two
        # cannot disagree.
        self._is_deepseek_host = (
            urlparse(self.base_url).hostname == "api.deepseek.com"
        )
        if not self._is_deepseek_host and (
            self.thinking == "enabled" or self.reasoning_effort is not None
        ):
            logging.getLogger(__name__).warning(
                "thinking/reasoning_effort configured but not sent: "
                "these parameters are only sent to api.deepseek.com "
                "(thinking=%s, reasoning_effort=%s, host=%s)",
                self.thinking,
                self.reasoning_effort,
                urlparse(self.base_url).hostname,
            )
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
        if self._is_deepseek_host:
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
        metrics: Dict[str, int] = {}
        model_name = None
        finish_reason = None
        for attempt in (1, 2):
            try:
                with _open_request(req, timeout=45) as response:
                    body = response.read(_RESPONSE_SIZE_LIMIT + 1)
                    if len(body) > _RESPONSE_SIZE_LIMIT:
                        raise ValueError(
                            "model response exceeds 256000 bytes"
                        )
                    result = json.loads(body.decode())
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
            raw, metrics, model_name, finish_reason = _extract_compatible_response(
                result
            )
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
        # E-P2-4: validate before recording responded. Previously
        # model.responded was emitted here first and parse_decision
        # ran after it, so an invalid decision (extra key, empty
        # content twice) still counted as a responded turn. Now a
        # validation failure raises with only model.requested (and
        # any model.retry) on the log; responded means the decision
        # passed validation. The cancelled check keeps its old place
        # after responded, so a valid-but-interrupted turn still
        # records responded exactly as before.
        decision = parse_decision(raw)
        self.events.append(
            Event(
                session_id,
                "model.responded",
                {
                    "backend": self.name,
                    "content": raw,
                    "usage": metrics,
                    "duration_ms": round((perf_counter() - started) * 1000),
                    "model": model_name,
                    "finish_reason": finish_reason,
                },
            )
        )
        if cancelled is not None and cancelled.is_set():
            raise DecisionInterrupted()
        return decision


# Python -> bridge environment whitelist. Mirrors the bridge -> DSH
# grandchild whitelist in integrations/dsh/bridge.mjs launchOptions
# (PATH/SystemRoot/WINDIR/LANG, with TMPDIR/TMP/TEMP overridden to
# the per-decision scratch dir below), plus the three MEDAGENT_* keys
# the bridge main() reads. Nothing else from the parent process
# environment reaches the bridge child: previously the whole parent
# env (including unrelated secrets) was inherited. PATH is required:
# without it neither `node` nor the DSH child can be located, and a
# missing PATH must fail here with a named error rather than surface
# later as an obscure spawn failure.
BRIDGE_ENV_PASSTHROUGH = ("PATH", "SystemRoot", "WINDIR", "LANG")
# Bridge stdout cap, in characters, mirroring the Compatible backend's
# 256000 read limit. Oversize output fails closed (ValueError) instead
# of being silently truncated or parsed unbounded.
BRIDGE_STDOUT_LIMIT_CHARS = 256000
_BRIDGE_DIAGNOSTIC_RE = re.compile(
    r"^DSH bridge failed \(([A-Za-z0-9_.$: -]{1,120})\)$"
)


def bridge_env(config, scratch):
    """Build the bridge child environment from the whitelist."""
    env = {
        key: os.environ[key]
        for key in BRIDGE_ENV_PASSTHROUGH
        if key in os.environ
    }
    if "PATH" not in env:
        raise RuntimeError(
            "DSH bridge requires PATH in the parent environment; "
            "refusing to spawn the bridge without it"
        )
    env.update(config)
    env.update({"TMPDIR": scratch, "TMP": scratch, "TEMP": scratch})
    return env


def bridge_stderr_summary(stderr):
    """Extract the bridge's safe diagnostic line, or a placeholder.

    The bridge writes exactly one line, `DSH bridge failed (<Class>[:
    <code>])`, and never echoes messages, prompts or patient data.
    Only a first line matching that shape is retained; anything else
    (including any further line) is replaced by a placeholder, so a
    misbehaving bridge cannot smuggle content into errors or logs
    through stderr. Returns "" when the bridge wrote nothing.
    """
    if not stderr:
        return ""
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", "replace")
    first = stderr.splitlines()[0].strip() if stderr else ""
    if not first:
        return ""
    match = _BRIDGE_DIAGNOSTIC_RE.match(first)
    if match is None:
        return "bridge diagnostic not in expected format"
    return match.group(1)


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
        # Same plaintext-endpoint rule as CompatibleBackend: the bridge
        # inherits this URL, so a direct DshBackend construction must
        # not be the hole that CompatibleBackend's check closes.
        require_https_or_loopback_endpoint(
            self.config["MEDAGENT_LLM_BASE_URL"].rstrip("/")
        )
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
                env=bridge_env(self.config, scratch),
            )
            self._children.add(child)
        stderr = ""
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
                stdout, stderr = child.communicate(data, timeout=55)
            else:
                deadline = monotonic() + 55
                while True:
                    if cancelled.is_set():
                        self._terminate(child)
                        child.communicate()
                        raise DecisionInterrupted()
                    try:
                        stdout, stderr = child.communicate(
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
            summary = bridge_stderr_summary(stderr)
            detail = f"; bridge diagnostic: {summary}" if summary else ""
            logging.getLogger(__name__).warning("DSH bridge failed%s", detail)
            raise RuntimeError(
                "DSH failed" + detail
                + "; run npm run dsh:check for installation diagnostics"
            )
        if len(stdout) > BRIDGE_STDOUT_LIMIT_CHARS:
            raise ValueError(
                "DSH bridge output exceeds 256000 characters; "
                "refusing to parse unbounded bridge output"
            )
        envelope = json.loads(stdout)
        if not isinstance(envelope, dict):
            raise ValueError("DSH envelope must be an object")
        if not isinstance(envelope.get("final_response"), str):
            raise ValueError("DSH did not return a committed response")
        # events is auxiliary (usage + diagnostics only): a malformed
        # field must never drag down an otherwise valid proposal.
        # Sanitize it, keep the valid proposal, and leave a trace of
        # every dropped element on the model.responded event itself
        # (plus a log line) instead of silently swallowing it. The
        # decision_wait generic-Exception mitigation chain is untouched:
        # a genuinely bad envelope/proposal still raises ValueError
        # above and surfaces as a round error, never a crash or a
        # wrong fact.
        runtime_events, dropped_events = sanitize_dsh_events(
            envelope.get("events", [])
        )
        responded_payload = {
            "backend": self.name,
            "content": envelope["final_response"],
            "runtime_events": runtime_events,
            "duration_ms": round((perf_counter() - started) * 1000),
            "usage": dsh_usage(runtime_events),
        }
        if dropped_events:
            responded_payload["dropped_runtime_events"] = dropped_events
            logging.getLogger(__name__).warning(
                "DSH dropped %d malformed runtime event(s): %s",
                len(dropped_events),
                dropped_events,
            )
        self.events.append(
            Event(
                session_id,
                "model.responded",
                responded_payload,
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


def sanitize_dsh_events(raw):
    """Split a DSH ``events`` field into usable events and a drop trace.

    Returns ``(events, dropped)`` where ``events`` is always a list of
    dicts and ``dropped`` lists one trace entry per discarded item:
    ``{"index": ..., "reason": ..., "type": ...}``. Trace entries carry
    only the position, the reason and the Python type name — never the
    raw value, which could be large or carry SDK internals. A
    non-list field drops as a whole (index ``None``); a dict element
    whose ``data`` is present but not an object is dropped too, because
    its usage payload is unreadable and no consumer may assume a shape
    for it. Missing ``events`` is not a drop: callers pass the default
    ``[]`` for that case.
    """
    if not isinstance(raw, list):
        return [], [
            {"index": None, "reason": "events_not_list", "type": type(raw).__name__}
        ]
    events = []
    dropped = []
    for index, event in enumerate(raw):
        if not isinstance(event, dict):
            dropped.append(
                {
                    "index": index,
                    "reason": "event_not_object",
                    "type": type(event).__name__,
                }
            )
            continue
        if "data" in event and not isinstance(event["data"], dict):
            dropped.append(
                {
                    "index": index,
                    "reason": "event_data_not_object",
                    "type": type(event["data"]).__name__,
                }
            )
            continue
        events.append(event)
    return events, dropped


def dsh_usage(events):
    """DSH TokenUsage input/cache counts are disjoint, unlike prompt_tokens.

    ``events`` must be a list (anything else is a caller contract
    violation and raises ValueError, matching parse_decision). Inside
    the list, though, a malformed element is auxiliary usage data, not
    a reason to lose the proposal: non-object elements and elements
    whose ``data`` is not an object are skipped, exactly as a
    non-dict ``usage`` already was. Callers that need an audit trail
    for the skipped elements run sanitize_dsh_events first — the
    backend does so before calling this.
    """
    if not isinstance(events, list):
        raise ValueError("dsh events must be a list")
    totals = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != "assistant/message":
            continue
        data = event.get("data", {})
        if not isinstance(data, dict):
            continue
        usage = data.get("usage")
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
