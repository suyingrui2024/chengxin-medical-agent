"""Mixin: validate for SubagentCoordinator."""
from ._preamble import *  # noqa: F401,F403


class CoordinatorValidate:
    def _validate_report(
        self,
        state: CaseState,
        branch: Dict[str, Any],
        report: Any,
        web_pool: Dict[str, str],
        session_id: str,
        spawn_id: str,
        knowledge_pool: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        kind = branch["view"]["task_kind"]
        result = self._branch_shell(branch, "failed")
        validation = result["validation"]
        if not isinstance(report, dict):
            validation["notes"].append("report_not_object")
            return result
        validation["schema_ok"] = True
        if kind == "second_opinion":
            # Enum-validated at report acceptance time in the child loop.
            result["disposition_tendency"] = report.get("disposition_tendency")

        # Case evidence pool: only facts inside this branch's projection.
        case_pool: Dict[str, List[str]] = {}
        for key, fact_view in branch["view"]["facts"].items():
            pool = []
            if fact_view.get("evidence"):
                pool.append(str(fact_view["evidence"]))
            if fact_view.get("value") is not None:
                pool.append(str(fact_view["value"]))
            case_pool[key] = pool
        # Knowledge quotes verify ONLY against excerpts this branch was
        # actually fed — the initial view's content_excerpts plus the
        # knowledge_search results returned during this run. Verifying
        # against entry full text let a child "wash" an assertion with a
        # passage it never saw (e.g. text past the 1200-char excerpt
        # cut, quoted with no search at all).
        fed_knowledge: Dict[str, str] = {}
        for item in branch["view"].get("content_excerpts", []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("ref"), str)
                and isinstance(item.get("excerpt"), str)
            ):
                fed_knowledge[item["ref"]] = item["excerpt"]
        if knowledge_pool:
            fed_knowledge.update(knowledge_pool)
        knowledge_pool = fed_knowledge

        def evidence_ok(item: Any) -> bool:
            if not isinstance(item, dict):
                return False
            quote = item.get("quote")
            ref = item.get("ref")
            source_type = item.get("source_type")
            if not isinstance(quote, str) or not quote or not isinstance(ref, str):
                return False
            if source_type == "case":
                return any(quote in part for part in case_pool.get(ref, []))
            if source_type == "knowledge":
                text = knowledge_pool.get(ref)
                return text is not None and quote in text
            if source_type == "web":
                # v1 keeps web off by default; a web quote verifies only
                # against full text this branch actually fetched.
                text = web_pool.get(ref)
                return text is not None and quote in text
            return False

        raw_findings = report.get("findings")
        if not isinstance(raw_findings, list):
            raw_findings = []
        kept: List[Dict[str, Any]] = []
        dropped = 0
        quotes_total = 0
        quotes_verified = 0
        for finding in raw_findings[:MAX_FINDINGS]:
            if not isinstance(finding, dict):
                dropped += 1
                continue
            claim = finding.get("claim")
            evidence = finding.get("evidence")
            if (
                not isinstance(claim, str)
                or not claim
                or len(claim) > MAX_CLAIM_CHARS
                or not isinstance(evidence, list)
                or not evidence
            ):
                dropped += 1
                quotes_total += len(evidence) if isinstance(evidence, list) else 0
                continue
            quotes_total += len(evidence)
            verified_here = sum(1 for item in evidence if evidence_ok(item))
            if _banned_hit(claim) or _DISPOSITION_PATTERN.search(claim):
                dropped += 1
                continue
            if verified_here != len(evidence):
                dropped += 1
                continue
            # Only surviving claims contribute to quotes_verified: a
            # dropped claim's verified quotes previously inflated the
            # metric (P2-B-D09) even though its evidence never survives.
            quotes_verified += verified_here
            kept.append(
                {
                    "claim": claim,
                    "evidence": evidence,
                    "confidence": finding.get("confidence")
                    if finding.get("confidence") in {"low", "medium", "high"}
                    else "low",
                    "provenance": "subagent",
                }
            )
        dropped += max(0, len(raw_findings) - MAX_FINDINGS)
        validation["quotes_total"] = quotes_total
        validation["quotes_verified"] = quotes_verified
        validation["dropped_claims"] = dropped

        conclusion = report.get("conclusion")
        conclusion = conclusion if isinstance(conclusion, str) else ""
        if len(conclusion) > MAX_CONCLUSION_CHARS:
            conclusion = conclusion[:MAX_CONCLUSION_CHARS]
            validation["notes"].append("conclusion_truncated")
        if conclusion and (
            _banned_hit(conclusion) or _DISPOSITION_PATTERN.search(conclusion)
        ):
            # A conclusion carrying banned/disposition phrasing is
            # dropped wholesale, never partially reworded.
            conclusion = ""
            dropped += 1
            validation["dropped_claims"] = dropped
            validation["notes"].append("conclusion_dropped_lint")

        uncertainty = report.get("uncertainty")
        if not isinstance(uncertainty, dict):
            uncertainty = {}
        level = uncertainty.get("level")
        if level not in {"low", "medium", "high"}:
            level = "high"
        reasons = uncertainty.get("reasons")
        reasons = [str(item)[:200] for item in reasons] if isinstance(reasons, list) else []
        if dropped > 0 and level == "low":
            level = "medium"  # uncertainty floor (step 5)
            reasons.append("dropped_claims_floor")

        total_claims = len(kept) + dropped
        status: Optional[str] = None
        if total_claims > 0 and dropped / total_claims > 0.5:
            status = "rejected_by_validation"  # drop-rate fuse (step 6)
            kept = []
        elif kind == "evidence_lookup" and quotes_verified == 0:
            status = "rejected_by_validation"  # no verified source (step 5)
            kept = []
        result["conclusion"] = conclusion
        result["findings"] = kept
        result["uncertainty"] = {"level": level, "reasons": reasons}
        sources: List[Dict[str, Any]] = []
        seen_sources = set()
        for finding in kept:
            for item in finding["evidence"]:
                key = (item["source_type"], item["ref"])
                if key in seen_sources:
                    continue
                seen_sources.add(key)
                source = {
                    "kind": {
                        "case": "fact_key",
                        "knowledge": "content_id",
                        "web": "url",
                    }[item["source_type"]],
                    "locator": item["ref"],
                }
                if item["source_type"] == "web":
                    source["retrieved_at"] = utc_now()
                sources.append(source)
        result["sources"] = sources

        if kind == "case_digest":
            result["digest"] = self._validate_digest(
                state,
                report.get("digest"),
                scope_keys=set(branch["scope_summary"]["fact_keys"]),
            )
            if status is None:
                status = (
                    "completed"
                    if result["digest"] is not None and dropped == 0
                    else "partial"
                    if result["digest"] is not None
                    else "failed"
                )
        elif kind == "followup_batch":
            result["followup"] = self._validate_followup(report, validation)
            if status is None:
                status = "completed" if dropped == 0 else "partial"
        else:
            if status is None:
                status = "completed" if dropped == 0 else "partial"
        result["status"] = status
        self.events.append(
            Event(
                session_id,
                "subagent.validated",
                {
                    "spawn_id": spawn_id,
                    "branch_id": branch["branch_id"],
                    "task_kind": kind,
                    "status": status,
                    "quotes_total": quotes_total,
                    "quotes_verified": quotes_verified,
                    "dropped_claims": dropped,
                    "followup_draft_dropped": (
                        kind == "followup_batch" and result["followup"] is None
                    ),
                },
            )
        )
        return result

    def _validate_digest(
        self,
        state: CaseState,
        digest: Any,
        scope_keys: Optional[set] = None,
    ) -> Optional[Dict[str, Any]]:
        if not isinstance(digest, dict):
            return None

        def clean(items: Any) -> List[str]:
            if not isinstance(items, list):
                return []
            output = []
            for item in items:
                if not isinstance(item, str):
                    continue
                text = " ".join(item.split())
                if not text or len(text) > MAX_CLAIM_CHARS:
                    continue
                if _banned_hit(text) or _DISPOSITION_PATTERN.search(text):
                    continue
                if text not in output:
                    output.append(text)
            return output[:20]

        transcript_pool = "\n".join(
            item.get("content", "")
            for item in state.transcript
            if item.get("role") == "user"
        )
        # Anchor pool for timeline items: fact evidence/values plus the
        # patient's own turns. A timeline item is kept only if it shares
        # at least one 2-character substring with this pool — a
        # paraphrase of something actually said/recorded ("主诉感冒一周
        # 未愈" vs "感冒一周了还没好") survives, while an event that
        # appears nowhere in the case ("上周做过胃镜结果正常") is
        # dropped instead of entering the digest as verified content.
        # This is a lexical anchor, not a semantic proof: an invented
        # item sharing a common bigram with the transcript could still
        # pass, and such items must not be read as quote-verified.
        anchor_pool = [
            str(part)
            for fact in state.facts.values()
            for part in (fact.evidence or "", fact.value)
            if part is not None and str(part)
        ] + [
            item.get("content", "")
            for item in state.transcript
            if item.get("role") == "user"
        ]

        def anchored(text: str) -> bool:
            for pool_text in anchor_pool:
                if not pool_text:
                    continue
                if text in pool_text or pool_text in text:
                    return True
                bigrams = {text[i : i + 2] for i in range(len(text) - 1)}
                if any(bigram in pool_text for bigram in bigrams):
                    return True
            return False
        confirmed: List[Dict[str, str]] = []
        raw_confirmed = digest.get("confirmed_facts")
        if isinstance(raw_confirmed, list):
            for item in raw_confirmed:
                if not isinstance(item, dict):
                    continue
                key = item.get("key")
                quote = item.get("quote")
                fact = state.facts.get(key) if isinstance(key, str) else None
                if fact is None or not isinstance(quote, str) or not quote:
                    continue
                if scope_keys is not None and key not in scope_keys:
                    # Branch scope is the task boundary (P2-B-D08): an
                    # out-of-scope fact, however well quoted, must not
                    # enter the digest — it is skipped outright, so it
                    # counts neither as a pass nor as a failure.
                    continue
                pool = [part for part in (fact.evidence or "",) if part]
                if fact.value is not None:
                    pool.append(str(fact.value))
                if any(quote in part for part in pool) or quote in transcript_pool:
                    confirmed.append({"key": key, "quote": quote})
        validated = {
            "timeline": [
                item for item in clean(digest.get("timeline")) if anchored(item)
            ],
            "confirmed_facts": confirmed,
            "contradictions": clean(digest.get("contradictions")),
            "prior_outcomes": clean(digest.get("prior_outcomes")),
        }
        if not confirmed and not validated["timeline"]:
            return None
        return validated

    @staticmethod
    def _validate_followup(
        report: Dict[str, Any], validation: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        priority = report.get("priority")
        if priority not in {"high", "routine"}:
            priority = "routine"
        reason = report.get("reason")
        reason = reason if isinstance(reason, str) else ""
        draft = report.get("draft_message")
        if not isinstance(draft, str) or not draft.strip():
            validation["notes"].append("followup_draft_missing")
            return None
        draft = draft.strip()
        if (
            len(draft) > MAX_CLAIM_CHARS
            or _banned_hit(draft)
            or _DISPOSITION_PATTERN.search(draft)
            or _DOSAGE_PATTERN.search(draft)
        ):
            validation["notes"].append("followup_draft_lint")
            return None
        return {
            "priority": priority,
            "reason": reason[:MAX_CLAIM_CHARS],
            "draft_message": draft,
        }
