"""Execution-side scenario drivers shared across suites: the capability
payload shapes, the scripted SSE model reply, and the long-running
exercise_* drivers the Windows and cancellation suites reuse."""
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from uuid import uuid4

from medagent.execution import ExecutionManager
from medagent.execution.capabilities import freeze_binding
from tests.support.scripted import Provider, final, tool


def command(seconds, path):
    if os.name == "nt":
        return (
            f"[IO.File]::WriteAllText([IO.Path]::Combine([Environment]::CurrentDirectory, '{path}.started'), 'started'); "
            f"Start-Sleep -Seconds {seconds}; 'finished' | Set-Content -Encoding UTF8 {path}"
        )
    return f"printf started > {path}.started; sleep {seconds}; printf finished > {path}"



def received_answers(body):
    values = []
    for message in body.get("messages", []):
        for part in message.get("content", []):
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            content = part.get("content", [])
            texts = [content] if isinstance(content, str) else [b.get("text", "") for b in content if isinstance(b, dict)]
            for text in texts:
                try:
                    value = json.loads(text)
                except (ValueError, TypeError):
                    continue
                if isinstance(value, dict) and value.get("schema") == "execution-answer/v1":
                    values.append(value)
    return values



def payload(
    text="请把1.25 kg换算成g", quote="1.25 kg", value="1.25", before="kg", after="g"
):
    turn = str(uuid4())
    return {
        "binding": freeze_binding(),
        "operation": "execute",
        "workspace_id": str(uuid4()),
        "turn_id": turn,
        "sources": [{"id": turn, "text": text}],
        "request": {
            "capability_id": "calculate.units",
            "input": {"value": value, "from_unit": before, "to_unit": after},
            "evidence": quote,
        },
    }



def model_reply(handler, index, tool=None):
    events = [
        {
            "type": "message_start",
            "message": {
                "id": f"cap-{index}",
                "type": "message",
                "role": "assistant",
                "model": "deepseek-flash",
                "content": [],
                "usage": {"input_tokens": 20, "output_tokens": 1},
            },
        },
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": f"call-{index}",
                "name": tool["name"],
                "input": {},
            }
            if tool
            else {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": json.dumps(tool["input"]),
            }
            if tool
            else {"type": "text_delta", "text": "计算完成，输入仍待核对。"},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use" if tool else "end_turn"},
            "usage": {"output_tokens": 20},
        },
        {"type": "message_stop"},
    ]
    data = "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
        for event in events
    ).encode()
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    handler.wfile.write(data)



