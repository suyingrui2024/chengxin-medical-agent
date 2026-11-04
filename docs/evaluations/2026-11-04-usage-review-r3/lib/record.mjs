/**
 * 记录器：步骤日志、截图去重、断言归档、逐轮对话全文。
 *
 * 上一轮的两个毛病在这里修掉：
 *  1. 序号与截图共用一个计数器，导致 `39-`、`53-` 重号 —— 现在 stepSeq 与
 *     shotSeq 分开，且 shotSeq 从目录里已有文件的最大编号续起（分批跑不撞号）。
 *  2. 同一屏反复截图（上轮 73 张里 25 张重复）—— 按内容指纹去重，重复时
 *     只记一条 shot-skipped，并在汇总里报数。
 */
import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import path from "node:path";

const ARROW = /[↗→›»\s]+$/u;

function normalizeLabel(text) {
  return (text ?? "").replace(ARROW, "").replace(/\s+/g, " ").trim();
}

/** 一屏的身份：同一个人、同一阶段、同一修订、同一题、同样的按钮。 */
export function fingerprintOf(meta) {
  return [
    meta.persona,
    meta.phase ?? "",
    meta.revision ?? "",
    meta.questionId ?? "",
    meta.kind ?? "",
    meta.dialogId ?? "",
    normalizeLabel(meta.title),
    (meta.labels ?? []).map(normalizeLabel).join("|"),
  ].join("§");
}

export function createRecorder({ dir, fresh = false }) {
  const shotsDir = path.join(dir, "screenshots");
  const dialogueDir = path.join(dir, "dialogue");
  const stepLog = path.join(dir, "steps-r3.jsonl");
  const findingsPath = path.join(dir, "findings-r3.json");
  mkdirSync(shotsDir, { recursive: true });
  mkdirSync(dialogueDir, { recursive: true });

  if (fresh) {
    for (const name of readdirSync(shotsDir)) {
      if (name.startsWith("r3-")) unlinkSync(path.join(shotsDir, name));
    }
    writeFileSync(stepLog, "");
    writeFileSync(findingsPath, "[]");
  }

  const previous = existsSync(stepLog)
    ? readFileSync(stepLog, "utf-8")
        .split("\n")
        .filter(Boolean)
        .map((line) => {
          try {
            return JSON.parse(line);
          } catch {
            return null;
          }
        })
        .filter(Boolean)
    : [];

  let stepSeq = previous.length;
  let shotSeq = readdirSync(shotsDir).reduce((max, name) => {
    const match = /^r3-(\d+)-/.exec(name);
    return match ? Math.max(max, Number(match[1])) : max;
  }, 0);

  /** 指纹 → 已有截图名，跨批次保留。 */
  const fingerprints = new Map();
  for (const entry of previous) {
    if (entry.action === "shot" && entry.fp && entry.shot) {
      fingerprints.set(entry.fp, entry.shot);
    }
  }

  const findings = [];
  const shotIndex = new Map(); // persona → [{shot, turn, slug}]

  function record(entry) {
    const row = { seq: ++stepSeq, at: new Date().toISOString(), ...entry };
    appendFileSync(stepLog, `${JSON.stringify(row)}\n`, "utf-8");
    const tag = `[${String(row.seq).padStart(3, "0")}]`;
    console.log(
      `${tag} ${row.group ?? "-"} ${row.action}${row.detail ? ` ${row.detail}` : ""}`,
    );
    return row;
  }

  /**
   * 截一张图。meta 至少要有 persona/group/phase/questionId；full=false 时
   * 只截可视区（超长结论单用它，避免 400KB 一张）。
   */
  async function shot(page, meta, slug, { full = true, force = false } = {}) {
    const fp = fingerprintOf(meta);
    const existing = fingerprints.get(fp);
    if (existing && !force) {
      record({
        ...meta,
        action: "shot-skipped",
        slug,
        duplicate_of: existing,
        detail: `同屏已拍过：${existing}`,
      });
      return existing;
    }
    const name = `r3-${String(++shotSeq).padStart(2, "0")}-${meta.group}-${slug}.png`;
    const file = path.join(shotsDir, name);
    await page.screenshot({ path: file, fullPage: full });
    fingerprints.set(fp, name);
    const turn = meta.turn ?? null;
    if (!shotIndex.has(meta.persona)) shotIndex.set(meta.persona, []);
    shotIndex
      .get(meta.persona)
      .push({ shot: name, turn, slug, group: meta.group });
    record({ ...meta, action: "shot", shot: name, fp, detail: name });
    return name;
  }

  function assert(entry) {
    const row = {
      id: entry.id,
      severity: entry.severity ?? "P2",
      expected: entry.expected,
      actual: entry.actual,
      pass: Boolean(entry.pass),
      group: entry.group,
      persona: entry.persona,
      evidence: entry.evidence ?? [],
      note: entry.note,
    };
    findings.push(row);
    record({
      group: entry.group,
      persona: entry.persona,
      action: "assert",
      detail: `${row.pass ? "pass" : "FAIL"} ${row.id} (${row.severity})`,
      ...row,
    });
    return row;
  }

  function shotsFor(persona, turn) {
    if (turn == null) return [];
    return (shotIndex.get(persona) ?? [])
      .filter((item) => item.turn === turn)
      .map((item) => item.shot);
  }

  /** 保存断言汇总（可被后续批次追加）。 */
  function saveFindings() {
    const merged = new Map();
    if (existsSync(findingsPath)) {
      try {
        for (const row of JSON.parse(readFileSync(findingsPath, "utf-8"))) {
          merged.set(row.id, row);
        }
      } catch {
        /* 坏文件就重建 */
      }
    }
    for (const row of findings) merged.set(row.id, row);
    const rows = [...merged.values()].sort((a, b) => a.id.localeCompare(b.id));
    writeFileSync(findingsPath, `${JSON.stringify(rows, null, 1)}\n`, "utf-8");
    return rows;
  }

  function stats() {
    const skipped = previous.filter((e) => e.action === "shot-skipped").length;
    return { steps: stepSeq, shots: shotSeq, duplicates: skipped };
  }

  return {
    dir,
    shotsDir,
    dialogueDir,
    record,
    shot,
    assert,
    saveFindings,
    shotsFor,
    stats,
  };
}

