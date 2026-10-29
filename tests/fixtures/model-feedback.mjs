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
  if (parsed.latest_user_text) lastFullContext = parsed;
  return parsed;
}

function errorOf(context) {
  return String(context.tool_result?.error ?? "");
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
  if (!match) return [];
  return match[1]
    .split(/[,，]/)
    .map((name) => name.trim())
    .filter((name) => name.startsWith("warning_"));
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

  const warnings = untriedWarningFields(context);
  if (warnings.length) {
    const evidence = denialClause();
    if (evidence) {
      return {
        tool: "record_facts",
        arguments: {
          facts: warnings.map((key) => ({
            key,
            value: null,
            evidence,
            assertion: "absent",
          })),
        },
      };
    }
  }

  return commands.shift();
}

/** Forget the remembered turn. Only needed if a test swaps servers. */
export function resetFeedback() {
  lastFullContext = {};
}
