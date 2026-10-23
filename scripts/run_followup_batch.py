#!/usr/bin/env python3
"""Offline follow-up batch drafting (design: docs/联网与子智能体工具设计.md §5.7).

Assembles the same runtime + workspace as the server, collects follow-up
plans that are due and still "scheduled", and asks the SubagentCoordinator
(one child per case) to rank them and draft a check-in message per case.

The design suggests triggering the batch from the UI once >=3 cases are
due; this offline entry point deliberately runs with >=1 due case so a
single overdue follow-up is never held back by the batch threshold.

Drafts are written only to the workspace's in-memory follow_up_drafts and
printed to stdout. Nothing is sent anywhere: a human confirms/edits each
draft in the follow-up UI before any message goes out.

Usage:
    PYTHONPATH=src python scripts/run_followup_batch.py [--event-log PATH]
                                                         [--backend MODE]
Requires MEDAGENT_SUBAGENT_MODE=advisory to actually run; any other mode
prints a hint and exits 0 without calling the model.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from medagent.domain import utc_now  # noqa: E402
from medagent.runtime import build_runtime  # noqa: E402
from medagent.subagents import SubagentCoordinator  # noqa: E402
from medagent.web_tools import WebTools  # noqa: E402
from medagent.workspace import Workspace  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-log", type=Path, default=None)
    parser.add_argument("--backend", default=None)
    args = parser.parse_args()

    mode = os.environ.get("MEDAGENT_SUBAGENT_MODE", "off")
    if mode != "advisory":
        print(
            f"MEDAGENT_SUBAGENT_MODE={mode}：随访批量整理需要 advisory 才会实际执行；"
            "当前模式下不调用模型，退出。"
        )
        return 0

    runtime = build_runtime(args.event_log, args.backend)
    context = runtime.start()
    engine = context.services.require("consultation")
    events = context.services.require("events")
    workspace = Workspace(
        engine, events, context.services.require("backend_mode")
    )
    web_tools = WebTools(events)
    coordinator = SubagentCoordinator(
        events,
        getattr(engine, "backend", None),
        mode=mode,
        web_enabled=os.environ.get("MEDAGENT_SUBAGENT_WEB", "off") == "on",
        web_tools=web_tools,
    )

    now = utc_now()
    due = [
        item
        for item in workspace.follow_ups()
        if item["plan"].get("status") == "scheduled"
        and item["plan"].get("due_at", "") <= now
    ]
    if not due:
        print("没有已到期且待处理的随访计划。")
        return 0

    states = {}
    for item in due:
        session_id = item["session_id"]
        states[session_id] = workspace.engine_for(session_id).state(session_id)

    results = coordinator.run_followup_batch(due, states)
    for result in results:
        session_id = result.get("session_id")
        if session_id is None:
            continue
        workspace.follow_up_drafts[session_id] = result
        print(
            {
                "session_id": session_id,
                "priority": result.get("priority"),
                "draft_message": result.get("draft_message"),
            }
        )
    print(f"已生成 {len(results)} 份随访草稿（仅存内存，未发送）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