const EVENT_KEEP =
  /^(turn\.|fact\.|self_check\.|triage\.|emergency\.|followup\.|capability\.|observation\.)/;

/**
 * 把一次会话的事件流写成逐轮对话全文。
 *
 * 从 snapshot 生成而不是边点边记：UI 会截断、折叠、重渲染，事件流才是
 * 患者到底说了什么、系统到底回了什么的权威来源。
 */
export function writeDialogue({
  dir,
  persona,
  sessionId,
  snapshot,
  shots,
  label,
}) {
  const events = (snapshot.events ?? []).filter((e) =>
    EVENT_KEEP.test(e.event_type ?? ""),
  );
  const turns = [];
  let current = null;

  for (const event of events) {
    const payload = event.payload ?? {};
    if (event.event_type === "turn.user_message") {
      current = { user: [], replies: [], events: [], turnId: event.turn_id };
      turns.push(current);
    }
    if (!current) {
      current = { user: [], replies: [], events: [], turnId: event.turn_id };
      turns.push(current);
    }
    if (event.event_type === "turn.user_message") {
      current.user.push(payload.text ?? payload.choice_value ?? "");
    } else if (event.event_type === "turn.agent_reply") {
      current.replies.push(payload.reply ?? {});
    } else if (event.event_type !== "turn.agent_reply") {
      current.events.push(`${event.event_type}(${compactPayload(payload)})`);
    }
  }

  const lines = [
    `# 对话全文 · ${persona}`,
    "",
    `> 会话 \`${sessionId}\` · ${turns.length} 轮 · 生成于 ${new Date().toISOString()}`,
    "> 来源：`GET /api/sessions/" +
      sessionId +
      "/snapshot` 的事件流（患者原话与助手回复逐字，未经 UI 截断）",
    "",
  ];

  turns.forEach((turn, index) => {
    const reply = turn.replies.at(-1) ?? {};
    const phase = reply.phase ?? "?";
    const qid = reply.question_id ?? reply.kind ?? "";
    lines.push(`## 轮 ${index + 1} · phase=${phase}${qid ? ` · ${qid}` : ""}`);
    if (turn.user.length) {
      lines.push(`**患者**：${turn.user.join(" / ")}`);
    } else {
      lines.push("**患者**：（无输入，系统主动发起）");
    }
    const messages = turn.replies.map((item) => item.message).filter(Boolean);
    lines.push(`**助手**：${messages.join("\n\n") || "（无文本）"}`);
    const options = (reply.quick_replies ?? []).map(
      (item) => item.label ?? item.value,
    );
    if (options.length) lines.push(`- 可选项：${options.join(" / ")}`);
    if (turn.events.length) lines.push(`- 事件：${turn.events.join("；")}`);
    const turnShots = (shots ?? [])
      .filter((item) => item.turn === index + 1)
      .map((item) => item.shot);
    if (turnShots.length) lines.push(`- 截图：${turnShots.join(", ")}`);
    lines.push("");
  });

  const file = path.join(dir, "dialogue", `dialogue-${label ?? persona}.md`);
  writeFileSync(file, `${lines.join("\n")}\n`, "utf-8");
  return file;
}

function compactPayload(payload) {
  const parts = [];
  for (const [key, value] of Object.entries(payload ?? {})) {
    if (value === null || value === undefined) continue;
    if (typeof value === "object") {
      const keys = Object.keys(value);
      parts.push(`${key}:{${keys.slice(0, 4).join(",")}}`);
    } else {
      parts.push(`${key}=${String(value).slice(0, 40)}`);
    }
  }
  return parts.slice(0, 4).join(" ");
}
