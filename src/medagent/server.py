from __future__ import annotations

import argparse
import json
import mimetypes
import secrets
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse, parse_qs

from .consultation import (
    ConsultationEngine,
    StaleActionError,
    UserInput,
    ConsultationBusyError,
)
from .events import EventStore
from .background_review import BackgroundReviewCoordinator
from .runtime import build_runtime
from .workspace import Workspace, StaleCompositionError
from . import __version__
from .execution import ExecutionManager, ExecutionError, ExecutionBusy
from .execution_files import DirectoryChanged


MAX_BODY_BYTES = 64 * 1024


class MedicalAgentServer(ThreadingHTTPServer):
    # A page loads fonts, CSS and an ES-module graph in parallel. Python 3.12's
    # default listen backlog of five can reset loopback connections during that
    # burst, leaving the module graph incomplete and the desktop uninitialized.
    request_queue_size = 64

    def __init__(
        self,
        address,
        handler,
        engine: ConsultationEngine,
        events: EventStore,
        backend_mode: str = "demo",
        access_token: Optional[str] = None,
    ):
        super().__init__(address, handler)
        self.engine = engine
        self.events = events
        self.backend_mode = backend_mode
        self.workspace = Workspace(engine, events, backend_mode)
        self.access_token = access_token
        self.execution = ExecutionManager()
        self.reviews = BackgroundReviewCoordinator(events)

    def server_close(self):
        self.reviews.close()
        self.execution.close()
        super().server_close()
        self.workspace.close()


