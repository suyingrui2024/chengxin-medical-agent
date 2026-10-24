"""Per-kind subagent mode overrides (lane A: coordinator + env layer).

Each task kind may run at its own mode while the rest follow the global
MEDAGENT_SUBAGENT_MODE. The coordinator only *gates* on the effective
mode; shadow vs advisory surfacing stays with the consumer.
"""
import tempfile
import unittest
from pathlib import Path

from medagent.domain import Assertion, CaseState, Fact, Source
from medagent.env_config import read_env, subagent_kind_modes
from medagent.events import EventStore
from medagent.reasoning import Hypothesis, ReasoningWorkspace
from medagent.subagents import KINDS, SubagentCoordinator


CHIEF_EVIDENCE = "我咳嗽三天了，还有点嗓子疼"


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
    ]
    return state


def report(findings=None, **extra):
    arguments = {
        "conclusion": "小结",
        "findings": findings or [],
        "uncertainty": {"level": "low", "reasons": []},
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
    name = "stub"

    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def decide_cancellable(self, session_id, messages, cancelled):
        self.calls.append(session_id)
        return self.handler(session_id, messages, cancelled)


def spawn_proposal(kind, scope=None):
    branch = {"branch_id": "b1", "objective": "核对", "scope": scope or {}}
    return {"task_kind": kind, "objective": "测试", "branches": [branch]}


def second_opinion_backend():
    return StubBackend(
        lambda sid, m, c: report(
            findings=[case_finding("咳嗽已持续三天", CHIEF_EVIDENCE)],
            disposition_tendency="routine_visit",
        )
    )


class ModeForTests(unittest.TestCase):
    def test_override_wins_and_unknown_kind_falls_back_to_global(self):
        coord = SubagentCoordinator(
            EventStore(),
            object(),
            mode="off",
            kind_modes={"second_opinion": "advisory"},
        )
        self.assertEqual(coord.mode_for("second_opinion"), "advisory")
        self.assertEqual(coord.mode_for("evidence_lookup"), "off")
        self.assertEqual(coord.mode_for("case_digest"), "off")
        self.assertEqual(coord.mode_for("followup_batch"), "off")
        # Unknown kinds and malformed values fall back to the global mode.
        self.assertEqual(coord.mode_for("nope"), "off")
        self.assertEqual(coord.mode_for(None), "off")

    def test_override_can_lower_a_kind_below_global(self):
        coord = SubagentCoordinator(
            EventStore(),
            object(),
            mode="advisory",
            kind_modes={"evidence_lookup": "off"},
        )
        self.assertEqual(coord.mode_for("evidence_lookup"), "off")
        self.assertEqual(coord.mode_for("second_opinion"), "advisory")

    def test_constructor_rejects_bad_kind_modes(self):
        with self.assertRaises(ValueError):
            SubagentCoordinator(
                EventStore(), object(), kind_modes={"nope": "advisory"}
            )
        with self.assertRaises(ValueError):
            SubagentCoordinator(
                EventStore(), object(), kind_modes={"case_digest": "loud"}
            )


class PerKindSpawnGatingTests(unittest.TestCase):
    def test_global_off_second_opinion_advisory_runs_others_stay_disabled(self):
        events = EventStore()
        backend = second_opinion_backend()
        coord = SubagentCoordinator(
            events,
            backend,
            mode="off",
            kind_modes={"second_opinion": "advisory"},
        )
        state = make_state()
        result = coord.run_spawn(
            state,
            spawn_proposal(
                "second_opinion", scope={"fact_keys": ["chief_complaint"]}
            ),
            session_id=state.session_id,
        )
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["mode"], "advisory")
        self.assertEqual(result["branches"][0]["status"], "completed")
        self.assertEqual(len(backend.calls), 1)
        # Events carry the kind's effective mode, not the global one.
        proposed = [
            e
            for e in events.for_session(state.session_id)
            if e.event_type == "subagent.proposed"
        ]
        self.assertEqual(len(proposed), 1)
        self.assertEqual(proposed[0].payload["mode"], "advisory")

        denied = coord.run_spawn(
            state,
            spawn_proposal("evidence_lookup"),
            session_id=state.session_id,
        )
        self.assertEqual(denied, {"ok": False, "status": "disabled"})
        self.assertEqual(len(backend.calls), 1)

    def test_kind_override_off_blocks_under_global_advisory(self):
        events = EventStore()
        backend = second_opinion_backend()
        coord = SubagentCoordinator(
            events,
            backend,
            mode="advisory",
            kind_modes={"second_opinion": "off"},
        )
        state = make_state()
        result = coord.run_spawn(
            state,
            spawn_proposal(
                "second_opinion", scope={"fact_keys": ["chief_complaint"]}
            ),
            session_id=state.session_id,
        )
        self.assertEqual(result, {"ok": False, "status": "disabled"})
        self.assertEqual(backend.calls, [])

    def test_case_digest_entry_follows_its_own_mode(self):
        events = EventStore()
        backend = StubBackend(
            lambda sid, m, c: report(
                conclusion="",
                digest={
                    "timeline": ["三天前开始咳嗽"],
                    "confirmed_facts": [
                        {"key": "chief_complaint", "quote": CHIEF_EVIDENCE}
                    ],
                    "contradictions": [],
                    "prior_outcomes": [],
                },
            )
        )
        off_coord = SubagentCoordinator(
            events, backend, mode="advisory", kind_modes={"case_digest": "off"}
        )
        state = make_state()
        self.assertIsNone(
            off_coord.run_case_digest(state, session_id=state.session_id)
        )
        self.assertEqual(backend.calls, [])
        on_coord = SubagentCoordinator(
            events, backend, mode="off", kind_modes={"case_digest": "advisory"}
        )
        digest = on_coord.run_case_digest(state, session_id=state.session_id)
        self.assertIsNotNone(digest)
        self.assertEqual(digest["timeline"], ["三天前开始咳嗽"])


