#!/usr/bin/env python3
"""Function-size guard.

Why this exists
---------------
The package, layer, naming, export and test-tree work is done and gated.  What
was left was the inside of the functions: 1218 of them with a 15-line median,
and 59 that exceed 100 lines or 25 branches while holding a quarter of the
function-body code.  Reading the project means reading those, and they are
where the reading stops.

A ceiling alone would have failed on day one and been deleted.  So this guard
freezes today's outliers instead: a baselined function may shrink but not grow,
and a function that is not baselined must stay under the ceiling.  The number
can only go down.

Rules
-----
* Every function under ``src/medagent`` is measured for two things: the number
  of source lines it spans, and its branch count -- see :func:`branch_count`
  for exactly what is counted, because "how complex is this" is the kind of
  number that gets argued about.
* A function over either ceiling fails unless ``BASELINE`` records it.
* A baselined function fails if it grew past its recorded size.
* A baselined function that no longer exists is reported, because a rename
  silently turns an old debt entry into a free pass for the new name.
* A baselined function that has come back under both ceilings is reported as
  ready to be struck off.

Run locally::

    python scripts/lint/function_size.py            # check
    python scripts/lint/function_size.py --freeze   # print current outliers
"""
from __future__ import annotations

import ast
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCAN_ROOT = ROOT / "src" / "medagent"

#: Lines a function body may span.
LINE_CEILING = 60
#: Branches it may contain.
BRANCH_CEILING = 20

