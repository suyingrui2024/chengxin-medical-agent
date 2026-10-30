"""Mixin: context for AdaptiveConsultationEngine."""
from ._preamble import *  # noqa: F401,F403


class AdaptiveConsultationEngineContext:
    def _context(self, state: CaseState, text: str) -> Dict[str, Any]:
        chief = str(state.get("chief_complaint", ""))
        candidates = self._candidate_questions(state, chief)
        active_question = next(
            (q for q in self.content.questions if q.id == state.active_question),
            None,
        )
        recent = state.transcript[-16:]
        recent_user_texts = {
            item["content"] for item in recent if item["role"] == "user"
        }
        facts = {
            k: {
                name: value
                for name, value in v.to_dict().items()
                if name
                in {
                    "key",
                    "value",
                    "assertion",
                    "source",
                    "quality",
                    "evidence",
                    "structured_input",
                }
            }
            for k, v in state.facts.items()
        }
        earlier = {}
        source_turns = self._source_turns(state)
        for key, fact in state.facts.items():
            context = self._fact_provenance(fact, source_turns).get("context")
            if context and context["text"] not in recent_user_texts:
                facts[key]["source_context_id"] = context["turn_id"]
                earlier[context["turn_id"]] = context["text"]
        return {
            "latest_user_text": text,
            "active_question": state.active_question,
            "active_task": state.active_task,
            "self_check_conversation": self._task_conversation_context(state),
            "consultation_subject": subject_profile(state),
            "facts": facts,
            **(
                {
                    "earlier_source_turns": [
                        {"id": key, "text": value} for key, value in earlier.items()
                    ]
                }
                if earlier
                else {}
            ),
            # patient_goal is a blocking field like any other: the prompt
            # requires it to be recorded before review_summary, and
            # excluding it here let an abstained summary close with the
            # patient's goal never recorded (healthcheck P1-A-D1). The
            # controller auto-summary path only fires after the goal
            # answer itself lands, so it still sees an empty list there.
            "missing_required": [
                q.fact_key for q in candidates if q.blocking
            ],
            **(
                {"pathway_exploration": exploration}
                if (exploration := self._pathway_exploration(state))
                else {}
            ),
            "questions": [{"id": q.id, "field": q.fact_key} for q in candidates],
            **(
                {
                    "incidentally_recorded": sorted(
                        self._incidental_records.get(state.session_id, ())
                    )
                }
                if self._incidental_records.get(state.session_id)
                else {}
            ),
            **(
                {"active_question_field_already_recorded": active_question.fact_key}
                if active_question is not None
                and active_question.fact_key in state.facts
                else {}
            ),
            "available_self_checks": [
                {key: t[key] for key in ("id", "title", "result_key")}
                for t in self.content.applicable_tasks(chief)
                if t["result_key"] not in state.facts
                and not self._self_check_block_reason(state)
            ],
            "self_check_block_reason": self._self_check_block_reason(state),
            "requestable_self_checks": [
                {key: task[key] for key in ("id", "title", "result_key")}
                for task in self.content.applicable_tasks(chief)
                if task["result_key"] not in state.facts
            ],
            "pending_self_checks": state.pending_self_checks,
            "repeatable_self_checks": [
                {key: task[key] for key in ("id", "title", "result_key")}
                for task in self.content.applicable_tasks(chief)
                if task.get("repeatable") and task["result_key"] in state.facts
            ],
            "recent_observation_attempts": state.observation_attempts[-8:],
            "observation_completion": self._observation_completion_context(state),
            "observation_fields_read_only": sorted(
                {t["result_key"] for t in self.content.tasks.values()}
            ),
            "completed_observation_with_supplement": (
                state.observation_feedback
                if state.observation_feedback
                and state.observation_feedback.get("has_supplement", True)
                else None
            ),
            "calculation_request_with_supplement": self._calculation_followup_context(
                state
            ),
            "transcript": recent,
            "calculation_drafts": [
                {key: value for key, value in result.items() if key != "presentation"}
                for result in state.calculations[-3:]
            ],
            "patient_confirmed_report_items": [
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "reported_value",
                        "unit",
                        "reference_range",
                        "record_title",
                        "measured_at",
                        "source",
                    )
                }
                for item in state.report_items[-20:]
            ],
            # P2c: archive reference sits next to the confirmed report
            # items, gated by the same session switch. It is context
            # only and never lands in state.facts.
            **(
                {"profile_baseline": dict(state.profile_baseline)}
                if state.baseline_reference_enabled and state.profile_baseline
                else {}
            ),
            **(
                {"background_review": state.background_review["advice"]}
                if state.background_review
                and state.background_review.get("mode") == "advisory"
                and state.background_review.get("status") == "completed"
                and state.background_review.get("source_revision") == state.revision
                and isinstance(state.background_review.get("advice"), dict)
                else {}
            ),
            **(
                {"earlier_digest": state.subagent_digest["digest"]}
                if state.subagent_digest
                and state.subagent_digest.get("status") == "ready"
                and (state.subagent_digest.get("watermark") or {}).get("revision")
                == state.revision
                and isinstance(state.subagent_digest.get("digest"), dict)
                else {}
            ),
            **(
                {
                    "prior_case": {
                        key: prior.get(key)
                        for key in (
                            "chief_complaint",
                            "disposition_level",
                            "disposition_text",
                            "outcome",
                            "outcome_note",
                        )
                    }
                }
                if (
                    prior := (state.runtime_snapshot or {}).get("prior_case")
                )
                else {}
            ),
        }
