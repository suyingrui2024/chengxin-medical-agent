"""Offline tests for the subagent coordinator (design §5, Phase C1).

A stub backend scripts the sub-model's envelopes; timing is controlled
through threading events, never sleeps.
"""
import json
import socket
import unittest
from unittest import mock

from medagent.decision_wait import DecisionInterrupted
from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.events import Event, EventStore
from medagent.reasoning import Hypothesis, ReasoningWorkspace
from medagent.subagents import KINDS, SubagentCoordinator, _content_index, watermark
from medagent.web_tools import WebTools


CHIEF_EVIDENCE = "我咳嗽三天了，还有点嗓子疼"
COUGH_EVIDENCE = "主要是干咳，没有痰"


def make_state(session_id="case-1"):
    state = CaseState(session_id=session_id)
    state.record(
        Fact(
            key="chief_complaint",
            value="咳嗽三天",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=CHIEF_EVIDENCE,
        )
    )
    state.record(
        Fact(
            key="resp_cough_details",
            value="干咳",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence=COUGH_EVIDENCE,
        )
    )
    state.record(
        Fact(
            key="medications_taken",
            value="吃过感冒药",
            assertion=Assertion.PRESENT,
            source=Source.PATIENT,
            turn_id="t1",
            evidence="吃过一片感冒药",
        )
    )
    workspace = ReasoningWorkspace(version=1)
    workspace.hypotheses["uri"] = Hypothesis(
        id="uri",
        label="上呼吸道感染",
        category="common_condition",
        prior_band="high",
    )
    state.reasoning = workspace.to_dict()
    state.transcript = [
        {"role": "user", "content": CHIEF_EVIDENCE},
        {"role": "assistant", "content": "咳嗽是干咳还是有痰？"},
        {"role": "user", "content": COUGH_EVIDENCE},
    ]
    return state


def report(findings=None, conclusion="小结", level="low", **extra):
    arguments = {
        "conclusion": conclusion,
        "findings": findings or [],
        "uncertainty": {"level": level, "reasons": []},
    }
    arguments.update(extra)
    return {"tool": "subagent_report", "arguments": arguments}


def case_finding(claim, quote, ref="chief_complaint"):
    return {
        "claim": claim,
        "evidence": [{"source_type": "case", "quote": quote, "ref": ref}],
        "confidence": "medium",
    }


class StubBackend:
    """Scripts sub-model replies through a handler function."""

    name = "stub"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.views = {}

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls.append(session_id)
        if session_id not in self.views:
            self.views[session_id] = json.loads(messages[1]["content"])
        return self.handler(session_id, messages, cancelled)


def last_tool_result(messages):
    content = messages[-1]["content"]
    parsed = json.loads(content)
    return parsed.get("tool_result")


def coordinator(backend, events, **kwargs):
    kwargs.setdefault("mode", "advisory")
    return SubagentCoordinator(events, backend, **kwargs)


def spawn_proposal(kind="evidence_lookup", scope=None, **branch_extra):
    branch = {"branch_id": "b1", "objective": "核对证据", "scope": scope or {}}
    branch.update(branch_extra)
    return {"task_kind": kind, "objective": "测试", "branches": [branch]}


