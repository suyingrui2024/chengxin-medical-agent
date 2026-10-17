"""Replay saved assistant text and tool decisions against the final runtime, no API."""

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def decisions(row):
    root = row["snapshot"]["id"]
    groups = {}
    seen = set()
    for envelope in row["snapshot"]["events"]:
        notification = envelope.get("notification", {})
        params = notification.get("params", {})
        event = params.get("event", {})
        if notification.get("method") != "session.event" or params.get("sessionId") != root:
            continue
        kind = event.get("type")
        identity = (kind, event.get("seq"), event.get("time"))
        if identity in seen or kind not in {"assistant/message", "tool/call"}:
            continue
        seen.add(identity)
        data = event["data"]
        group = groups.setdefault((data["turn"], data["step"]), {"text": [], "tools": []})
        if kind == "assistant/message":
            group["text"] += [value for value in data.get("message", {}).get("content", []) if value.get("type") == "text"]
        else:
            arguments = data["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            group["tools"].append({"type": "tool_use", "id": data["callId"], "name": data["name"], "input": arguments})
    return [group["text"] + group["tools"] for _, group in sorted(groups.items())]


class LocalProvider(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, frames):
        self.frames, self.requests = frames, []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.requests.append(body)
                index = len(self.server.requests) - 1
                content = self.server.frames[index] if index < len(self.server.frames) else [{"type": "text", "text": "Unexpected replay request"}]
                events = [{"type": "message_start", "message": {"id": f"replay-{index}", "model": "saved-decisions", "usage": {"input_tokens": 10, "output_tokens": 1}}}]
                for position, block in enumerate(content):
                    events.extend([{"type": "content_block_start", "index": position, "content_block": block}, {"type": "content_block_stop", "index": position}])
                events.extend([{"type": "message_delta", "delta": {"stop_reason": "tool_use" if any(block["type"] == "tool_use" for block in content) else "end_turn"}, "usage": {"output_tokens": 10}}, {"type": "message_stop"}])
                data = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=HERE.parents[3])
    parser.add_argument("--report", type=Path, default=HERE / "real-provider/round1.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify-source-only", action="store_true")
    args = parser.parse_args()
    if not args.verify_source_only and (args.output is None or args.output.exists()):
        raise SystemExit("Fresh replay output required")
    root = args.project_root.resolve()
    evidence = json.loads((HERE / "verification.json").read_text())
    for name, expected in evidence["final_runtime_source_sha256"].items():
        if not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
            raise SystemExit("Restore snapshot-final into an independent project; source drift: " + name)
    if args.verify_source_only:
        print(json.dumps({"final_frozen_source_matches": True, "checked_files": len(evidence["final_runtime_source_sha256"]), "external_model_requests": 0}))
        return
    sys.path[:0] = [str(root / "scripts"), str(root / "src")]
    from evaluate_delivery_checks import run_case
    report = json.loads(args.report.read_text())
    result = {"kind": "saved-delivery-decisions-replay/v1", "external_model_requests": 0, "scope": "Saved assistant text and ordered tool calls; excludes reasoning and does not claim byte-identical provider frames or new real-model outcomes.", "runs": []}
    for row in report["runs"]:
        frames = decisions(row)
        provider = LocalProvider(frames)
        settings = {"MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic", "MEDAGENT_LLM_API_KEY": "offline-local-replay", "MEDAGENT_LLM_MODEL": "saved-decisions"}
        try:
            with tempfile.TemporaryDirectory(prefix="delivery-saved-replay-") as raw:
                replay = run_case(row["case"], settings, Path(raw) / row["case"])
                checks = {"same_declared_task_outcome": replay["passed"] is row["passed"], "all_engineering_checks_passed": replay["passed"], "same_number_of_model_decisions": len(provider.requests) == row["gateway"]["attempted_calls"] == len(frames), "same_check_exits": [value["exit_code"] for value in replay.get("observations", [])] == [value["exit_code"] for value in row["observations"]]}
                result["runs"].append({"case": row["case"], "checks": checks, "passed": all(checks.values()), "local_fixture_requests": len(provider.requests), "replay": replay})
        finally:
            provider.shutdown()
            provider.server_close()
        result["passed"] = len(result["runs"]) == len(report["runs"]) and all(row["passed"] for row in result["runs"])
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps({"case": row["case"], "passed": result["runs"][-1]["passed"], "checks": checks}), flush=True)
    raise SystemExit(0 if result.get("passed") else 1)


if __name__ == "__main__":
    main()