#: ``module::Qualified.name`` -> (lines, branches) as measured when the guard
#: was introduced.  Existing debt only: an entry may be lowered as the function
#: is split, and removed once both numbers are under the ceilings.  Longest
#: first, because that is the order in which they are worth opening.
BASELINE: dict[str, tuple[int, int]] = {
    "medagent.consultation.agent.run::AdaptiveConsultationEngineRun._run": (291, 37),
    "medagent.conclusion.adjustments::compose_conclusion": (297, 50),
    "medagent.execution.changes::capture": (295, 7),
    "medagent.subagents.coordinator.branch::CoordinatorBranch._run_spawn": (243, 29),
    "medagent.subagents.coordinator.validate::CoordinatorValidate._validate_report": (88, 17),
    "medagent.execution.session::ExecutionSession._read": (212, 70),
    "medagent.subagents.coordinator.branch::CoordinatorBranch._run_branch": (207, 20),
    "medagent.infra.agent_tools::_validate_item": (190, 55),
    "medagent.core.measurement_kinds::saveable_from_observation": (187, 21),
    "medagent.patient_records.store.schema::StoreSchema._initialize": (183, 9),
    "medagent.consultation.agent.context::AdaptiveConsultationEngineContext._context": (178, 40),
    "medagent.reports.bridging::build_bridging_sentence": (176, 51),
    "medagent.dialogue.observations::ObservationMixin._handle_observation_action": (165, 34),
    "medagent.patient_records.store.queries::StoreQueries.measurement_trend": (157, 12),
    "medagent.execution.session::ExecutionSession.__init__": (156, 30),
    "medagent.execution.files::directory_page": (150, 32),
    "medagent.server.patient::HandlerPatient._patient_get": (150, 32),
    "medagent.consultation.agent.submit::AdaptiveConsultationEngineSubmit.correct_fact": (133, 25),
    "medagent.execution.changes::capture.walk": (147, 36),
    "medagent.core.presentation::reply_presentation": (143, 25),
    "medagent.extensions.registry::CapabilityRegistry.__init__": (142, 44),
    "medagent.dialogue.task_conversation::TaskConversationMixin._propose_task_result": (136, 29),
    "medagent.execution.observations::validate_observation": (131, 49),
    "medagent.execution.store::WorkspaceStore.validate": (128, 62),
    "medagent.server.agent::HandlerAgent._post_session_attach_records": (127, 18),
    "medagent.consultation.agent.core::AdaptiveConsultationEngineCore.__init__": (126, 16),
    "medagent.reports.report_interpretation::build_interpretation": (124, 29),
    "medagent.server.consultation::HandlerConsultation._do_PATCH": (124, 22),
    "medagent.clinical.knowledge::ContentRepository.question_applies_in_state": (123, 40),
    "medagent.consultation.subject::ConsultationEngineSubject.restore_fact": (123, 21),
    "medagent.consultation.agent.execute::AdaptiveConsultationEngineExecute._tool_ask_patient": (121, 22),
    "medagent.dialogue.adaptive_questions::validate_adaptive_question": (116, 44),
    "medagent.infra.backends::CompatibleBackend._decide": (113, 22),
    "medagent.consultation.calculation::ConsultationEngineCalculation._handle_calculation_action": (111, 31),
    "medagent.server.consultation::HandlerConsultation._do_GET": (111, 30),
    "medagent.infra.backends::DshBackend._bridge_decision": (111, 19),
    "medagent.subagents.coordinator.branch::CoordinatorBranch._prepare_branch": (110, 23),
    "medagent.reports.handoff::handoff_lines": (108, 53),
    "medagent.consultation.closing_flow::ConsultationEngineClosing._record_reply": (108, 18),
    "medagent.execution.changes::ChangeTracker.load": (107, 31),
    "medagent.patient_records.store.assets::StoreAssets.finish_recognition": (107, 12),
    "medagent.server.patient::HandlerPatient._post_patient_measurement_from_observation": (106, 21),
    "medagent.infra.medical_images::DeepSeekVisionRecognizer.recognize": (106, 10),
    "medagent.consultation.planning::ConsultationEnginePlanning._accept_task_result": (105, 15),
    "medagent.infra.medical_images::validate_recognition": (104, 34),
    "medagent.reports.handoff::_conclusion_section": (103, 22),
    "medagent.consultation.agent.execute::AdaptiveConsultationEngineExecute._tool_review_summary": (101, 24),
    "medagent.consultation.closing::render_confirmed_close": (101, 15),
    "medagent.consultation.closing_flow::ConsultationEngineClosing._summary_payload": (100, 19),
    "medagent.consultation.agent.execute::AdaptiveConsultationEngineExecute._tool_spawn_subagent": (100, 18),
    "medagent.consultation.subject::ConsultationEngineSubject.correct_fact": (99, 19),
    "medagent.extensions.scale_engine::_validate_items": (98, 34),
    "medagent.conclusion.support::_select_directions": (96, 13),
    "medagent.execution.checkpoints::_capture_paths": (95, 25),
    "medagent.subagents.coordinator.validate::CoordinatorValidate._validate_digest": (94, 24),
    "medagent.review.background_review::BackgroundReviewCoordinator._finish_success": (94, 13),
    "medagent.extensions.scale_engine::run_scale": (92, 17),
    "medagent.execution.clarifications::validate_clarification": (89, 37),
    "medagent.subagents.coordinator.branch::CoordinatorBranch._build_view": (88, 21),
    "medagent.consultation.planning::ConsultationEnginePlanning._sync_task_requests": (88, 16),
    "medagent.patient_records.store.assets::StoreAssets.revise_observation": (87, 9),
    "medagent.execution.session::ExecutionSession.start_turn": (86, 15),
    "medagent.consultation.agent.pathway::AdaptiveConsultationEnginePathway._controller_summary": (86, 11),
    "medagent.execution.session::ExecutionSession.artifact_overview": (85, 3),
    "medagent.consultation.subject::ConsultationEngineSubject._apply_fact_update_dependencies": (84, 19),
    "medagent.consultation.agent.execute::AdaptiveConsultationEngineExecute._guard_trajectory_wording": (84, 8),
    "medagent.reports.patient_calculations::request_draft": (83, 20),
    "medagent.execution.session::ExecutionSession.persist": (82, 19),
    "medagent.infra.platform_fs::WindowsOS.open": (82, 17),
    "medagent.consultation.closing::_append_unaddressed_goals": (82, 11),
    "medagent.reports.measurement_reports::temperature_report": (79, 33),
    "medagent.conclusion.support::_adjust_assertion_wording": (78, 21),
    "medagent.patient_records.store.assets::StoreAssets.add_measurement": (78, 5),
    "medagent.guards.baseline_projection::build_baseline_projection": (77, 24),
    "medagent.server.workspace::Workspace.subagent_metrics": (77, 22),
    "medagent.core.measurement_kinds::normalize_measurement": (76, 18),
    "medagent.consultation.planning::ConsultationEnginePlanning._offer_task": (76, 14),
    "medagent.execution.session::ExecutionSession._start": (75, 13),
    "medagent.desktop_service::main": (75, 5),
    "medagent.patient_records.store.queries::StoreQueries.glucose_trend": (75, 5),
    "medagent.reports.calculation_followup::simple_conversion_request": (75, 4),
    "medagent.execution.artifacts::dimensions": (74, 36),
    "medagent.clinical.reasoning::RuleBasedProposer.propose": (74, 17),
    "medagent.reports.education::EducationEnricher._enrich": (73, 19),
    "medagent.extensions.glucose::glucose_evidence": (73, 17),
    "medagent.execution.capabilities::validate_receipt": (71, 22),
    "medagent.conclusion.disposition::format_window_text": (71, 19),
    "medagent.consultation.planning::ConsultationEnginePlanning._plan_next": (71, 8),
    "medagent.infra.env_config::read_env": (70, 22),
    "medagent.extensions.scale_forms::scale_form_spec": (70, 21),
    "medagent.clinical.reasoning::DifferentialPlanner.plan": (70, 18),
    "medagent.infra.web_tools::WebTools._run_search": (70, 14),
    "medagent.consultation.emergency::ConsultationEngineEmergency._emergency_reply": (69, 7),
    "medagent.consultation.planning::ConsultationEnginePlanning._undetermined_close_discriminator": (68, 19),
    "medagent.server.agent::HandlerAgent._execution_get": (68, 18),
    "medagent.server.workspace::Workspace.maybe_schedule_followup_drafts": (68, 15),
    "medagent.consultation.agent.execute::AdaptiveConsultationEngineExecute._tool_clarify": (67, 21),
    "medagent.conclusion.narrative::_build_worry": (67, 17),
    "medagent.subagents.coordinator.branch::CoordinatorBranch._execute_tool": (67, 10),
    "medagent.clinical.interpreter::Interpreter.interpret": (66, 29),
    "medagent.conclusion.narrative::_narrative_reasoning": (66, 21),
    "medagent.server.agent::HandlerAgent._execution_post": (66, 21),
    "medagent.execution.changes::capture.previews": (66, 14),
    "medagent.consultation.agent.pathway::AdaptiveConsultationEnginePathway._pathway_exploration": (66, 12),
    "medagent.execution.session::ExecutionSession.snapshot": (66, 11),
    "medagent.reports.education::EducationEnricher._run_review": (66, 10),
    "medagent.conclusion.disposition::spoken_close_line": (65, 19),
    "medagent.clinical.hypothesis_ranking::HypothesisRanker.order": (65, 15),
    "medagent.clinical.question_ordering::HypothesisOrdering.order": (65, 15),
    "medagent.core.domain::CaseState.to_dict": (65, 14),
    "medagent.patient_records.store.assets::StoreAssets.create_image_entry": (65, 7),
    "medagent.patient_records.validation::validate_baseline_patch": (64, 23),
    "medagent.conclusion.goals::_visit_part_residual": (64, 17),
    "medagent.dialogue.task_conversation::TaskConversationMixin._resume_task_conversation": (64, 14),
    "medagent.consultation.closing_flow::ConsultationEngineClosing._attach_conclusion": (64, 7),
    "medagent.reports.education::EducationEnricher.schedule": (63, 27),
    "medagent.execution._preamble::safe_bytes": (61, 14),
    "medagent.consultation.handlers::ConsultationEngineHandlers._h_intake": (61, 7),
    "medagent.server.workspace::Workspace.__init__": (61, 2),
    "medagent.extensions.formula_forms::formula_form_spec": (58, 22),
    "medagent.dialogue.adaptive_questions::project_selected_answer": (57, 28),
    "medagent.dialogue.location_answers::validate_draft_evidence": (55, 24),
    "medagent.clinical.triage::TriageService.assess": (55, 21),
    "medagent.conclusion.adjustments::_build_visit_notes": (50, 24),
    "medagent.reports.publication::ReplyPublicationGate.validate": (43, 25),
    "medagent.clinical.reasoning::_condition_quote": (38, 23),
}


