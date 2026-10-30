from __future__ import annotations
import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from importlib import resources
from threading import BoundedSemaphore, Event as ThreadEvent, Lock, Thread
from time import monotonic
from typing import Any, Dict, List, Optional
from uuid import uuid4
from ..backends import parse_decision
from ..conclusion import BANNED_PHRASES, GAP_IMPACTS, MEASUREMENT_FACTS
from ..decision_wait import DecisionInterrupted
from ..domain import CaseState, utc_now
from ..events import Event
from ..reasoning import ReasoningWorkspace, load_pathways

MODES = {"off", "shadow", "advisory"}


TENDENCY_LEVELS = {"self_care", "routine_visit", "urgent_today", "emergency"}


KINDS: Dict[str, Dict[str, Any]] = {
    "evidence_lookup": {
        "max_calls": 6,
        "max_tokens": 24000,
        "deadline_s": 45,
        "allow_web": True,
    },
    "second_opinion": {
        "max_calls": 3,
        "max_tokens": 16000,
        "deadline_s": 40,
        "allow_web": False,
    },
    "case_digest": {
        "max_calls": 2,
        "max_tokens": 12000,
        "deadline_s": 30,
        "allow_web": False,
    },
    "followup_batch": {
        "max_calls": 4,
        "max_tokens": 16000,
        "deadline_s": 60,
        "allow_web": False,
        "batch_deadline_s": 600,
    },
}


_KIND_TOOLS: Dict[str, tuple] = {
    "evidence_lookup": (
        "case_view",
        "knowledge_search",
        "web_search",
        "web_fetch",
        "subagent_report",
    ),
    "second_opinion": ("case_view", "knowledge_search", "subagent_report"),
    "case_digest": ("case_view", "knowledge_search", "subagent_report"),
    "followup_batch": ("case_view", "knowledge_search", "subagent_report"),
}


MAX_BRANCHES = 4


SPAWN_ENVELOPE_TOKENS = 48000


SPAWN_ENVELOPE_DEADLINE_S = 45


RECENT_WINDOW_TURNS = 16


MAX_FINDINGS = 20


MAX_CLAIM_CHARS = 300


MAX_CONCLUSION_CHARS = 1000


_DISPOSITION_PATTERN = re.compile(
    r"(应立即就医|必须立即就医|立即就医|必须服用|立即服用|必须住院"
    r"|立即停药|加量服用|减量服用|应尽快手术|必须手术"
    r"|去急诊|看急诊|挂急诊|尽快就医)"
)


_DOSAGE_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*(mg|毫克|ml|毫升|片|粒|支|袋|iu|IU)"
)


_ENVELOPE_HINT = """你只能返回一个 JSON 对象，格式恰好为 {"tool": ..., "arguments": {...}}，不得输出任何其他文字、解释或代码块标记。
可用工具：
- knowledge_search：检索本地内容库（鉴别条目与自护内容）。arguments: {"query": "检索词", "max_results": 3}。结果会以 tool_result 回喂给你，其中的 ref 是内容定位符，摘录是可逐字引用的原文。
- case_view：重读本次任务的病例投影。arguments: {}。
- subagent_report：提交最终结果并结束任务，arguments 形状见下方「提交格式」。
证据纪律（硬性）：每条 finding 必须至少带一条 evidence，quote 必须是逐字引文——case 类引文只能出自病例投影中对应事实的 evidence 或值（ref 填事实键）；knowledge 类引文只能出自 knowledge_search 回喂的摘录（ref 填该摘录的 ref）；没有逐字证据的判断不要写成 finding。不得编造引文、数字或来源。
禁止事项：不得给出确诊结论，不得给出处置、用药、剂量或就医时限指令，不得使用「确诊、保证、肯定是」等措辞；你的产出只是供主流程复核的参考材料，不是给患者的答复。"""


KIND_INSTRUCTIONS: Dict[str, str] = {
    "evidence_lookup": (
        """你是一个受控的证据检索子智能体。主流程正在考虑若干鉴别方向，但证据稀薄；你的任务是针对病例投影中给定的方向，整理：还缺什么关键证据、现有事实中哪些表现支持或反对该方向、需要注意的红旗点。先用 knowledge_search 查本地内容库再下结论。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"conclusion":"≤1000字小结","findings":[{"claim":"≤300字","evidence":[{"source_type":"case|knowledge|web","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":["..."]}}}
若检索后仍无任何已验证来源支持任何判断，findings 返回空数组并在 uncertainty 中说明。"""
    ),
    "second_opinion": (
        """你是一个受控的二意见复核子智能体。你看不到主流程的结论、方向与处置倾向——这是刻意的盲化设计，请完全基于病例投影独立判断：当前信息下你倾向的处置等级（自我观察/常规就诊/今日尽快就诊/急诊）与理由、你认为最不能漏掉的方向、以及还需要补问什么。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"conclusion":"≤1000字，写明你的独立处置倾向与理由","disposition_tendency":"self_care|routine_visit|urgent_today|emergency 四选一，与你的结论一致","findings":[{"claim":"≤300字","evidence":[{"source_type":"case|knowledge","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":["..."]}}}
处置倾向只能写在 conclusion 里作为复核意见，不得写成给患者的指令口吻。"""
    ),
    "case_digest": (
        """你是一个受控的病例摘要子智能体。你的任务是把病例投影（事实与最近对话）压缩成结构化摘要，供主流程替换窗口外的原文。摘要中的每条已确认事实必须带逐字引文（出自事实 evidence、事实值或患者原话），不得改写、推断或补全；发现的表述矛盾要单列出来。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"digest":{"timeline":["按时间顺序的事件短句"],"confirmed_facts":[{"key":"事实键","quote":"逐字引文"}],"contradictions":["矛盾点短句"],"prior_outcomes":["既往结局短句，无则空数组"]},"conclusion":"","findings":[],"uncertainty":{"level":"low|medium|high","reasons":[]}}}"""
    ),
    "followup_batch": (
        """你是一个受控的随访整理子智能体。你会收到一个已关闭病例的结论与随访计划投影；你的任务是判断这次随访的优先级并起草一条给患者的随访提醒草稿。草稿只是草稿，会经人工确认后才发送。草稿中不得出现确诊、保证类措辞，不得写具体药物剂量，不得给出新的处置指令。
"""
        + _ENVELOPE_HINT
        + """
提交格式：{"tool":"subagent_report","arguments":{"priority":"high|routine","reason":"优先级理由≤300字","draft_message":"随访提醒草稿≤300字","findings":[{"claim":"判断依据","evidence":[{"source_type":"case|knowledge","quote":"逐字引文","ref":"事实键或内容ref"}],"confidence":"low|medium|high"}],"uncertainty":{"level":"low|medium|high","reasons":[]}}}"""
    ),
}


_content_index_cache: Optional[Dict[str, Dict[str, str]]] = None