class SubagentCoordinatorTests(unittest.TestCase):
    def test_scope_projection_and_clamping(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                scope={
                    "fact_keys": [
                        "chief_complaint",
                        "resp_cough_details",
                        "temperature_c",
                        "nonexistent_key",
                    ],
                    "hypotheses": ["uri", "nope"],
                },
                tools=["knowledge_search", "record_facts"],
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"])
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "completed")
        view = next(iter(backend.views.values()))
        self.assertEqual(
            set(view["facts"]), {"chief_complaint", "resp_cough_details"}
        )
        self.assertNotIn("medications_taken", view["facts"])
        self.assertEqual([h["id"] for h in view["hypotheses"]], ["uri"])
        clamped = branch["validation"]["scope_clamped"]
        self.assertIn("temperature_c", clamped)
        self.assertIn("nonexistent_key", clamped)
        self.assertIn("hypothesis:nope", clamped)
        started = [
            e for e in events.for_session(state.session_id)
            if e.event_type == "subagent.started"
        ]
        self.assertEqual(started[0].payload["tools_clamped"], ["record_facts"])
        types = [e.event_type for e in events.for_session(state.session_id)]
        for expected in (
            "subagent.proposed",
            "subagent.started",
            "subagent.validated",
            "subagent.finished",
        ):
            self.assertIn(expected, types)

    def test_second_opinion_view_is_blinded(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[case_finding("干咳为主", COUGH_EVIDENCE, "resp_cough_details")]
            )
        )
        state = make_state()
        state.conclusion = {"disposition": "urgent_today", "revision": 3}
        state.risk.level = state.risk.level.URGENT
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                kind="second_opinion",
                scope={"fact_keys": ["resp_cough_details"]},
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"])
        view = next(iter(backend.views.values()))
        self.assertNotIn("conclusion", view)
        self.assertNotIn("risk_level", view)
        self.assertNotIn("urgent_today", json.dumps(view, ensure_ascii=False))

    def test_fake_quote_dropped_real_quote_survives(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[
                    case_finding("患者发烧到四十度", "根本没有这句原话"),
                    case_finding("咳嗽已持续三天", CHIEF_EVIDENCE),
                ]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "partial")
        self.assertEqual(len(branch["findings"]), 1)
        self.assertEqual(branch["findings"][0]["claim"], "咳嗽已持续三天")
        self.assertEqual(branch["findings"][0]["provenance"], "subagent")
        self.assertEqual(branch["validation"]["dropped_claims"], 1)
        self.assertEqual(branch["validation"]["quotes_verified"], 1)
        # Uncertainty floor: a dropped claim forbids a "low" self-report.
        self.assertEqual(branch["uncertainty"]["level"], "medium")

    def test_majority_dropped_rejects_whole_result(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[
                    case_finding("断言一", "伪造引文一"),
                    case_finding("断言二", "伪造引文二"),
                ]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "rejected_by_validation")
        self.assertEqual(branch["findings"], [])

    def test_banned_phrase_drops_claim(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[
                    case_finding("结合表现可以确诊为上呼吸道感染", CHIEF_EVIDENCE),
                    case_finding("咳嗽已持续三天", CHIEF_EVIDENCE),
                ]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "partial")
        self.assertEqual(
            [finding["claim"] for finding in branch["findings"]], ["咳嗽已持续三天"]
        )

    def test_stale_when_case_changes_mid_run(self):
        events = EventStore()
        state = make_state()

        def mutating_handler(sid, messages, cancelled):
            state.revision += 1
            return report(findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)])

        backend = StubBackend(mutating_handler)
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "stale")
        self.assertEqual(branch["findings"], [])
        types = [e.event_type for e in events.for_session(state.session_id)]
        self.assertIn("subagent.stale", types)

    def test_budget_exhausted_when_never_reports(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: {
                "tool": "knowledge_search",
                "arguments": {"query": "上呼吸道感染"},
            }
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(kind="second_opinion", scope={"fact_keys": ["chief_complaint"]}),
            session_id=state.session_id,
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "budget_exhausted")
        self.assertEqual(len(backend.calls), KINDS["second_opinion"]["max_calls"])
        exceeded = [
            e for e in events.for_session(state.session_id)
            if e.event_type == "budget.exceeded"
        ]
        self.assertTrue(exceeded)

    def test_knowledge_search_roundtrip_uses_content_pool(self):
        events = EventStore()

        def handler(sid, messages, cancelled):
            result = last_tool_result(messages)
            if result is None:
                return {
                    "tool": "knowledge_search",
                    "arguments": {"query": "上呼吸道感染"},
                }
            hits = result["results"]
            assert hits, "content pool returned no hits"
            first = hits[0]
            quote = first["excerpt"][5:25]
            return report(
                findings=[
                    {
                        "claim": "本地内容库收录了该方向条目",
                        "evidence": [
                            {
                                "source_type": "knowledge",
                                "quote": quote,
                                "ref": first["ref"],
                            }
                        ],
                        "confidence": "high",
                    }
                ]
            )

        backend = StubBackend(handler)
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(scope={"fact_keys": ["chief_complaint"], "hypotheses": ["uri"]}),
            session_id=state.session_id,
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "completed")
        self.assertEqual(len(branch["findings"]), 1)
        self.assertEqual(branch["sources"][0]["kind"], "content_id")

    def test_knowledge_search_result_content(self):
        events = EventStore()
        seen = {}

        def handler(sid, messages, cancelled):
            result = last_tool_result(messages)
            if result is None:
                return {
                    "tool": "knowledge_search",
                    "arguments": {"query": "上呼吸道感染"},
                }
            seen["result"] = result
            return report(findings=[], disposition_tendency="routine_visit")

        backend = StubBackend(handler)
        state = make_state()
        coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(kind="second_opinion", scope={"fact_keys": ["chief_complaint"]}),
            session_id=state.session_id,
        )
        self.assertIn("results", seen["result"])
        self.assertTrue(seen["result"]["results"])
        self.assertIn(
            "上呼吸道感染",
            json.dumps(seen["result"]["results"], ensure_ascii=False),
        )

    def test_denied_tool_attempt_is_logged_and_run_continues(self):
        events = EventStore()
        attempts = {"count": 0}

        def handler(sid, messages, cancelled):
            attempts["count"] += 1
            if attempts["count"] == 1:
                return {"tool": "record_facts", "arguments": {"facts": []}}
            return report(findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)])

        backend = StubBackend(handler)
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        self.assertEqual(result["branches"][0]["status"], "completed")
        denied = [
            e for e in events.for_session(state.session_id)
            if e.event_type == "subagent.denied"
        ]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0].payload["tool"], "record_facts")

    def test_timeout_settles_branch(self):
        events = EventStore()

        def blocking_handler(sid, messages, cancelled):
            cancelled.wait(5)
            if cancelled.is_set():
                raise DecisionInterrupted()
            return report()

        backend = StubBackend(blocking_handler)
        state = make_state()
        proposal = spawn_proposal()
        proposal["budget"] = {"deadline_s": 0.2}
        result = coordinator(backend, events).run_spawn(
            state, proposal, session_id=state.session_id
        )
        self.assertEqual(result["branches"][0]["status"], "timeout")

    def test_usage_aggregated_from_model_events(self):
        events = EventStore()

        def handler(sid, messages, cancelled):
            events.append(
                Event(
                    sid,
                    "model.responded",
                    {
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                            "prompt_cache_hit_tokens": 30,
                        }
                    },
                )
            )
            return report(findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)])

        backend = StubBackend(handler)
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        usage = result["branches"][0]["usage"]
        self.assertEqual(usage["model_calls"], 1)
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["output_tokens"], 20)
        self.assertEqual(usage["total_tokens"], 120)
        self.assertEqual(result["usage_total"]["total_tokens"], 120)

    def test_web_tools_called_with_subagent_caller(self):
        # 子智能体经 WebTools 联网时，遥测必须归因 caller="subagent"，
        # 不能默认记成教育层的 "education"（search 与 fetch 两条路径）。
        events = EventStore()
        document = {
            "content": [
                {
                    "type": "web_search_tool_result",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://www.who.int/zh/fact",
                            "title": "WHO 流感说明",
                        }
                    ],
                }
            ]
        }

        def transport(method, url, headers, body, timeout):
            return (
                200,
                {"Content-Type": "application/json"},
                json.dumps(document).encode(),
            )

        tools = WebTools(events, api_key="<redacted>", transport=transport)
        tools._http_get = lambda url, timeout: (
            200,
            {"Content-Type": "text/plain; charset=utf-8"},
            "流感防治要点：多休息、多喝水。".encode(),
        )
        steps = {"n": 0}

        def handler(sid, messages, cancelled):
            steps["n"] += 1
            if steps["n"] == 1:
                return {
                    "tool": "web_search",
                    "arguments": {"query": "流感 防治", "purpose": "guideline_check"},
                }
            if steps["n"] == 2:
                return {
                    "tool": "web_fetch",
                    "arguments": {"url": "https://www.who.int/zh/fact"},
                }
            return report(
                findings=[
                    {
                        "claim": "页面给出流感防治要点",
                        "evidence": [
                            {
                                "source_type": "web",
                                "quote": "流感防治要点",
                                "ref": "https://www.who.int/zh/fact",
                            }
                        ],
                        "confidence": "medium",
                    }
                ]
            )

        backend = StubBackend(handler)
        coord = coordinator(
            backend, events, web_enabled=True, web_tools=tools
        )
        state = make_state()
        public_addr = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))
        ]
        with mock.patch.object(socket, "getaddrinfo", lambda *a, **k: public_addr):
            result = coord.run_spawn(
                state,
                spawn_proposal(
                    scope={"fact_keys": ["chief_complaint"]},
                    tools=["web_search", "web_fetch"],
                ),
                session_id=state.session_id,
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["branches"][0]["status"], "completed")
        web_events = events.for_session("web-tools")
        for event_type in (
            "web.search_requested",
            "web.search_completed",
            "web.fetch_completed",
        ):
            payloads = [e.payload for e in web_events if e.event_type == event_type]
            self.assertTrue(payloads, event_type)
            self.assertEqual(payloads[0].get("caller"), "subagent", event_type)

    def test_mode_off_is_disabled(self):
        events = EventStore()
        backend = StubBackend(lambda sid, m, c: report())
        state = make_state()
        result = SubagentCoordinator(events, backend, mode="off").run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        self.assertEqual(result, {"ok": False, "status": "disabled"})
        self.assertEqual(backend.calls, [])

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            SubagentCoordinator(EventStore(), object(), mode="loud")

    def test_backend_without_cancellable_is_unavailable(self):
        class DecideOnly:
            name = "demo"

            def decide(self, session_id, messages):
                return {}

        events = EventStore()
        coord = SubagentCoordinator(events, DecideOnly(), mode="advisory")
        state = make_state()
        self.assertEqual(
            coord.run_spawn(state, spawn_proposal(), session_id=state.session_id),
            {"ok": False, "status": "unavailable"},
        )
        self.assertIsNone(coord.run_case_digest(state, session_id=state.session_id))
        self.assertEqual(coord.run_followup_batch([], {}), [])

    def test_case_digest_returns_validated_structure(self):
        events = EventStore()

        def handler(sid, messages, cancelled):
            return report(
                conclusion="",
                findings=[],
                digest={
                    "timeline": ["三天前开始咳嗽"],
                    "confirmed_facts": [
                        {"key": "chief_complaint", "quote": CHIEF_EVIDENCE},
                        {"key": "chief_complaint", "quote": "这是伪造的引文"},
                        {"key": "resp_cough_details", "quote": COUGH_EVIDENCE},
                    ],
                    "contradictions": [],
                    "prior_outcomes": [],
                },
            )

        backend = StubBackend(handler)
        state = make_state()
        digest = coordinator(backend, events).run_case_digest(
            state, session_id=state.session_id
        )
        self.assertIsNotNone(digest)
        self.assertEqual(digest["timeline"], ["三天前开始咳嗽"])
        self.assertEqual(len(digest["confirmed_facts"]), 2)
        view = next(iter(backend.views.values()))
        self.assertIn("transcript", view)
        self.assertEqual(len(view["transcript"]), 3)

    def test_followup_batch_drafts_and_lint_drop(self):
        events = EventStore()

        def handler(sid, messages, cancelled):
            view = json.loads(messages[1]["content"])
            if view.get("title") == "咳嗽复查":
                return report(
                    findings=[],
                    priority="high",
                    reason="上次结论提示需要确认恢复情况",
                    draft_message="您好，想了解一下您这几天咳嗽恢复得怎么样了？",
                )
            return report(
                findings=[],
                priority="routine",
                reason="例行回访",
                draft_message="您放心，保证没事，按时吃药就行",
            )

        backend = StubBackend(handler)
        states = {}
        items = []
        for session_id, title in (("fu-a", "咳嗽复查"), ("fu-b", "发热复查")):
            state = make_state(session_id)
            state.follow_up = {
                "kind": "routine_checkin",
                "status": "scheduled",
                "due_at": "2026-10-24T09:00:00+00:00",
                "reason": "例行回访确认恢复情况",
                "outcome": None,
            }
            states[session_id] = state
            items.append(
                {
                    "session_id": session_id,
                    "title": title,
                    "plan": dict(state.follow_up),
                    "risk": "routine",
                }
            )
        output = coordinator(backend, events).run_followup_batch(items, states)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["session_id"], "fu-a")
        self.assertEqual(output[0]["priority"], "high")
        self.assertIn("咳嗽恢复", output[0]["draft_message"])

    def test_watermark_changes_with_revision(self):
        state = make_state()
        before = watermark(state)
        state.revision += 1
        self.assertNotEqual(before, watermark(state))

    def test_second_opinion_tendency_is_required_and_repaired(self):
        events = EventStore()
        attempts = {"count": 0}

        def handler(sid, messages, cancelled):
            attempts["count"] += 1
            if attempts["count"] == 1:
                # Missing the required structured tendency: the host must
                # feed the schema error back instead of accepting it.
                return report(
                    findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)]
                )
            return report(
                findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)],
                disposition_tendency="urgent_today",
            )

        backend = StubBackend(handler)
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                kind="second_opinion", scope={"fact_keys": ["chief_complaint"]}
            ),
            session_id=state.session_id,
        )
        branch = result["branches"][0]
        self.assertEqual(attempts["count"], 2)
        self.assertEqual(branch["status"], "completed")
        self.assertEqual(branch["disposition_tendency"], "urgent_today")

    def test_daily_token_fuse_refuses_new_spawns(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(findings=[])
        )
        coord = coordinator(backend, events, daily_token_cap=10)
        coord._daily_add(10)
        state = make_state()
        result = coord.run_spawn(
            state, spawn_proposal(), session_id=state.session_id
        )
        self.assertEqual(result["status"], "daily_budget_exceeded")
        self.assertEqual(backend.calls, [])
        exceeded = [
            e for e in events.for_session(state.session_id)
            if e.event_type == "budget.exceeded"
            and e.payload.get("scope") == "daily_tokens"
        ]
        self.assertEqual(len(exceeded), 1)