def branch_count(node: ast.AST) -> int:
    """Count the decisions in a function.

    Every construct here is a place a reader has to hold two states at once:
    ``if``/``for``/``while``/``try``/``with`` and their async forms, the
    conditional expression, a short-circuiting ``and``/``or``, and one entry per
    ``for`` clause in a comprehension.  ``elif`` counts once, because the AST
    stores it as a nested ``If`` in the ``orelse`` slot only when there is an
    ``else``; a plain ``elif`` chain is a flat list of tests and each test is a
    branch in the sense that matters.

    It is deliberately a syntactic count.  Anything that tries to be cleverer
    becomes a number people argue with instead of a number they lower.
    """
    kinds = (
        ast.If,
        ast.For,
        ast.AsyncFor,
        ast.While,
        ast.Try,
        ast.With,
        ast.AsyncWith,
        ast.IfExp,
        ast.BoolOp,
        ast.comprehension,
    )
    nested = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)

    total = 0
    stack = list(ast.iter_child_nodes(node))
    while stack:
        child = stack.pop()
        if isinstance(child, nested):
            # A closure is measured separately; counting its branches here too
            # would make an outer function look complex because of code the
            # reader does not meet until they open the inner one.
            continue
        if isinstance(child, kinds):
            total += 1
        stack.extend(ast.iter_child_nodes(child))
    return total


