"""Mixin: core routes for Handler."""
from ._preamble import *  # noqa: F401,F403
from .types import *  # noqa: F401,F403


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
        try:
            self._do_POST()
        except OSError:
            self._io_error()

    def do_DELETE(self) -> None:
        try:
            self._do_DELETE()
        except OSError:
            self._io_error()

    def do_PATCH(self) -> None:
        try:
            self._do_PATCH()
        except OSError:
            self._io_error()

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
            self._error(HTTPStatus.BAD_REQUEST, "invalid_json", "请求不是有效 JSON")
            return None
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

    def _static(self, path: str) -> None:
        filename = "index.html" if path == "/" else path.removeprefix("/")
        if "/" in filename or filename.startswith("."):
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        resource = resources.files("medagent.web").joinpath(filename)
        if not resource.is_file():
            self._error(HTTPStatus.NOT_FOUND, "not_found", "文件不存在")
            return
        body = resource.read_bytes()
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header(
            "Content-Type",
            f"{mime}; charset=utf-8"
            if mime.startswith("text/") or mime == "application/javascript"
            else mime,
        )
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'",
        )
        self.end_headers()
        self.wfile.write(body)