class Handler(BaseHTTPRequestHandler):
    server_version = f"MedAgentPrototype/{__version__}"

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
        return True

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self._json(
                {
                    "status": "ok",
                    "service": "medagent",
                    "version": __version__,
                    "backend": self.app.workspace.backend,
                    "execution_available": self.app.access_token is not None,
                }
            )
            return
        if path == "/api/execution" or path.startswith("/api/execution/"):
            self._execution_get(path)
            return
        if path == "/api/settings":
            self._json(self.app.workspace.public_settings())
            return
        if path == "/api/capabilities":
            self._json(self.app.workspace.catalog())
            return
        if path == "/api/sessions":
            self._json({"sessions": self.app.workspace.sessions()})
            return
        if path == "/api/tasks":
            self._json(
                {
                    "tasks": list(self.app.workspace.engine.content.tasks.values()),
                    "versions": self.app.workspace.engine.content.versions,
                }
            )
            return
        if path.startswith("/api/sessions/"):
            parts = path.strip("/").split("/")
            try:
                engine = self.app.workspace.engine_for(parts[2])
                state = engine.state(parts[2])
            except (IndexError, KeyError):
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            if len(parts) == 4 and parts[3] == "events":
                self._json(
                    {
                        "events": [
                            event.to_dict()
                            for event in self.app.events.for_session(state.session_id)
                        ]
                    }
                )
            elif len(parts) == 4 and parts[3] == "snapshot":
                with engine._session_locks[state.session_id]:
                    self._json(
                        {
                            "state": state.to_dict(),
                            "events": [
                                event.to_dict()
                                for event in self.app.events.for_session(
                                    state.session_id
                                )
                            ],
                        }
                    )
            else:
                with engine._session_locks[state.session_id]:
                    self._json({"state": state.to_dict()})
            return
        self._static(path)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/execution" or path.startswith("/api/execution/"):
            self._execution_post(path)
            return
        if path == "/api/capabilities/configure" or path == "/api/capabilities/preview":
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "此操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                if path.endswith("/configure"):
                    result = self.app.workspace.reconfigure(payload)
                else:
                    if set(payload) != {
                        "id",
                        "input",
                        "expected_revision",
                    } or not isinstance(payload["id"], str):
                        raise ValueError("能力验证参数无效")
                    result = self.app.workspace.preview(
                        payload["id"], payload["input"], payload["expected_revision"]
                    )
            except StaleCompositionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_composition", str(exc))
                return
            except (ValueError, TypeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_capability", str(exc))
                return
            except Exception:
                self._error(
                    HTTPStatus.BAD_GATEWAY, "capability_failed", "能力执行未成功，未写入病例"
                )
                return
            self._json(result)
            return
        if path in {"/api/settings", "/api/settings/test"}:
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "配置操作只允许来自当前应用")
                return
            payload = self._read_json()
            if payload is None:
                return
            try:
                result = (
                    self.app.workspace.probe(payload)
                    if path.endswith("/test")
                    else self.app.workspace.apply(payload)
                )
            except (ValueError, RuntimeError) as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_settings", str(exc))
                return
            except Exception:
                self._error(
                    HTTPStatus.BAD_GATEWAY,
                    "connection_failed",
                    "连接未成功，请检查地址、协议、模型 ID 与密钥",
                )
                return
            self._json(result)
            return
        if path == "/api/sessions":
            with self.app.workspace.lock:
                state, reply = self.app.workspace.engine.create_session()
            self._json(
                {
                    "session_id": state.session_id,
                    "reply": reply.to_dict(),
                    "state": state.to_dict(),
                },
                HTTPStatus.CREATED,
            )
            return

        parts = path.strip("/").split("/")
        if (
            len(parts) == 4
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "messages"
        ):
            payload = self._read_json()
            if payload is None:
                return
            text = payload.get("text")
            choice_value = payload.get("choice_value")
            expected_action_id = payload.get("expected_action_id")
            calculation = payload.get("calculation")
            observation = payload.get("observation")
            if observation is not None and not isinstance(observation, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_observation", "定位答案格式错误")
                return
            if calculation is not None and not isinstance(calculation, dict):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_calculation", "计算参数格式错误")
                return
            if not isinstance(text, str) or not text.strip():
                self._error(HTTPStatus.BAD_REQUEST, "invalid_message", "消息不能为空")
                return
            if choice_value is not None and not isinstance(choice_value, str):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_choice", "选项值格式错误")
                return
            if expected_action_id is not None and not isinstance(
                expected_action_id, str
            ):
                self._error(HTTPStatus.BAD_REQUEST, "invalid_action_id", "问题标识格式错误")
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "new_submitted_message")
                    reply = engine.submit(
                        parts[2],
                        UserInput(
                            text=text,
                            choice_value=choice_value,
                            expected_action_id=expected_action_id,
                            calculation=calculation,
                            observation=observation,
                        ),
                    )
                    self.app.reviews.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_action", str(exc))
                return
            self._json(response)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if (
            len(parts) == 6
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "facts"
            and parts[5] == "restore"
        ):
            payload = self._read_json()
            if payload is None:
                return
            expected_fact_id = payload.get("expected_fact_id")
            if not isinstance(expected_fact_id, str) or not expected_fact_id:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_fact_version", "请从最新记录撤销更正"
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "fact_restored")
                    reply = engine.restore_fact(
                        parts[2], parts[4], expected_fact_id
                    )
                    self.app.reviews.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_fact", str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_restore", str(exc))
                return
            self._json(response)
            return
        if (
            len(parts) == 5
            and parts[0:2] == ["api", "sessions"]
            and parts[3] == "facts"
        ):
            payload = self._read_json()
            if payload is None:
                return
            text = payload.get("text")
            if not isinstance(text, str) or not text.strip():
                self._error(HTTPStatus.BAD_REQUEST, "invalid_correction", "更正内容不能为空")
                return
            expected_fact_id = payload.get("expected_fact_id")
            if not isinstance(expected_fact_id, str) or not expected_fact_id:
                self._error(
                    HTTPStatus.BAD_REQUEST, "invalid_fact_version", "请从最新记录打开更正操作"
                )
                return
            try:
                engine = self.app.workspace.engine_for(parts[2])
                with engine._session_locks[parts[2]]:
                    state = engine.state(parts[2])
                    self.app.reviews.invalidate(state, "fact_corrected")
                    reply = engine.correct_fact(
                        parts[2], parts[4], text.strip(), expected_fact_id
                    )
                    self.app.reviews.schedule(engine, state)
                    response = {
                        "reply": reply.to_dict(),
                        "state": engine.state(parts[2]).to_dict(),
                    }
            except KeyError:
                self._error(HTTPStatus.NOT_FOUND, "session_not_found", "找不到该问诊会话")
                return
            except ConsultationBusyError as exc:
                self._error(HTTPStatus.CONFLICT, "consultation_busy", str(exc))
                return
            except StaleActionError as exc:
                self._error(HTTPStatus.CONFLICT, "stale_fact", str(exc))
                return
            except ValueError as exc:
                self._error(HTTPStatus.BAD_REQUEST, "invalid_correction", str(exc))
                return
            self._json(response)
            return
        self._error(HTTPStatus.NOT_FOUND, "not_found", "接口不存在")

    def _read_json(self) -> Optional[Dict[str, Any]]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._error(HTTPStatus.BAD_REQUEST, "invalid_length", "请求长度无效")
            return None
        if length <= 0 or length > MAX_BODY_BYTES:
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

    def _json(
        self, payload: Dict[str, Any], status: HTTPStatus = HTTPStatus.OK
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, code: str, message: str) -> None:
        self._json({"error": {"code": code, "message": message}}, status)

    def _execution_allowed(self):
        if self.app.access_token is None:
            self._error(HTTPStatus.FORBIDDEN, "desktop_required", "执行工作台仅在已鉴权的独立桌面中开放")
            return False
        origin = self.headers.get("Origin")
        if origin and urlparse(origin).netloc != self.headers.get("Host"):
            self._error(HTTPStatus.FORBIDDEN, "invalid_origin", "执行操作只允许来自当前应用")
            return False
        return True

    def _execution_get(self, path):
        if not self._execution_allowed():
            return
        try:
            parts = path.strip("/").split("/")
            if len(parts) == 2:
                self._json({"sessions": self.app.execution.catalog()})
                return
            session = self.app.execution.get(parts[2])
            query = parse_qs(urlparse(self.path).query)
            if len(parts) == 4 and parts[3] == "files":
                self._json(session.read_file(query.get("path", [""])[0]))
            elif len(parts) == 4 and parts[3] == "directory":
                hidden = query.get("hidden", ["0"])[0]
                if hidden not in {"0", "1"}:
                    raise ExecutionError("隐藏项参数无效")
                self._json(
                    session.list_directory(
                        query.get("path", [""])[0],
                        query.get("cursor", [None])[0],
                        int(query.get("limit", ["100"])[0]),
                        hidden == "1",
                    )
                )
            elif len(parts) == 4 and parts[3] == "preview":
                self._json(session.preview_file(query.get("path", [""])[0]))
            elif len(parts) == 4 and parts[3] == "artifact-status":
                self._json(
                    session.artifact_status(
                        query.get("path", [""])[0], query.get("sha256", [""])[0]
                    )
                )
            elif len(parts) == 4 and parts[3] == "image":
                data, mime = session.image_file(
                    query.get("path", [""])[0], query.get("sha256", [""])[0]
                )
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy", "default-src 'none'; sandbox"
                )
                self.end_headers()
                self.wfile.write(data)
            elif len(parts) == 4 and parts[3] == "changes":
                self._json(
                    session.changes(
                        query.get("path", [None])[0],
                        limit=int(query.get("limit", ["200"])[0]),
                        offset=int(query.get("offset", ["0"])[0]),
                    )
                )
            elif len(parts) == 3:
                after = int(query.get("after", ["0"])[0])
                include = query.get("include_artifacts", ["1"])[0]
                if include not in {"0", "1"}:
                    raise ExecutionError("文件概览参数无效")
                self._json(
                    session.snapshot(max(0, after), include_artifacts=include == "1")
                )
            else:
                raise ExecutionError("执行接口不存在")
        except DirectoryChanged as exc:
            self._error(HTTPStatus.CONFLICT, "directory_changed", str(exc))
        except (ExecutionError, ValueError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "execution_error", str(exc))

    def _execution_post(self, path):
        if not self._execution_allowed():
            return
        payload = self._read_json()
        if payload is None:
            return
        try:
            parts = path.strip("/").split("/")
            if len(parts) == 2 and not payload:
                with self.app.workspace.lock:
                    settings = dict(self.app.workspace.settings)
                    registry = self.app.workspace.engine.content.registry
                self._json(
                    self.app.execution.create(settings, registry=registry).snapshot(),
                    HTTPStatus.CREATED,
                )
                return
            if len(parts) != 4:
                raise ExecutionError("执行接口不存在")
            if parts[3] == "open" and not payload:
                with self.app.workspace.lock:
                    settings = dict(self.app.workspace.settings)
                self._json(self.app.execution.open(parts[2], settings).snapshot())
                return
            if parts[3] == "close" and not payload:
                self.app.execution.release(parts[2])
                self._json({"closed": True})
                return
            session = self.app.execution.get(parts[2])
            if parts[3] == "answer" and set(payload) == {
                "request_id",
                "turn_id",
                "answer",
            }:
                self._json(
                    session.answer_clarification(
                        payload["request_id"], payload["turn_id"], payload["answer"]
                    )
                )
                return
            if parts[3] == "runs" and not set(payload) - {
                "text",
                "max_calls",
                "time_budget_seconds",
            }:
                if not session.execution_configured:
                    with self.app.workspace.lock:
                        settings = dict(self.app.workspace.settings)
                    with session.lock:
                        session.bind(settings)
                self._json(
                    session.start_turn(
                        payload.get("text"),
                        payload.get("max_calls", 24),
                        payload.get("time_budget_seconds", 180),
                    ),
                    HTTPStatus.ACCEPTED,
                )
            elif parts[3] == "cancel" and not payload:
                self._json(session.cancel())
            else:
                raise ExecutionError("执行参数无效")
        except ExecutionBusy as exc:
            self._error(HTTPStatus.CONFLICT, "execution_busy", str(exc))
        except (ExecutionError, ValueError, OSError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, "execution_error", str(exc))

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


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    event_log: Optional[Path] = None,
    backend: Optional[str] = None,
) -> None:
    runtime = build_runtime(event_log, backend)
    context = runtime.start()
    engine = context.services.require("consultation")
    events = context.services.require("events")
    httpd = MedicalAgentServer(
        (host, port), Handler, engine, events, context.services.require("backend_mode")
    )
    print(f"问诊智能体研究原型运行于 http://{host}:{port}")
    print("按 Ctrl+C 停止。默认不持久化会话；这不是已经验证的医疗服务。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        runtime.stop()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the outpatient consultation agent prototype"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--event-log",
        type=Path,
        default=None,
        help="Optional append-only JSONL event log",
    )
    parser.add_argument(
        "--backend", choices=["demo", "compatible", "dsh"], default=None
    )
    args = parser.parse_args()
    serve(args.host, args.port, args.event_log, args.backend)


if __name__ == "__main__":
    main()