def module_name(path: pathlib.Path, scan_root: pathlib.Path | None = None) -> str:
    """``src/medagent/a/b.py`` -> ``medagent.a.b``.

    The package prefix comes from the directory name, so a throwaway tree in a
    test produces the same keys as the real one.
    """
    relative = path.relative_to((scan_root or SCAN_ROOT).parent).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def functions(path: pathlib.Path) -> list[tuple[str, int, int, int]]:
    """Every function in one file as ``(qualified name, lines, branches, exits)``.

    Classes and enclosing functions contribute to the qualified name so that
    the eight classes in this tree with their own ``__init__`` do not collide.
    """
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:  # pragma: no cover - would fail ruff and pytest
        raise SystemExit(f"{path}: cannot parse: {exc}") from exc

    found: list[tuple[str, int, int, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                span = (child.end_lineno or child.lineno) - child.lineno + 1
                exits = sum(isinstance(n, ast.Return) for n in ast.walk(child))
                found.append((f"{prefix}{child.name}", span, branch_count(child), exits))
                # Nested functions are measured on their own as well: a long
                # closure buried in a long function is still a long closure.
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    return found


def measure(root: pathlib.Path | None = None) -> dict[str, tuple[int, int]]:
    """Every function under ``root``, keyed by ``module::Qualified.name``.

    Two definitions with the same qualified name are a failure, not a merge.
    The second one wins at import time and the first becomes dead code nobody
    can see -- ``department_formulas`` carried an exact copy of ``_enum`` seven
    hundred lines below the original.  ruff does not report it, because F811
    only fires when the shadowed name was never used, and this one was.
    """
    scan_root = root or SCAN_ROOT
    sizes: dict[str, tuple[int, int]] = {}
    duplicates: list[str] = []
    for path in sorted(scan_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        module = module_name(path, scan_root)
        for name, span, branches, _exits in functions(path):
            key = f"{module}::{name}"
            if key in sizes:
                duplicates.append(key)
            sizes[key] = (span, branches)
    if duplicates:
        raise SystemExit(
            "two definitions share a name and the first is dead code:\n"
            + "\n".join(f"  - {key}" for key in duplicates)
        )
    return sizes


def check(
    sizes: dict[str, tuple[int, int]],
    baseline: dict[str, tuple[int, int]],
) -> tuple[list[str], list[str]]:
    """Apply the ceilings. Returns ``(failures, warnings)``.

    A baseline entry is a snapshot of one function's size, not a licence to sit
    there.  So there are three outcomes besides "fine": it grew (failure); it
    came back under the ceilings (strike it off); or it shrank but is still
    over (lower the entry).  Without that last one, a function could halve and
    keep the old ceiling as headroom to grow back into — the record would
    describe a function that no longer exists.
    """
    failures: list[str] = []
    warnings: list[str] = []

    for key, (span, branches) in sorted(sizes.items()):
        recorded = baseline.get(key)
        if recorded is None:
            if is_over((span, branches)):
                failures.append(
                    f"{key}: {span} lines / {branches} branches exceeds "
                    f"{LINE_CEILING}/{BRANCH_CEILING}; split it"
                )
            continue

        if span > recorded[0] or branches > recorded[1]:
            failures.append(
                f"{key}: {span} lines / {branches} branches grew past its "
                f"baselined {recorded[0]}/{recorded[1]}"
            )
        elif not is_over((span, branches)):
            warnings.append(
                f"{key}: now {span} lines / {branches} branches -- under the "
                "ceilings, strike it off"
            )
        elif (span, branches) != recorded:
            warnings.append(
                f"{key}: now {span} lines / {branches} branches, down from "
                f"{recorded[0]}/{recorded[1]} -- lower the entry"
            )

    for key, recorded in sorted(baseline.items()):
        if key not in sizes:
            warnings.append(
                f"{key}: baselined at {recorded[0]}/{recorded[1]} but no longer "
                "exists -- renamed or removed? Update the entry."
            )

    return failures, warnings


def is_over(size: tuple[int, int]) -> bool:
    return size[0] > LINE_CEILING or size[1] > BRANCH_CEILING


def freeze(sizes: dict[str, tuple[int, int]]) -> None:
    """Print the offending functions as BASELINE entries, longest first."""
    rows = [(key, size) for key, size in sizes.items() if is_over(size)]
    rows.sort(key=lambda row: (-row[1][0], -row[1][1], row[0]))
    print(f"# {len(rows)} functions over {LINE_CEILING} lines or {BRANCH_CEILING} branches")
    for key, (span, branches) in rows:
        print(f'    "{key}": ({span}, {branches}),')


def main(argv: list[str]) -> int:
    sizes = measure()

    if "--freeze" in argv:
        freeze(sizes)
        return 0

    failures, warnings = check(sizes, BASELINE)

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    if failures:
        print("function-size check FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        print(
            f"\n{len(failures)} function(s) over the "
            f"{LINE_CEILING}-line / {BRANCH_CEILING}-branch ceiling.  Extract "
            "named steps; do not raise the ceiling.",
            file=sys.stderr,
        )
        return 1

    over = sum(1 for size in sizes.values() if is_over(size))
    print(
        f"function-size check passed: {len(sizes)} functions scanned, "
        f"{len(BASELINE)} grandfathered, {over} still over the ceiling."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
