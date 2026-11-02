"""Mixin: core routes for Handler."""
from http import HTTPStatus
from importlib import resources
import json
import mimetypes
from pathlib import Path
import re
import secrets
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from uuid import uuid4
from ._preamble import MAX_BODY_BYTES
from .types import MedicalAgentServer

#: Explicit types for the extensions the page actually ships.
#:
#: ``mimetypes`` reads a platform database, and that database disagrees
#: across systems: Windows consults the registry and reports ``text/plain``
#: for ``.js`` on a machine where the key is missing or has been edited. A
#: wrong type is not a cosmetic problem here, because every response carries
#: ``X-Content-Type-Options: nosniff`` — the browser then refuses the
#: stylesheet outright. The files are known up front, so the answer should
#: not depend on the host's registry.
_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".txt": "text/plain; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".otf": "font/otf",
    ".woff2": "font/woff2",
}

#: Segments a static request may not contain. ``..`` and ``.`` would walk out
#: of the package, and a leading dot reaches ``__pycache__`` and any dotfile
#: that ends up beside the assets.
_FORBIDDEN_SEGMENTS = {"", ".", ".."}

#: Suffixes the handler refuses even when the file is really there. The web
#: package is a Python package, so it carries its own ``__init__.py``; serving
#: it would work, which is exactly why it needs an explicit answer.
_FORBIDDEN_SUFFIXES = {".py", ".pyc", ".pyo"}


class HandlerCore:
    @property
    def app(self) -> MedicalAgentServer:
        return self.server  # type: ignore[return-value]

    def parse_request(self) -> bool:
        if not super().parse_request():
            return False
        token = self.app.access_token
        if token is not None and not secrets.compare_digest(
            self.headers.get("X-MedAgent-Token", "").encode("utf-8"),
            token.encode("utf-8"),
        ):
            self._error(HTTPStatus.UNAUTHORIZED, "desktop_only", "此服务只接受当前桌面应用的请求")
            return False
        candidate = self.headers.get("X-Request-ID", "")
        self.request_id = (
            candidate
            if re.fullmatch(r"[A-Za-z0-9._-]{1,128}", candidate)
            else str(uuid4())
        )
        return True

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self) -> None:
        try:
            self._do_GET()
        except OSError:
            self._io_error()

    def do_POST(self) -> None:
        self._body_remaining = self._body_length()
        try:
            self._do_POST()
        except OSError:
            self._io_error()
        finally:
            self._drain_body()

    def do_DELETE(self) -> None:
        self._body_remaining = self._body_length()
        try:
            self._do_DELETE()
        except OSError:
            self._io_error()
        finally:
            self._drain_body()

    def do_PATCH(self) -> None:
        self._body_remaining = self._body_length()
        try:
            self._do_PATCH()
        except OSError:
            self._io_error()
        finally:
            self._drain_body()

    def _body_length(self) -> int:
        try:
            return max(0, int(self.headers.get("Content-Length", "0")))
        except (TypeError, ValueError):
            return 0

    def _drain_body(self) -> None:
        """Consume whatever is left of the request body before closing.

        A route that answers without parsing its body — ``POST /api/sessions``
        is one, and the browser sends ``{}`` with it — leaves those bytes in
        the socket. Closing a socket that still has unread data makes the
        kernel send RST instead of FIN, and a client that has not finished
        reading the response loses it: ``ConnectionResetError`` on the client,
        an intermittently failed 「开始问诊」 in the browser.

        Loopback on an idle machine usually delivers the response before the
        RST, which is why this only ever surfaced as a flaky CI failure.
        Reading the remainder costs two bytes here and makes the close clean.
        """
        remaining = getattr(self, "_body_remaining", 0)
        if remaining <= 0:
            return
        if remaining > MAX_BODY_BYTES:
            # A rejected oversized body is not worth reading out; the reset
            # that follows is the correct answer to it.
            return
        try:
            while remaining > 0:
                chunk = self.rfile.read(min(remaining, 65536))
                if not chunk:
                    break
                remaining -= len(chunk)
        except OSError:
            pass
        self._body_remaining = 0

    def _io_error(self) -> None:
        try:
            self._error(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                "server_io_error",
                "服务器读写文件时出错，请稍后重试",
            )
        except OSError:
            # The connection itself is the casualty; nothing more can
            # be delivered on it.
            pass

    def _persistence_health(self) -> Dict[str, int]:
        """Durable-layer health for /api/health, read defensively: the
        counters live on the event store and the engines, and a missing
        counter reads as zero rather than breaking the health check."""
        events = self.app.events
        session_failures = 0
        for engine in getattr(self.app.workspace, "engines", []):
            value = getattr(engine, "_persist_failures", 0)
            if isinstance(value, dict):
                session_failures += sum(
                    int(item)
                    for item in value.values()
                    if isinstance(item, (int, float))
                )
            elif isinstance(value, (int, float)):
                session_failures += int(value)
        return {
            "event_append_failures": int(
                getattr(events, "append_failures", 0) or 0
            ),
            "event_skipped_corrupt": int(
                getattr(events, "skipped_corrupt", 0) or 0
            ),
            "session_persist_failures": session_failures,
        }

    def _json(
        self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Request-ID", getattr(self, "request_id", str(uuid4())))
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _read_json(self, max_bytes: int = MAX_BODY_BYTES) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_length", "请求长度无效")
            return None
        if length <= 0 or length > max_bytes:
            self._error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_size", "请求内容过大或为空")
            return None
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # The bytes are gone from the socket either way; a body that
            # failed to parse must not leave the drain waiting for them.
            self._body_remaining = 0
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "请求不是有效 JSON")
            return None
        self._body_remaining = 0
        if not isinstance(payload, dict):
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "请求必须是 JSON 对象")
            return None
        return payload

    def _same_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
            return False
        return True

    @staticmethod
    def _route(parts: list[str], *pattern: Optional[str]) -> bool:
        """True when the path segments match ``pattern`` exactly.

        ``parts`` is ``path.strip("/").split("/")``. ``None`` in the pattern
        matches any one segment, which is how route ids are skipped. The
        length is part of the match, so ``/api/sessions/x`` can never satisfy
        a four-segment pattern by prefix accident.
        """
        if len(parts) != len(pattern):
            return False
        return all(
            expected is None or expected == actual
            for expected, actual in zip(pattern, parts)
        )

    def _static(self, path: str) -> None:
        """Serve one file from the ``medagent.web`` package.

        Nested paths are allowed: the page keeps its scripts, stylesheets and
        media in separate directories, and a flat handler forced all of them
        into one folder of 55 siblings. Traversal is refused segment by
        segment rather than by string prefix, so ``/css/../core.py`` and
        ``/%2e%2e/core.py`` both fail — the second one stays percent-encoded,
        matches no directory, and 404s like any other missing name.
        """
        relative = "index.html" if path == "/" else path.removeprefix("/")
        segments = relative.split("/")
        if any(
            segment in _FORBIDDEN_SEGMENTS
            or segment.startswith(".")
            or "\\" in segment
            or "\x00" in segment
            for segment in segments
        ) or Path(segments[-1]).suffix.lower() in _FORBIDDEN_SUFFIXES:
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        node = resources.files("medagent.web")
        for segment in segments:
            node = node.joinpath(segment)
        if not node.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        suffix = Path(segments[-1]).suffix.lower()
        mime = _STATIC_TYPES.get(suffix) or mimetypes.guess_type(relative)[0] or (
            "application/octet-stream"
        )
        body = node.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)
