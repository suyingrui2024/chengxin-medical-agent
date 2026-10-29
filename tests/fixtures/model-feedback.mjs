/**
 * Shared feedback handling for the scripted model servers.
 *
 * Every harness in this suite replays a fixed command queue and ignores
 * what the engine says back. That was fine while the engine accepted
 * whatever came next, but it now enforces three gates that a blind queue
 * walks straight into:
 *
 *   - questions are ordered by hypothesis, so a proposal for the wrong
 *     question is rejected outright;
 *   - a triggered pathway must be explored, or explicitly abstained;
 *   - the chief complaint's triage fields must be attempted before a
 *     summary.
 *
 * A rejected command shifts the whole script by one turn, the following
 * command answers a turn it was not written for, and the six-step
 * self-correction loop burns through the rest of the queue. The
 * consultation then never reaches the summary the test waits for — which
 * is why so many specs failed on a missing `.summary-card` rather than on
 * anything resembling their stated subject.
 *
 * `nextCommand` keeps a script in phase by obeying the feedback that is
 * machine-readable, and only that: a compliant reply does not consume the
 * queue. Anything it cannot read still falls through to the script, so a
 * harness that genuinely misbehaves fails loudly instead of being papered
 * over.
 */

/**
 * The engine sends the full turn context on the first call and a bare
 * `{tool_result}` on every retry, so the patient's own words are only
 * available from the frame before. One scripted model is active at a time
 * (workers: 1, one server per test), so the most recent full context is
 * the one the current turn is based on.
 */
let lastFullContext = {};
let writeBudgetSpent = false;

function readContext(body) {
  const last = [...(body.messages || [])]
    .reverse()
    .find((message) => message.role === "user");
  let parsed;
  try {
    parsed = JSON.parse(last?.content ?? "{}");
  } catch {
    return {};
  }
  if (parsed.latest_user_text) {
    if (parsed.latest_user_text !== lastFullContext.latest_user_text) {
      writeBudgetSpent = false;
    }
    lastFullContext = parsed;
  }
  return parsed;
}

function errorOf(context) {
  return String(context.tool_result?.error ?? "");
}

/**
 * Set `MODEL_FEEDBACK_DIAG=1` to see every frame the scripted model
 * receives. A rejected proposal is otherwise invisible: the test fails on
 * a missing card far downstream, and the reason is buried in a payload
 * nobody prints.
 */
function diag(context, reply) {
  if (!process.env.MODEL_FEEDBACK_DIAG) return;
  const error = errorOf(context);
  console.error(
    "[model-feedback]",
    JSON.stringify({
      active: context.active_question ?? null,
      error: error.slice(0, 160),
      reply: reply?.tool ?? reply ?? null,
    }),
  );
}

/** The question the engine insists on before the proposed one, if any. */
export function requiredQuestion(context) {
  const match = errorOf(context).match(/应先问\s*([a-z_]+)/);
  return match ? match[1] : null;
}

/**
 * The product's own wording for "this pathway does not apply", copied
 * from the controller's auto-summary path in `agent.py`. Reusing it keeps
 * the fixtures from inventing a clinical justification of their own.
 */
export const PATHWAY_ABSTAIN_REASON =
  "该通路无专属细节字段，已记录的伴随症状、病史等鉴别证据不足以单独探明，" +
  "摘要前不再追问该通路细节；结论与随访仍按已记录事实生成。";

/** True when the engine refused a summary until a pathway is explored. */
export function pathwayGateOpen(context) {
  return errorOf(context).includes("主诉通路尚未探明");
}

/** Required triage fields the engine says have not been attempted. */
export function untriedWarningFields(context) {
  const match = errorOf(context).match(/必要字段尚未尝试：([^。]+)/);
  if (match) {
    return match[1]
      .split(/[,，]/)
      .map((name) => name.trim())
      .filter((name) => name.startsWith("warning_"));
  }
  // The engine only names them once a summary is proposed, but the same
  // list is already visible in the context. Reading it early lets the
  // fields ride along with the next scripted write instead of costing a
  // write of their own — and the per-turn write budget is finite.
  const missing = lastFullContext.missing_required || [];
  return missing.filter((key) => key.startsWith("warning_"));
}

/**
 * The clause in which the patient denied red flags, e.g. 「上述危险信号
 * 都没有」. Recording the warning fields as absent against that clause is
 * what a real model would do — the patient already answered. Returns null
 * when the patient never said such a thing, so a fixture that genuinely
 * lacks the answer still fails instead of inventing one.
 */
export function denialClause() {
  const text = String(lastFullContext.latest_user_text ?? "");
  const match = text.match(/[^，。；;、\s]*危险信号[^，。；;、]*/);
  if (!match) return null;
  const clause = match[0].trim();
  if (!/没有|无|未/.test(clause)) return null;
  return text.includes(clause) ? clause : null;
}

/**
 * The next command to send: the engine's required question, a summary
 * that abstains from an unexplored pathway, the warning fields recorded
 * absent when the patient already denied them, or else the next line of
 * the script.
 */
export function nextCommand(body, commands, { abstainPathway = true } = {}) {
  // A `null` at the head of the queue is a deliberate backend failure, not
  // a missing line: the harnesses push it to exercise the retry UI. It
  // must win over any compliance reply, or the failure under test never
  // happens and the retry button never appears.
  if (commands.length && commands[0] === null) return commands.shift();

  const context = readContext(body);
  const reply = decide(context, commands, { abstainPathway });
  diag(context, reply);
  return reply;
}

function decide(context, commands, { abstainPathway }) {
  const required = requiredQuestion(context);
  if (required) {
    return { tool: "ask_question", arguments: { question_id: required } };
  }

  if (abstainPathway && pathwayGateOpen(context)) {
    return {
      tool: "review_summary",
      arguments: { abstain_reason: PATHWAY_ABSTAIN_REASON },
    };
  }

  // A turn only allows so many writes. Answering the triage gate with
  // another record_facts can spend the last one, and the engine then
  // refuses to write at all — so stop writing for this turn and take the
  // route it names: ask, or summarise.
  if (errorOf(context).includes("写入次数已达上限")) writeBudgetSpent = true;
  if (writeBudgetSpent) {
    const next = commands.shift();
    if (next) return next;
    return { tool: "review_summary", arguments: {} };
  }

  const warnings = untriedWarningFields(context);
  if (warnings.length) {
    const evidence = denialClause();
    if (evidence) {
      const facts = warnings.map((key) => ({
        key,
        value: null,
        evidence,
        assertion: "absent",
      }));
      // Fold them into the next scripted write when there is one. A turn
      // allows only so many writes; spending an extra one on the triage
      // fields exhausts the budget and then nothing can be written at all.
      const head = commands[0];
      if (head && head.tool === "record_facts") {
        commands.shift();
        return {
          tool: "record_facts",
          arguments: { facts: [...(head.arguments.facts || []), ...facts] },
        };
      }
      return { tool: "record_facts", arguments: { facts } };
    }
  }

  return commands.shift();
}

/** Forget the remembered turn. Only needed if a test swaps servers. */
export function resetFeedback() {
  lastFullContext = {};
}