class HealthcheckBRouteTests(unittest.TestCase):
    """Pins for the 2026-10-24 API-healthcheck B-route fixes (P0-B-D01,
    P1-B-D02..D06). Each fix is pinned on both sides where it has two:
    the bad input is blocked AND the legitimate input still works."""

    def test_second_opinion_view_omits_objective_but_keeps_task_info(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[case_finding("干咳为主", COUGH_EVIDENCE, "resp_cough_details")]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                kind="second_opinion",
                scope={"fact_keys": ["resp_cough_details"]},
                objective="主流程倾向急诊，请确认是否急诊",
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"])
        view = next(iter(backend.views.values()))
        # Tendency sentence must not reach the blinded child at all.
        self.assertNotIn("objective", view)
        self.assertNotIn(
            "主流程倾向急诊", json.dumps(view, ensure_ascii=False)
        )
        # Legitimate task info is still usable by the child.
        self.assertEqual(view["task_kind"], "second_opinion")
        self.assertIn("resp_cough_details", view["facts"])
        self.assertIn("content_excerpts", view)

    def test_second_opinion_view_strips_prior_disposition(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[case_finding("干咳为主", COUGH_EVIDENCE, "resp_cough_details")]
            )
        )
        state = make_state()
        state.runtime_snapshot["prior_case"] = {
            "chief_complaint": "上次咳嗽",
            "disposition_level": "emergency",
            "disposition_text": "建议急诊",
            "outcome": "recovered",
        }
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                kind="second_opinion",
                scope={"fact_keys": ["resp_cough_details"], "prior_case": True},
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"])
        view = next(iter(backend.views.values()))
        prior = view["prior_case"]
        self.assertEqual(prior["chief_complaint"], "上次咳嗽")
        self.assertEqual(prior["outcome"], "recovered")
        self.assertNotIn("disposition_level", prior)
        self.assertNotIn("disposition_text", prior)
        self.assertNotIn("emergency", json.dumps(view, ensure_ascii=False))

    def test_other_kinds_still_carry_objective_and_prior_disposition(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)]
            )
        )
        state = make_state()
        state.runtime_snapshot["prior_case"] = {
            "chief_complaint": "上次咳嗽",
            "disposition_level": "emergency",
            "disposition_text": "建议急诊",
        }
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                scope={
                    "fact_keys": ["chief_complaint"],
                    "prior_case": True,
                },
                objective="核对证据",
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"])
        view = next(iter(backend.views.values()))
        self.assertEqual(view["objective"], "核对证据")
        self.assertEqual(view["prior_case"]["disposition_level"], "emergency")

    def test_knowledge_quote_beyond_fed_excerpt_is_dropped_without_search(self):
        full = _content_index()["respiratory"]["text"]
        self.assertGreater(len(full), 1300)
        quote = full[1300:1330]
        self.assertTrue(quote)
        self.assertNotIn(quote, full[:1200])
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[
                    {
                        "claim": "内容库尾部提到了该方向",
                        "evidence": [
                            {
                                "source_type": "knowledge",
                                "quote": quote,
                                "ref": "respiratory",
                            }
                        ],
                        "confidence": "high",
                    }
                ]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(scope={"fact_keys": ["chief_complaint"]}),
            session_id=state.session_id,
        )
        branch = result["branches"][0]
        self.assertEqual(branch["findings"], [])
        self.assertEqual(branch["validation"]["dropped_claims"], 1)

    def test_knowledge_quote_from_view_excerpt_survives_without_search(self):
        excerpt = _content_index()["uri"]["text"][:1200]
        quote = excerpt[5:25]
        events = EventStore()
        backend = StubBackend(
            lambda sid, messages, cancelled: report(
                findings=[
                    {
                        "claim": "视图摘录收录了该方向条目",
                        "evidence": [
                            {
                                "source_type": "knowledge",
                                "quote": quote,
                                "ref": "uri",
                            }
                        ],
                        "confidence": "high",
                    }
                ]
            )
        )
        state = make_state()
        result = coordinator(backend, events).run_spawn(
            state,
            spawn_proposal(
                scope={"fact_keys": ["chief_complaint"], "hypotheses": ["uri"]}
            ),
            session_id=state.session_id,
        )
        branch = result["branches"][0]
        self.assertEqual(branch["status"], "completed")
        self.assertEqual(len(branch["findings"]), 1)

    def test_followup_draft_lint_catches_chinese_dosage_forms(self):
        for draft in (
            "请按时服用布洛芬200mg每日两次，注意休息。",
            "请每天吃2片药，观察症状变化。",
            "请每天服用500毫克维生素，注意休息。",
            "服用布洛芬200mg。",
        ):
            validation = {"notes": []}
            self.assertIsNone(
                SubagentCoordinator._validate_followup(
                    {"priority": "routine", "reason": "例行", "draft_message": draft},
                    validation,
                ),
                draft,
            )
        validation = {"notes": []}
        kept = SubagentCoordinator._validate_followup(
            {
                "priority": "routine",
                "reason": "例行",
                "draft_message": "您好，来问问您这两天的情况好些了吗？",
            },
            validation,
        )
        self.assertIsNotNone(kept)

    def test_followup_draft_lint_catches_emergency_instruction(self):
        validation = {"notes": []}
        self.assertIsNone(
            SubagentCoordinator._validate_followup(
                {
                    "priority": "routine",
                    "reason": "例行",
                    "draft_message": "情况加重请立即去急诊，不要等待。",
                },
                validation,
            )
        )
        # The pre-existing safety-net wording is NOT newly banned.
        validation = {"notes": []}
        kept = SubagentCoordinator._validate_followup(
            {
                "priority": "routine",
                "reason": "例行",
                "draft_message": "如果出现发烧、腰痛或尿血，请及时就医。",
            },
            validation,
        )
        self.assertIsNotNone(kept)

    def test_digest_timeline_drops_unanchored_event(self):
        events = EventStore()

        def handler(sid, messages, cancelled):
            return report(
                conclusion="",
                findings=[],
                digest={
                    "timeline": ["上周做过胃镜结果正常", "三天前开始咳嗽"],
                    "confirmed_facts": [
                        {"key": "chief_complaint", "quote": CHIEF_EVIDENCE},
                    ],
                    "contradictions": [],
                    "prior_outcomes": [],
                },
            )

        backend = StubBackend(handler)
        state = make_state()
        digest = coordinator(backend, events).run_case_digest(
            state, session_id=state.session_id
        )
        self.assertIsNotNone(digest)
        self.assertEqual(digest["timeline"], ["三天前开始咳嗽"])
        self.assertNotIn(
            "胃镜", json.dumps(digest, ensure_ascii=False)
        )


if __name__ == "__main__":
    unittest.main()
