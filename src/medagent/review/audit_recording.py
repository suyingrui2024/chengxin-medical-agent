"""Offline model I/O audit support: recording wrapper + token measurement.

This module is audit tooling only. It never runs in the production
request path unless a caller explicitly wraps a backend with
``RecordingBackend`` — recording is off by default (no environment
switch, no assembly wiring), so a production server never writes
prompt fixtures to disk.

Token honesty rules (design §2.2, §5):
- ``TokenCounter`` uses tiktoken ``cl100k_base`` when the package is
  installed. That is a *proxy* tokenizer, not DeepSeek's deployment
  tokenizer: proxy counts are reported as ``proxy_tokens`` and must
  never be presented as deployment token counts.
- The deployment token counts are the ``usage`` fields the provider
  returns on live calls (``CompatibleBackend`` already records them in
  ``model.responded`` events); offline fixtures cannot supply them, so
  those columns are ``null`` ("待实测") in offline reports, never
  estimated from characters.
- When tiktoken is absent, ``TokenCounter`` reports characters only and
  ``tokens`` is ``None`` — characters are never converted into tokens
  by a hidden ratio.
"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional


class TokenCounter:
    """Count tokens with a proxy tokenizer, or characters only.

    ``method`` is one of ``"tiktoken_cl100k_proxy"`` or
    ``"chars_only"``; callers must surface it next to any number they
    publish.
    """

    def __init__(self, encoding_name: str = "cl100k_base") -> None:
        self.encoding_name = encoding_name
        self._encoding = None
        try:
            import tiktoken  # type: ignore

            self._encoding = tiktoken.get_encoding(encoding_name)
            self.method = "tiktoken_cl100k_proxy"
        except Exception:
            self.method = "chars_only"

    def count(self, text: str) -> Dict[str, Any]:
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=False)
        result: Dict[str, Any] = {"chars": len(text), "method": self.method}
        result["tokens"] = (
            len(self._encoding.encode(text)) if self._encoding is not None else None
        )
        return result


def _content_text(message: Dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False)


def measure_messages(
    messages: List[Dict[str, Any]], counter: Optional[TokenCounter] = None
) -> Dict[str, Any]:
    """Per-message and total size of one model call's input."""
    counter = counter or TokenCounter()
    per_message = []
    total_chars = 0
    total_tokens: Optional[int] = 0
    for message in messages:
        text = _content_text(message)
        counted = counter.count(text)
        per_message.append(
            {
                "role": message.get("role"),
                "chars": counted["chars"],
                "tokens": counted["tokens"],
            }
        )
        total_chars += counted["chars"]
        if counted["tokens"] is None:
            total_tokens = None
        elif total_tokens is not None:
            total_tokens += counted["tokens"]
    return {
        "message_count": len(messages),
        "per_message": per_message,
        "total_chars": total_chars,
        "total_proxy_tokens": total_tokens,
        "token_method": counter.method,
        "deployment_tokens": None,  # only a live provider response can fill this
    }


class RecordingBackend:
    """Wrap a DecisionBackend and record every decide() input.

    The inner backend is untouched: decisions, retries and errors
    behave exactly as before. Each record keeps the messages verbatim
    (needed to rebuild fixtures) plus timing and a result summary
    (tool name + argument keys only — argument values are already in
    the recorded messages, so they are not duplicated here).
    Records are kept in memory and, when ``directory`` is given, also
    written one JSON file per call: ``{call_site}_{session_id}_{seq}.json``.
    ``directory`` must be supplied explicitly by audit tooling; there
    is no default path and no production wiring.
    """

    name = "recording"

    def __init__(
        self,
        inner: Any,
        *,
        call_site: str,
        directory: Optional[str] = None,
    ) -> None:
        self.inner = inner
        self.call_site = call_site
        self.directory = Path(directory) if directory else None
        if self.directory is not None:
            self.directory.mkdir(parents=True, exist_ok=True)
        self.records: List[Dict[str, Any]] = []
        self._seq = 0
        # Preserve the wrapped backend's name for event/log readers.
        self.wrapped_name = getattr(inner, "name", None)

    def _record(self, session_id: str, messages, started: float, result, error):
        self._seq += 1
        record = {
            "call_site": self.call_site,
            "session_id": session_id,
            "seq": self._seq,
            "messages": deepcopy(messages),
            "duration_ms": round((time.perf_counter() - started) * 1000),
            "result_tool": None,
            "result_argument_keys": None,
            "error": repr(error) if error is not None else None,
        }
        if isinstance(result, dict):
            record["result_tool"] = result.get("tool")
            arguments = result.get("arguments")
            if isinstance(arguments, dict):
                record["result_argument_keys"] = sorted(arguments.keys())
        self.records.append(record)
        if self.directory is not None:
            path = self.directory / (
                f"{self.call_site}_{session_id}_{self._seq:04d}.json"
            )
            path.write_text(
                json.dumps(record, ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        return record

    def decide(self, session_id: str, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        started = time.perf_counter()
        try:
            result = self.inner.decide(session_id, messages)
        except Exception as exc:  # noqa: BLE001 - record then re-raise unchanged
            self._record(session_id, messages, started, None, exc)
            raise
        self._record(session_id, messages, started, result, None)
        return result

    def decide_cancellable(self, session_id, messages, cancelled):
        inner = getattr(self.inner, "decide_cancellable", None)
        if inner is None:
            return self.decide(session_id, messages)
        started = time.perf_counter()
        try:
            result = inner(session_id, messages, cancelled)
        except Exception as exc:  # noqa: BLE001 - record then re-raise unchanged
            self._record(session_id, messages, started, None, exc)
            raise
        self._record(session_id, messages, started, result, None)
        return result


def summarize_records(
    records: List[Dict[str, Any]], counter: Optional[TokenCounter] = None
) -> Dict[str, Any]:
    """Aggregate one call site's records into a metrics table row set."""
    counter = counter or TokenCounter()
    rows = []
    for record in records:
        measured = measure_messages(record["messages"], counter)
        rows.append(
            {
                "call_site": record["call_site"],
                "session_id": record["session_id"],
                "seq": record["seq"],
                "duration_ms": record["duration_ms"],
                "result_tool": record["result_tool"],
                "error": record["error"],
                **measured,
            }
        )
    return {"records": rows, "count": len(rows)}