class SetKindModesTests(unittest.TestCase):
    def test_merge_and_event_payload_carry_effective_modes(self):
        events = EventStore()
        coord = SubagentCoordinator(events, object(), mode="shadow")
        effective = coord.set_kind_modes({"second_opinion": "advisory"})
        expected = {
            "evidence_lookup": "shadow",
            "second_opinion": "advisory",
            "case_digest": "shadow",
            "followup_batch": "shadow",
        }
        self.assertEqual(effective, expected)
        self.assertEqual(coord.mode_for("second_opinion"), "advisory")
        changed = [
            e for e in events.for_session("") if e.event_type == "subagent.modes_changed"
        ]
        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0].payload, {"kinds": expected})
        # A second call merges rather than replacing the table.
        coord.set_kind_modes({"evidence_lookup": "off"})
        self.assertEqual(coord.mode_for("second_opinion"), "advisory")
        self.assertEqual(coord.mode_for("evidence_lookup"), "off")

    def test_invalid_mapping_raises_and_merges_nothing(self):
        events = EventStore()
        coord = SubagentCoordinator(events, object(), mode="off")
        with self.assertRaises(ValueError):
            coord.set_kind_modes({"nope": "advisory"})
        with self.assertRaises(ValueError):
            coord.set_kind_modes({"second_opinion": "loud"})
        with self.assertRaises(ValueError):
            coord.set_kind_modes(
                {"second_opinion": "advisory", "case_digest": "loud"}
            )
        # Validation happens before any merge: the valid entry above
        # must not have leaked in, and no event was recorded.
        self.assertEqual(coord.kind_modes, {})
        self.assertEqual(
            [
                e
                for e in events.for_session("")
                if e.event_type == "subagent.modes_changed"
            ],
            [],
        )


