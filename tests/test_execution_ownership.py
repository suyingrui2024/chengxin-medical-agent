"""Actual SDK descendants keep their original call grant and event identity."""

import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest

from medagent.execution import ExecutionManager
from tests.test_execution_delivery import Provider, tool, final


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


@unittest.skipUnless(os.environ.get("RUN_DSH_EXECUTION") == "1", "requires real isolated SDK")
class OwnershipRuntimeTests(unittest.TestCase):
    def test_original_child_grant_survives_a_foreground_followup(self):
        exercise_child_ownership(self)
