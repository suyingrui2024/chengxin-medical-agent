"""Package entry point: composes the Handler mixins and exposes the CLI."""
from ._preamble import *  # noqa: F401,F403
from ._preamble import __all__ as _PREAMBLE_ALL
from .types import *  # noqa: F401,F403
from .types import MedicalAgentServer
from .core import HandlerCore
from .consultation import HandlerConsultation
from .patient import HandlerPatient
from .agent import HandlerAgent
from ._preamble import BaseHTTPRequestHandler


class Handler(
    HandlerCore,
    HandlerConsultation,
    HandlerPatient,
    HandlerAgent,
    BaseHTTPRequestHandler
):

    timeout = 30

    server_version = f"MedAgentPrototype/{__version__}"



def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    event_log: Optional[Path] = None,
    backend: Optional[str] = None,
) -> None:
    # The standalone server has no access token (only the desktop
    # sidecar injects one). Binding a non-loopback interface would
    # expose full consultation records to the network — refuse unless
    # the operator explicitly accepts that with MEDAGENT_ALLOW_REMOTE=1.
    if host not in {"127.0.0.1", "::1", "localhost"} and os.environ.get(
        "MEDAGENT_ALLOW_REMOTE"
    ) != "1":
        raise SystemExit(
            f"拒绝启动：--host {host} 不是回环地址，而独立服务没有访问令牌，"
            "问诊记录会暴露给网络。如确需对外绑定，请设置环境变量 "
            "MEDAGENT_ALLOW_REMOTE=1 并自行承担风险。"
        )
    runtime = build_runtime(event_log, backend)
    context = runtime.start()
    engine = context.services.require("consultation")
    events = context.services.require("events")
    httpd = MedicalAgentServer(
        (host, port), Handler, engine, events, context.services.require("backend_mode")
    )
    print(f"问诊智能体研究原型运行于 http://{host}:{port}")
    if getattr(engine, "session_store", None) is not None:
        print(
            "按 Ctrl+C 停止。已关闭的会话与随访计划会保存到本机并在重启后恢复；"
            "进行中的问诊不跨重启。这不是已经验证的医疗服务。"
        )
    else:
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


__all__ = list(_PREAMBLE_ALL) + [
    "MedicalAgentServer",
    "Handler",
    "serve",
    "main"
]