class FollowupBatchKindModeTests(unittest.TestCase):
    def _items_states(self):
        state = make_state("fu-a")
        state.follow_up = {
            "kind": "routine_checkin",
            "status": "scheduled",
            "due_at": "2026-10-24T09:00:00+00:00",
            "reason": "例行回访确认恢复情况",
            "outcome": None,
        }
        items = [
            {
                "session_id": "fu-a",
                "title": "咳嗽复查",
                "plan": dict(state.follow_up),
                "risk": "routine",
            }
        ]
        return items, {"fu-a": state}

    def _backend(self):
        return StubBackend(
            lambda sid, m, c: report(
                findings=[],
                priority="high",
                reason="上次结论提示需要确认恢复情况",
                draft_message="您好，想了解一下您这几天咳嗽恢复得怎么样了？",
            )
        )

    def test_followup_batch_off_alone_returns_empty_under_global_advisory(self):
        items, states = self._items_states()
        backend = self._backend()
        coord = SubagentCoordinator(
            EventStore(),
            backend,
            mode="advisory",
            kind_modes={"followup_batch": "off"},
        )
        self.assertEqual(coord.run_followup_batch(items, states), [])
        self.assertEqual(backend.calls, [])

    def test_followup_batch_advisory_alone_runs_under_global_off(self):
        items, states = self._items_states()
        backend = self._backend()
        coord = SubagentCoordinator(
            EventStore(),
            backend,
            mode="off",
            kind_modes={"followup_batch": "advisory"},
        )
        output = coord.run_followup_batch(items, states)
        self.assertEqual(len(output), 1)
        self.assertEqual(output[0]["session_id"], "fu-a")
        self.assertIn("咳嗽恢复", output[0]["draft_message"])


class EnvKindModesTests(unittest.TestCase):
    def test_helper_returns_only_explicit_valid_overrides(self):
        source = {
            "MEDAGENT_SUBAGENT_MODE": "shadow",
            "MEDAGENT_SUBAGENT_MODE_SECOND_OPINION": "advisory",
            "MEDAGENT_SUBAGENT_MODE_CASE_DIGEST": "off",
            "UNRELATED": "x",
        }
        self.assertEqual(
            subagent_kind_modes(source),
            {"second_opinion": "advisory", "case_digest": "off"},
        )
        self.assertEqual(subagent_kind_modes({}), {})

    def test_helper_covers_all_four_kinds(self):
        source = {
            "MEDAGENT_SUBAGENT_MODE_CASE_DIGEST": "shadow",
            "MEDAGENT_SUBAGENT_MODE_EVIDENCE_LOOKUP": "advisory",
            "MEDAGENT_SUBAGENT_MODE_SECOND_OPINION": "advisory",
            "MEDAGENT_SUBAGENT_MODE_FOLLOWUP_BATCH": "off",
        }
        self.assertEqual(set(subagent_kind_modes(source)), set(KINDS))

    def test_helper_rejects_invalid_value_and_names_the_key(self):
        with self.assertRaises(ValueError) as ctx:
            subagent_kind_modes({"MEDAGENT_SUBAGENT_MODE_SECOND_OPINION": "loud"})
        self.assertIn("MEDAGENT_SUBAGENT_MODE_SECOND_OPINION", str(ctx.exception))

    def test_read_env_validates_kind_keys(self):
        base = [
            "MEDAGENT_LLM_BASE_URL=https://api.deepseek.com/anthropic",
            "MEDAGENT_LLM_API_KEY=test-key",
            "MEDAGENT_LLM_MODEL=deepseek-flash",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.env"
            good.write_text(
                "\n".join(base + ["MEDAGENT_SUBAGENT_MODE_EVIDENCE_LOOKUP=shadow"]),
                encoding="utf-8",
            )
            settings = read_env(good)
            self.assertEqual(
                settings["MEDAGENT_SUBAGENT_MODE_EVIDENCE_LOOKUP"], "shadow"
            )
            self.assertEqual(
                subagent_kind_modes(settings), {"evidence_lookup": "shadow"}
            )
            bad = Path(tmp) / "bad.env"
            bad.write_text(
                "\n".join(base + ["MEDAGENT_SUBAGENT_MODE_FOLLOWUP_BATCH=loud"]),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError) as ctx:
                read_env(bad)
            self.assertIn("MEDAGENT_SUBAGENT_MODE_FOLLOWUP_BATCH", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