def exercise_task_cancellation(owner):
    for mode in ["idle_short_followup", "busy_short_followup", "busy_pending_question", "old_expiry", "user_stop"]:
        with owner.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
            busy = mode.startswith("busy_")
            child_arrived, root_arrived = threading.Event(), threading.Event()
            child_release, root_release = threading.Event(), threading.Event()
            counts, lock, answer_id = {"root": 0, "child": 0}, threading.Lock(), [None]

            def respond(_index, body):
                root = "ROOT_SELECTIVE_FIXTURE" in json.dumps(body.get("messages", []))
                role = "root" if root else "child"
                with lock:
                    counts[role] += 1
                    step = counts[role]
                if root:
                    if step == 1:
                        return tool("subagent", description="Original independent child",
                                    prompt="CHILD_SELECTIVE_FIXTURE: complete the fixed synthetic plan.", run_in_background=True)
                    if step == 3:
                        root_arrived.set()
                        if busy or mode == "old_expiry":
                            root_release.wait(40)
                    return final("Root acknowledged.")
                if step == 1:
                    child_arrived.set()
                    child_release.wait(40)
                    if busy:
                        return tool("request_clarification", question="旧任务保留哪种格式？", options=["CSV", "JSON"])
                if step == 1 or busy and step == 2:
                    if busy:
                        owner.assertTrue(any(v.get("request_id") == answer_id[0] and v.get("source") == "user" and v.get("answer") == "CSV" for v in received_answers(body)))
                    return tool("bash", command=command(0, "child.txt"), description="Original child artifact", timeoutMs=30000)
                return final("Child completed.")

            provider = Provider(respond)
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create({"MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                                          "MEDAGENT_LLM_API_KEY": "selective-loopback-only", "MEDAGENT_LLM_MODEL": "fixture"})

                def wait(predicate, timeout=60 if os.name == "nt" else 35):
                    until = time.monotonic() + timeout
                    while time.monotonic() < until:
                        value = predicate()
                        if value:
                            return value
                        time.sleep(0.05)
                    owner.fail(json.dumps({"mode": mode, "status": session.status, "error": session.error,
                                           "events": list(session.events)[-70:], "jobs": session.jobs, "agents": session.agents}, ensure_ascii=False))

                old_seconds = (6 if os.name == "nt" else 2) if mode == "old_expiry" else 60
                session.start_turn("ROOT_SELECTIVE_FIXTURE: spawn and acknowledge the independent child.", 8, old_seconds)
                original = session.turn_id
                wait(lambda: session.status == "completed")
                owner.assertTrue(child_arrived.wait(5))
                process = session.process
                short = 3 if os.name == "nt" else 2
                new_seconds = 45 if mode in {"old_expiry", "user_stop"} else (6 if os.name == "nt" and mode == "busy_pending_question" else short)
                session.start_turn("ROOT_SELECTIVE_FIXTURE: acknowledge a separate task with its own deadline.", 4, new_seconds)
                latest = session.turn_id
                owner.assertTrue(root_arrived.wait(5))

                def child_finished():
                    return next((e for e in list(session.events) if e["type"] == "runtime.event"
                                 and e.get("notification", {}).get("method") == "subagent.finished"), None)

                if mode == "old_expiry":
                    finished = wait(child_finished)
                    owner.assertEqual(finished["turn_id"], original)
                    owner.assertEqual(finished["notification"]["params"]["status"], "error")
                    owner.assertEqual(session.status, "running")
                    wait(lambda: session.turns[0]["status"] == "cancelled")
                    root_release.set()
                    wait(lambda: session.status == "completed")
                elif mode == "user_stop":
                    wait(lambda: session.status == "completed")
                    wait(lambda: any(a["session_id"] != session.id for a in session.agents))
                    owner.assertFalse(session.jobs, "This case must exercise a child waiting on a model, not a command job")
                    session.cancel()
                    wait(lambda: session.status == "cancelled")
                    finished = wait(child_finished)
                    owner.assertEqual(finished["notification"]["params"]["status"], "error")
                    wait(lambda: not session.agents)
                else:
                    pending_before_stop = None
                    if busy:
                        if mode == "busy_pending_question":
                            child_release.set()
                            pending_before_stop = wait(lambda: next((q for q in session.clarifications if q["status"] == "pending"), None))
                            owner.assertEqual(session.status, "running")
                        wait(lambda: session.status == "cancelled")
                        if pending_before_stop:
                            owner.assertEqual(pending_before_stop["status"], "pending")
                    else:
                        wait(lambda: session.status == "completed")
                        time.sleep(short + 0.8)
                        owner.assertEqual(session.status, "completed", session.error)
                    child_release.set()
                    if busy:
                        q = wait(lambda: next((q for q in session.clarifications if q["status"] == "pending"), None))
                        owner.assertEqual(q["turn_id"], original)
                        answer_id[0] = q["id"]
                        session.answer_clarification(q["id"], original, "CSV")
                    finished = wait(child_finished)
                    owner.assertEqual(finished["turn_id"], original)
                    owner.assertEqual(finished["notification"]["params"]["status"], "ok")
                    owner.assertEqual((session.directory / "child.txt").read_text(encoding="utf-8-sig").strip(), "finished")
                if mode in {"old_expiry", "user_stop"}:
                    owner.assertFalse((session.directory / "child.txt").exists())
                owner.assertEqual(session.turn_id, latest)
                owner.assertIs(session.process, process)
                if mode != "user_stop":
                    owner.assertFalse(any(e["type"] == "jobs.cancelled" for e in session.events))
                print(json.dumps({"task_cancellation_case": mode, "passed": True, "actual_sdk": True,
                                  "synthetic_loopback_provider": True, "original_turn": original,
                                  "latest_turn": latest, "latest_status": session.status,
                                  "child_status": finished["notification"]["params"]["status"],
                                  "root_requests": counts["root"], "child_requests": counts["child"]}), flush=True)
            finally:
                root_release.set()
                child_release.set()
                manager.close()
                provider.shutdown()
                provider.server_close()



def exercise_root_job_cancellation(owner):
    for mode in ["short_new_with_only_old_job", "two_grants_with_jobs", "new_failure_with_old_job"]:
        with owner.subTest(mode=mode), tempfile.TemporaryDirectory() as raw:
            long_new = mode != "short_new_with_only_old_job"
            fails = mode == "new_failure_with_old_job"
            old_seconds, old_sleep, new_sleep = (30, 5, 10) if fails else (((8, 12, 9) if os.name == "nt" else (3, 6, 4)) if long_new else (30, 5, 0))
            responses = [tool("bash", command=command(old_sleep, "old.txt"), description="Original background command", run_in_background=True), final("Original job started.")]
            if long_new:
                responses += [tool("bash", command=command(new_sleep, "new.txt"), description="Independent background command", run_in_background=True), final("" if fails else "Independent job started.")]
            else:
                responses += [final("Independent short task acknowledged.")]
            provider = Provider(responses)
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create({"MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic", "MEDAGENT_LLM_API_KEY": "root-jobs-loopback-only", "MEDAGENT_LLM_MODEL": "fixture"})

                def wait(predicate, timeout=35):
                    until = time.monotonic() + timeout
                    while time.monotonic() < until:
                        if predicate(): return
                        time.sleep(0.05)
                    owner.fail(json.dumps({"error": session.error, "events": list(session.events)[-60:], "jobs": session.jobs}, ensure_ascii=False))

                session.start_turn("Start the original fixed background command.", 2, old_seconds)
                wait(lambda: session.status == "completed")
                process = session.process
                owner.assertTrue(any(j["status"] in {"running", "starting"} for j in session.jobs),
                                 json.dumps(list(session.events)[-30:], ensure_ascii=False))
                wait(lambda: (session.directory / "old.txt.started").exists())
                session.start_turn("A separate task must not stop the original job or adopt its deadline.", 2, 40 if long_new else (3 if os.name == "nt" else 2))
                latest = session.turn_id
                wait(lambda: session.status == ("failed" if fails else "completed"))
                wait(lambda: (session.directory / ("new.txt" if long_new and not fails else "old.txt")).exists())
                owner.assertEqual(session.status, "failed" if fails else "completed", session.error)
                owner.assertEqual(session.turn_id, latest)
                owner.assertIs(session.process, process)
                if fails:
                    owner.assertFalse((session.directory / "new.txt").exists())
                elif long_new:
                    owner.assertFalse((session.directory / "old.txt").exists())
                    wait(lambda: not any(j["status"] in {"running", "starting", "stopping"} for j in session.jobs))
                print(json.dumps({"root_job_cancellation_case": mode,
                                  "passed": True, "actual_sdk": True, "synthetic_loopback_provider": True}), flush=True)
            finally:
                manager.close()
                provider.shutdown()
                provider.server_close()



def exercise_command_deadline(owner):
    native = os.name == "nt"
    old_seconds, old_sleep, new_sleep = (5, 8, 6) if native else (2, 4, 3)

    def command(seconds, path):
        if native:
            def marker(stage):
                return (
                    f"[IO.File]::WriteAllText([IO.Path]::Combine([Environment]::CurrentDirectory, '{path}.{stage}'), "
                    "[DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()); "
                )
            return (
                marker("started")
                + f"Start-Sleep -Seconds {seconds}; "
                + marker("awake")
                + f"'finished' | Set-Content -Encoding UTF8 {path}"
            )
        return f"sleep {seconds}; printf finished > {path}"

    for followup in [False, True]:
        with owner.subTest(independent_followup=followup), tempfile.TemporaryDirectory() as raw:
            responses = [
                tool("bash", command=command(old_sleep, "old-late.txt"),
                     run_in_background=True, description="Original short task deadline"),
                final("Original background command started."),
                tool("bash", command=command(new_sleep, "followup.txt"),
                     description="Independent follow-up outlives the first deadline", timeoutMs=30000),
                final("Independent follow-up completed."),
            ]
            provider = Provider(responses)
            manager = ExecutionManager(root=Path(raw))
            settings = {
                "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                "MEDAGENT_LLM_API_KEY": "command-deadline-loopback-only",
                "MEDAGENT_LLM_MODEL": "fixture",
            }

            def finish(session):
                limit = time.monotonic() + 30
                while session.status in {"starting", "running", "cancelling"} and time.monotonic() < limit:
                    time.sleep(0.05)
                owner.assertEqual(
                    session.status, "completed",
                    json.dumps({"status": session.status, "error": session.error,
                                "events": list(session.events)[-60:], "jobs": session.jobs,
                                "artifacts": {name: (session.directory / name).read_text(encoding="utf-8-sig")
                                              for name in ["old-late.txt", "old-late.txt.started", "old-late.txt.awake", "followup.txt", "followup.txt.started", "followup.txt.awake"]
                                              if (session.directory / name).is_file()}}, ensure_ascii=False),
                )

            try:
                session = manager.create(settings)
                session.start_turn("Start the fixed synthetic background command.", 2, old_seconds)
                finish(session)
                original = session.turn_id
                process = session.process
                owner.assertTrue(any(job["status"] == "running" for job in session.jobs))
                observe_until = time.monotonic() + old_sleep + 1
                if followup:
                    session.start_turn(
                        "Run the independent follow-up command. Do not extend or restart the original job.",
                        4, 20,
                    )
                    finish(session)
                    owner.assertNotEqual(session.turn_id, original)
                    owner.assertIs(session.process, process)
                    owner.assertTrue(
                        (session.directory / "followup.txt").exists(),
                        json.dumps({"events": list(session.events)[-60:], "jobs": session.jobs,
                                "artifacts": {name: (session.directory / name).read_text(encoding="utf-8-sig")
                                              for name in ["old-late.txt", "old-late.txt.started", "old-late.txt.awake", "followup.txt", "followup.txt.started", "followup.txt.awake"]
                                              if (session.directory / name).is_file()}}, ensure_ascii=False),
                    )
                    owner.assertEqual(
                        (session.directory / "followup.txt").read_text(encoding="utf-8-sig").strip(),
                        "finished",
                    )
                while time.monotonic() < observe_until:
                    time.sleep(0.05)
                owner.assertFalse((session.directory / "old-late.txt").exists())
                owner.assertEqual(len(provider.requests), 4 if followup else 2)
                owner.assertEqual(session.turns[0]["time_budget_seconds"], old_seconds)
                if followup:
                    owner.assertEqual(
                    session.status, "completed",
                    json.dumps({"status": session.status, "error": session.error,
                                "events": list(session.events)[-60:], "jobs": session.jobs,
                                "artifacts": {name: (session.directory / name).read_text(encoding="utf-8-sig")
                                              for name in ["old-late.txt", "old-late.txt.started", "old-late.txt.awake", "followup.txt", "followup.txt.started", "followup.txt.awake"]
                                              if (session.directory / name).is_file()}}, ensure_ascii=False),
                )
                    owner.assertEqual(session.turns[-1]["time_budget_seconds"], 20)
            finally:
                manager.close()
                provider.shutdown()
                provider.server_close()



def exercise_child_ownership(owner):
    for funded in [False, True]:
        with owner.subTest(original_has_calls=funded), tempfile.TemporaryDirectory() as raw:
            released, arrived = threading.Event(), threading.Event()
            counts, lock = {"root": 0, "child": 0}, threading.Lock()
            answered_question = [None]
            search_calls = [0]

            def search_reply(body):
                if not any(t.get("type") == "web_search_20250305" for t in body.get("tools", [])):
                    return None
                search_calls[0] += 1
                return {"content": [{"type": "web_search_tool_result", "content": [
                    {"type": "web_search_result", "url": "https://example.org/fixture", "title": "Synthetic fixture"}
                ]}]}

            def respond(_index, body):
                root = "ROOT_OWNERSHIP_FIXTURE" in json.dumps(body.get("messages", []))
                role = "root" if root else "child"
                with lock:
                    counts[role] += 1
                    step = counts[role]
                if root and step == 1:
                    return tool("subagent", description="Synthetic independent child",
                                prompt="CHILD_OWNERSHIP_FIXTURE: execute the fixed synthetic tool and finish.",
                                run_in_background=True)
                if not root and step == 1:
                    arrived.set()
                    released.wait(20)
                    if funded:
                        return tool("request_clarification", question="原任务保留哪种格式？", options=["CSV", "JSON"])
                if not root and (step == 1 or funded and step == 2):
                    if funded:
                        answers = []
                        for message in body.get("messages", []):
                            for part in message.get("content", []):
                                if not isinstance(part, dict) or part.get("type") != "tool_result":
                                    continue
                                content = part.get("content", [])
                                texts = (
                                    [content] if isinstance(content, str)
                                    else [b.get("text", "") for b in content if isinstance(b, dict)]
                                )
                                for text in texts:
                                    try:
                                        value = json.loads(text)
                                    except (ValueError, TypeError):
                                        continue
                                    if isinstance(value, dict) and value.get("schema") == "execution-answer/v1":
                                        answers.append(value)
                        owner.assertTrue(any(
                            v.get("source") == "user" and v.get("answer") == "CSV"
                            and v.get("request_id") == answered_question[0]
                            for v in answers
                        ))
                    command = (
                        "'child' | Set-Content -Encoding UTF8 child.txt"
                        if os.name == "nt" else "printf child > child.txt"
                    )
                    return tool("bash", command=command, description="Synthetic child artifact", timeoutMs=30000)
                if not root and funded and step == 3:
                    return tool("web_search", queries=["synthetic ownership fixture"])
                return final("Root acknowledgement." if root else "Child completed.")

            provider = Provider(respond, json_reply=search_reply)
            manager = ExecutionManager(root=Path(raw))
            try:
                session = manager.create({
                    "MEDAGENT_LLM_BASE_URL": f"http://127.0.0.1:{provider.server_port}/anthropic",
                    "MEDAGENT_LLM_API_KEY": "ownership-loopback-only",
                    "MEDAGENT_LLM_MODEL": "fixture",
                })

                def wait(predicate, timeout=30):
                    limit = time.monotonic() + timeout
                    while time.monotonic() < limit:
                        value = predicate()
                        if value:
                            return value
                        time.sleep(0.05)
                    owner.fail(json.dumps(
                        {"error": session.error, "events": list(session.events)[-60:]},
                        ensure_ascii=False,
                    ))

                session.start_turn(
                    "ROOT_OWNERSHIP_FIXTURE: spawn the synthetic child, acknowledge "
                    "and retain its original shared call limit.",
                    8 if funded else 3, 60,
                )
                original = session.turn_id
                wait(lambda: session.status in {"completed", "failed", "cancelled"})
                owner.assertEqual(session.status, "completed", session.error)
                owner.assertTrue(arrived.wait(5))
                session.start_turn(
                    "ROOT_OWNERSHIP_FIXTURE: acknowledge an independent follow-up "
                    "without changing the old child grant.",
                    1 if funded else 8, 45,
                )
                latest = session.turn_id
                wait(lambda: session.status in {"completed", "failed", "cancelled"})
                owner.assertEqual(session.status, "completed", session.error)
                released.set()
                if funded:
                    question = wait(lambda: next((q for q in session.clarifications if q["status"] == "pending"), None))
                    owner.assertEqual(question["turn_id"], original)
                    answered_question[0] = question["id"]
                    session.answer_clarification(question["id"], original, "CSV")
                finished = wait(lambda: next((
                    e for e in list(session.events)
                    if e["type"] == "runtime.event"
                    and e.get("notification", {}).get("method") == "subagent.finished"
                ), None))
                owner.assertEqual(finished["turn_id"], original)
                params = finished["notification"]["params"]
                owner.assertEqual(params["status"], "ok" if funded else "error")
                owner.assertEqual(counts["child"], 4 if funded else 1)
                owner.assertEqual(search_calls[0], 1 if funded else 0)
                searches = [e for e in list(session.events) if e["type"] == "model.request_started" and e.get("source") == "search"]
                owner.assertEqual(len(searches), 1 if funded else 0)
                owner.assertTrue(all(e["turn_id"] == original for e in searches))
                owner.assertEqual((session.directory / "child.txt").read_text(encoding="utf-8-sig").strip(), "child")
                owner.assertEqual(session.turn_id, latest)
                owner.assertLessEqual(session.turns[0]["model_calls"], 8 if funded else 3)
                child_events = [
                    e for e in list(session.events)
                    if e.get("notification", {}).get("params", {}).get("sessionId")
                    == params["childSessionId"]
                ]
                owner.assertTrue(child_events)
                owner.assertTrue(all(e["turn_id"] == original for e in child_events))
            finally:
                released.set()
                manager.close()
                provider.shutdown()
                provider.server_close()

