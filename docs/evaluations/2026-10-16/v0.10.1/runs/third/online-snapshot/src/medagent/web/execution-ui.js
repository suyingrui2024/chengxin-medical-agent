// Only host receipt events become structured cards. Model HTML/JSON is text.
import { createRenderer, calculationEnvelope } from "./structured-ui.js";
import { createChangeReview } from "./execution-changes-ui.js";
import { createClarificationUI } from "./execution-clarification-ui.js";
import {
  observationPresentation,
  toolObservationKey,
} from "./execution-observation-ui.js";
export function createExecutionUI(api) {
  const $ = (id) => document.getElementById(id);
  let current = null,
    timer = null,
    available = false,
    pollingFor = null,
    submitting = false;
  const events = new Map();
  const rendered = new Set();
  const renderedReceipts = new Set();
  const toolCards = new Map();
  const renderer = createRenderer();
  const changeReview = createChangeReview(api, () => current);
  const clarificationUI = createClarificationUI(
    api,
    () => current,
    (snapshot) => render(snapshot),
  );
  let artifactSignature = "",
    previewRequest = 0;
  let catalogRequest = 0,
    catalogState = "",
    activation = 0;
  const drafts = new Map();
  const labels = {
    created: "准备就绪",
    starting: "启动隔离环境",
    running: "正在执行",
    cancelling: "正在停止",
    completed: "本轮已完成",
    failed: "执行未完成",
    cancelled: "已停止",
    interrupted: "上次执行已中断",
    saved: "已保存",
    unavailable: "记录不可读取",
  };
  const symbols = {
    request: "↗",
    model: "·",
    tool: "⌘",
    result: "↳",
    child: "⑂",
    failure: "!",
    waiting: "◷",
  };
  const busy = () =>
    ["starting", "running", "cancelling"].includes(current?.status) ||
    current?.clarifications?.some((value) =>
      ["pending", "submitted"].includes(value.status),
    );
  const hasJobs = () =>
    current?.jobs?.some((job) =>
      ["starting", "running", "stopping"].includes(job.status),
    );
  function node(tag, cls, text) {
    const value = document.createElement(tag);
    if (cls) value.className = cls;
    if (text !== undefined) value.textContent = text;
    return value;
  }
  function controls() {
    $("execution-run").disabled =
      !available || busy() || submitting || Boolean(current?.storage_error);
    if (current?.execution_configured === false)
      $("execution-run").disabled = true;
    $("execution-new").disabled = !available || busy() || submitting;
    $("execution-open-project").disabled = !available || busy() || submitting;
    $("execution-stop").disabled =
      (!busy() && !hasJobs()) || current?.status === "cancelling";
    $("execution-input").disabled = busy() || submitting;
    $("execution-budget").disabled = busy() || submitting;
    $("execution-time-budget").disabled = busy() || submitting;
    $("execution-workspace").disabled = !available || busy() || submitting;
    $("execution-close").disabled =
      !current || busy() || hasJobs() || submitting;
    $("execution-refresh").disabled = !available || submitting;
  }
  async function reloadWorkspaces() {
    if (!available) return;
    const request = ++catalogRequest;
    try {
      const data = await api("/api/execution");
      if (request !== catalogRequest) return;
      const select = $("execution-workspace");
      const entries = [...data.sessions];
      if (current && !entries.some((item) => item.id === current.id))
        entries.unshift(current);
      select.replaceChildren(new Option("选择已保存的工作区", ""));
      for (const entry of entries) {
        const option = new Option(
          `${entry.title || entry.id.slice(0, 8)} · ${labels[entry.status] || entry.status}`,
          entry.id,
        );
        option.disabled = entry.status === "unavailable";
        select.append(option);
      }
      select.value = current?.id || "";
    } catch (error) {
      if (request !== catalogRequest) return;
      $("execution-feedback").textContent =
        `读取工作区列表失败：${error.message}`;
    }
  }
  function project(event) {
    if (event.type === "tool.observed") {
      const value = event.observation;
      return {
        ...observationPresentation(value),
        toolKey: toolObservationKey(
          value.session_id,
          value.sdk_turn,
          value.sdk_step,
          value.call_id,
        ),
        toolPhase: "observation",
        observation: value,
        text: JSON.stringify(
          {
            tool: value.tool,
            state: value.state,
            exit_code: value.exit_code,
            signal: value.signal,
            job_id: value.job_id,
          },
          null,
          2,
        ),
      };
    }
    if (
      ["tool.observation_gap", "tool.observation_unavailable"].includes(
        event.type,
      )
    )
      return {
        kind: "model",
        title: "部分运行状态未提供",
        text: "可查看原始工具输出；当前没有完整的结构化运行观察。",
      };
    if (event.type === "capability.failed")
      return {
        kind: "failure",
        title: "计算未通过核对",
        text: event.error?.message || "请核对输入或能力版本。",
      };
    if (event.type === "workspace.restored")
      return { kind: "model", title: "工作区已恢复", text: event.text };
    if (event.type === "turn.interrupted")
      return {
        kind: "failure",
        title: "上次执行已中断",
        text: "继续前请核对实际文件。未自动重放旧命令。",
      };
    if (event.type === "turn.recovering")
      return {
        kind: "model",
        title: "继续未完成的执行",
        text: "上次模型输出达到单次上限，正在同一会话中接续；调用额度和截止时间保持累计。",
      };
    if (event.type === "turn.deadline")
      return {
        kind: "failure",
        title: "已到执行时限",
        text: `本轮 ${event.time_budget_seconds ?? 180} 秒的执行预算已用完，正在停止主任务和后台活动。`,
      };
    if (event.type === "turn.background_reply")
      return { kind: "final", title: "后台执行结果", text: event.text };
    if (event.type === "turn.completed" && event.final_response)
      return {
        kind: "final",
        title: event.restored ? "历史执行结果" : "本轮结果",
        text: event.final_response,
      };
    if (
      event.type === "runtime.event" &&
      event.notification?.method === "subagent.started"
    )
      return {
        kind: "child",
        title: "子智能体已启动",
        text: JSON.stringify(event.notification.params),
      };
    if (
      event.type === "runtime.event" &&
      event.notification?.method === "subagent.finished"
    )
      return {
        kind: "child",
        title: "子智能体返回",
        text: JSON.stringify(event.notification.params),
      };
    if (event.type === "task.submitted")
      return {
        kind: "request",
        title: event.restored ? "历史任务" : "你的任务",
        text: event.text,
      };
    if (
      event.type === "runtime.event" &&
      event.notification?.method === "session.event"
    ) {
      const entry = event.notification.params?.event;
      if (!entry) return null;
      const data = entry.data || {};
      const callKey = (id) =>
        toolObservationKey(
          event.notification.params?.sessionId || "",
          data.turn,
          data.step,
          id || "",
        );
      if (entry.type === "tool/call") {
        return {
          kind: "tool",
          toolKey: callKey(data.callId),
          toolPhase: "call",
          title: data.name || data.tool || "调用工具",
          text:
            typeof data.arguments === "string"
              ? data.arguments
              : JSON.stringify(data.arguments || data.input || data, null, 2),
        };
      }
      if (entry.type === "tool/result") {
        const message = data.message || {};
        const output = Array.isArray(message.content)
          ? message.content
              .filter((block) => block?.type === "text")
              .map((block) => block.text || "")
              .join("\n")
          : "";
        const text = output || JSON.stringify(data, null, 2);
        let kind = "result",
          title = "工具返回";
        if (message.isError === true || data.isError === true) {
          kind = "failure";
          title = "工具执行出错";
        }
        return {
          kind,
          title,
          toolKey: callKey(
            data.callId || message.toolCallId || message.source?.callId,
          ),
          toolPhase: "result",
          text:
            text.length > 2400
              ? `${text.slice(0, 1400)}\n…\n${text.slice(-1000)}`
              : text,
        };
      }
      if (
        entry.type === "subagent/started" ||
        entry.type === "subagent.started"
      )
        return {
          kind: "child",
          title: "子智能体已启动",
          text: JSON.stringify(data),
        };
    }
    if (["turn.failed", "turn.cancelled", "task.failed"].includes(event.type))
      return {
        kind: "failure",
        title: event.type === "turn.cancelled" ? "执行已停止" : "本轮未完成",
        text: event.error || event.error_type || "可检查已有产物后继续",
      };
    return null;
  }
  function render(snapshot) {
    current = snapshot;
    for (const event of snapshot.events) events.set(event.sequence, event);
    $("execution-status").textContent = current.storage_error
      ? "工作目录不可用"
      : current.status !== "cancelling" &&
          current.clarifications?.some((value) => value.status === "pending")
        ? "需要你核对"
        : !busy() && hasJobs()
          ? "后台任务仍在运行"
          : labels[current.status] || current.status;
    $("execution-directory").textContent =
      current.workspace_kind === "project"
        ? `本地项目 · 文件直接保存到原目录：${current.directory}`
        : current.directory;
    $("execution-model").textContent =
      `${current.model || "已配置模型"} · ${current.execution_configured === false ? "仅查看已保存记录" : "真实执行"} · ${current.turns?.at(-1)?.model_calls || 0}次模型调用`;
    if (current.turns?.length) {
      const seconds = current.turns.at(-1).time_budget_seconds ?? 180;
      $("execution-model").textContent +=
        ` · 时限 ${seconds % 60 === 0 ? `${seconds / 60} 分钟` : `${seconds} 秒`}`;
    }
    $("execution-directory").title =
      current.workspace_kind === "project"
        ? "此目录中的文件可被智能体读取和修改；应用运行资料单独保存"
        : "实际工作目录；已生成的文件会保留";
    $("execution-feedback").textContent = [
      current.error === "time_budget_exhausted"
        ? "已到本轮执行时限，可核对文件后继续。"
        : current.error,
      current.storage_error,
      current.persistence_error,
      current.configuration_error,
      current.history_trimmed ? "仅显示最近保存的任务记录。" : "",
    ]
      .filter(Boolean)
      .join("；");
    if (current.storage_error) {
      $("execution-preview-title").textContent = "文件暂不可用";
      $("execution-preview").textContent =
        "工作目录不可读取，暂时无法预览文件。";
    } else if ($("execution-preview-title").textContent === "文件暂不可用") {
      $("execution-preview-title").textContent = "文件预览";
      $("execution-preview").textContent = "选择一个文本或代码文件查看内容。";
    }
    const result = $("execution-results"),
      nearEnd =
        result.scrollHeight - result.scrollTop - result.clientHeight < 80;
    if (!rendered.size && events.size) result.replaceChildren();
    function receipt(value, restored = false) {
      if (!value?.execution_id || renderedReceipts.has(value.execution_id))
        return;
      renderedReceipts.add(value.execution_id);
      const card = node("section", "execution-calculation");
      card.dataset.receiptId = value.execution_id;
      card.append(
        node(
          "p",
          "task-kicker",
          restored ? "已保存的计算回执" : "注册能力 · 实际计算",
        ),
        renderer.renderEnvelope(calculationEnvelope(value)),
      );
      result.append(card);
    }
    for (const event of events.values()) {
      if (rendered.has(event.sequence)) continue;
      if (event.type === "capability.completed") {
        rendered.add(event.sequence);
        receipt(event.result, event.restored);
        continue;
      }
      const item = project(event);
      if (!item) continue;
      rendered.add(event.sequence);
      if (item.toolPhase) {
        let entry = toolCards.get(item.toolKey);
        if (!entry) {
          const card = node(
            "article",
            `execution-event execution-${item.kind}`,
          );
          const dot = node(
            "span",
            "execution-event-dot",
            symbols[item.kind] || "·",
          );
          const title = node("strong", "", item.title);
          const detail = node("details");
          detail.append(node("summary", "", "查看详情"));
          card.append(dot, title, detail);
          result.append(card);
          entry = { card, dot, title, detail, turnId: event.turn_id };
          toolCards.set(item.toolKey, entry);
        }
        if (item.toolPhase === "observation") {
          entry.observation = item.observation;
          entry.card.dataset.observationId = item.observation.observation_id;
          if (!entry.observationText) {
            entry.observationText = node(
              "pre",
              "execution-tool-observation",
              item.text,
            );
            entry.detail.append(
              node(
                "p",
                "execution-output-label",
                event.restored ? "已保存的运行观察" : "SDK 结构化运行观察",
              ),
              entry.observationText,
            );
          } else entry.observationText.textContent = item.text;
        } else if (item.toolPhase === "call" && !entry.input) {
          entry.input = node("pre", "execution-tool-input", item.text);
          entry.detail.insertBefore(
            entry.input,
            entry.detail.children[1] || null,
          );
        } else if (item.toolPhase === "result" && !entry.output) {
          entry.output = node("pre", "execution-tool-output", item.text);
          entry.detail.append(
            node("p", "execution-output-label", "工具输出"),
            entry.output,
          );
        }
        const state = entry.observation
          ? observationPresentation(entry.observation)
          : item;
        entry.card.className = `execution-event execution-${state.kind}`;
        entry.title.textContent = state.title;
        entry.dot.textContent = symbols[state.kind] || "·";
        continue;
      }
      if (item.kind === "final") {
        const final = node("article", "execution-final");
        final.append(node("strong", "", item.title), node("p", "", item.text));
        result.append(final);
        continue;
      }
      const card = node("article", `execution-event execution-${item.kind}`);
      const dot = node(
        "span",
        "execution-event-dot",
        symbols[item.kind] || "·",
      );
      const title = node("strong", "", item.title);
      card.append(dot, title);
      const detail = node("details");
      detail.append(
        node("summary", "", item.kind === "request" ? "任务内容" : "查看详情"),
        node(
          "pre",
          item.toolPhase === "result" ? "execution-tool-output" : "",
          item.text,
        ),
      );
      if (item.kind === "request") detail.open = true;
      card.append(detail);
      result.append(card);
    }
    // Receipts outlive the bounded live event ring, and are host-owned too.
    for (const saved of snapshot.calculations || []) receipt(saved, true);
    const latestTurn = current.turns?.at(-1)?.id;
    const issues = new Map();
    for (const entry of toolCards.values()) {
      if (
        entry.turnId !== latestTurn ||
        !entry.card.classList.contains("execution-failure")
      )
        continue;
      // Repeated reads of one background job represent the same process outcome.
      const identity = entry.observation?.job_id || entry.card;
      issues.set(identity, entry);
    }
    const outcome = $("execution-outcome");
    outcome.hidden = !issues.size;
    outcome.textContent = issues.size
      ? `本轮出现 ${issues.size} 项运行异常 · 查看`
      : "";
    outcome.onclick = () => {
      const entry = issues.values().next().value;
      if (!entry) return;
      entry.detail.open = true;
      result.scrollTop = entry.card.offsetTop - result.offsetTop;
      entry.detail.querySelector("summary").focus({ preventScroll: true });
    };
    if (nearEnd) result.scrollTop = result.scrollHeight;
    const signature = JSON.stringify(current.artifacts);
    $("execution-file-count").textContent = current.storage_error
      ? "—"
      : String(current.artifacts.length);
    if (signature !== artifactSignature) {
      artifactSignature = signature;
      $("execution-files").replaceChildren();
      for (const artifact of current.artifacts) {
        const file = node("button", "execution-file");
        file.type = "button";
        file.append(
          node("span", "", artifact.path),
          node("small", "", `${artifact.size} B`),
        );
        file.addEventListener("click", async () => {
          const id = current.id;
          const request = ++previewRequest;
          try {
            const content = await api(
              `/api/execution/${id}/files?path=${encodeURIComponent(artifact.path)}`,
            );
            if (
              current?.id !== id ||
              request !== previewRequest ||
              current.storage_error
            )
              return;
            $("execution-preview-title").textContent = content.path;
            $("execution-preview").textContent = content.text;
          } catch (error) {
            if (
              current?.id !== id ||
              request !== previewRequest ||
              current.storage_error
            )
              return;
            $("execution-preview").textContent = error.message;
          }
        });
        $("execution-files").append(file);
      }
    }
    $("execution-events").textContent = [...events.values()]
      .map((event) => JSON.stringify(event))
      .join("\n");
    controls();
    clarificationUI.update(current);
    changeReview.update(current);
    const stateKey = `${current.id}:${current.status}:${hasJobs()}`;
    if (stateKey !== catalogState) {
      catalogState = stateKey;
      void reloadWorkspaces();
    }
    if (!$("view-execution").hidden) schedule();
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, busy() || hasJobs() ? 700 : 2000);
  }
  async function refresh() {
    if (!current) {
      await reloadWorkspaces();
      return;
    }
    const id = current.id;
    const epoch = activation;
    const ticket = `${id}:${epoch}`;
    if (pollingFor === ticket) return;
    pollingFor = ticket;
    try {
      const snapshot = await api(
        `/api/execution/${id}?after=${Math.max(0, ...events.keys())}`,
      );
      if (current?.id === id && activation === epoch) render(snapshot);
    } catch (error) {
      if (current?.id !== id || activation !== epoch) return;
      $("execution-feedback").textContent =
        `读取状态失败：${error.message}；可稍后重试或检查工作目录。`;
      if (busy() || hasJobs()) schedule();
    } finally {
      if (pollingFor === ticket) pollingFor = null;
    }
  }
  function activate(snapshot) {
    changeReview.reset();
    clarificationUI.reset();
    activation++;
    previewRequest++;
    if (current) drafts.set(current.id, $("execution-input").value);
    current = snapshot;
    $("execution-input").value = drafts.get(current.id) || "";
    events.clear();
    rendered.clear();
    renderedReceipts.clear();
    toolCards.clear();
    artifactSignature = "";
    $("execution-results").replaceChildren();
    $("execution-preview-title").textContent = "文件预览";
    $("execution-preview").textContent = "选择一个文本或代码文件查看内容。";
    render(current);
  }
  async function create() {
    activate(await api("/api/execution", { method: "POST", body: {} }));
  }
  async function openWorkspace(identity) {
    activate(
      await api(`/api/execution/${identity}/open`, {
        method: "POST",
        body: {},
      }),
    );
  }
  $("execution-workspace").addEventListener("change", async () => {
    const identity = $("execution-workspace").value;
    if (!identity || busy() || submitting) return;
    submitting = true;
    controls();
    try {
      await openWorkspace(identity);
    } catch (error) {
      $("execution-feedback").textContent = error.message;
      $("execution-workspace").value = current?.id || "";
    } finally {
      submitting = false;
      controls();
    }
  });
  $("execution-refresh").addEventListener("click", reloadWorkspaces);
  $("execution-close").addEventListener("click", async () => {
    if (!current || busy() || hasJobs() || submitting) return;
    submitting = true;
    controls();
    try {
      await api(`/api/execution/${current.id}/close`, {
        method: "POST",
        body: {},
      });
      clearTimeout(timer);
      previewRequest++;
      drafts.set(current.id, $("execution-input").value);
      current = null;
      changeReview.reset();
      clarificationUI.reset();
      activation++;
      events.clear();
      rendered.clear();
      renderedReceipts.clear();
      toolCards.clear();
      artifactSignature = "";
      catalogState = "";
      $("execution-results").replaceChildren(
        node(
          "p",
          "execution-empty",
          "选择一个已保存的工作区，或创建新工作区。",
        ),
      );
      $("execution-files").replaceChildren();
      $("execution-outcome").hidden = true;
      $("execution-outcome").onclick = null;
      $("execution-file-count").textContent = "—";
      $("execution-preview-title").textContent = "文件预览";
      $("execution-preview").textContent = "选择工作区后查看文件。";
      $("execution-directory").textContent =
        "工作区文件和已保存记录保留在本机。";
      $("execution-status").textContent = "工作区已关闭";
      $("execution-input").value = "";
      $("execution-feedback").textContent = "";
      $("execution-events").textContent = "";
      await reloadWorkspaces();
    } catch (error) {
      $("execution-feedback").textContent = error.message;
    } finally {
      submitting = false;
      controls();
    }
  });
  $("execution-new").addEventListener("click", async () => {
    if (busy() || submitting) return;
    try {
      submitting = true;
      controls();
      await create();
    } catch (error) {
      $("execution-feedback").textContent = error.message;
    } finally {
      submitting = false;
      controls();
    }
  });
  $("execution-open-project").hidden =
    typeof globalThis.chengxinDesktop?.openProject !== "function";
  $("execution-open-project").title =
    "选择后，智能体可直接读取和修改项目目录中的文件";
  $("execution-open-project").addEventListener("click", async () => {
    if (busy() || submitting || !available) return;
    try {
      submitting = true;
      controls();
      const selected = await globalThis.chengxinDesktop.openProject();
      if (!selected.cancelled) {
        activate(selected.snapshot);
        await reloadWorkspaces();
      }
    } catch (error) {
      $("execution-feedback").textContent = error.message;
    } finally {
      submitting = false;
      controls();
    }
  });
  $("execution-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    if (busy() || submitting || !available) return;
    const text = $("execution-input").value.trim();
    if (!text) return;
    submitting = true;
    controls();
    $("execution-feedback").textContent = "";
    try {
      if (!current) await create();
      render(
        await api(`/api/execution/${current.id}/runs`, {
          method: "POST",
          body: {
            text,
            max_calls: Number($("execution-budget").value),
            time_budget_seconds: Number($("execution-time-budget").value),
          },
        }),
      );
      $("execution-input").value = "";
    } catch (error) {
      $("execution-feedback").textContent = error.message;
    } finally {
      submitting = false;
      controls();
    }
  });
  $("execution-stop").addEventListener("click", async () => {
    if (!current || (!busy() && !hasJobs())) return;
    try {
      render(
        await api(`/api/execution/${current.id}/cancel`, {
          method: "POST",
          body: {},
        }),
      );
    } catch (error) {
      $("execution-feedback").textContent = error.message;
    }
  });
  return {
    configure(value) {
      available = Boolean(value);
      $("execution-unavailable").hidden = available;
      controls();
      if (available) void reloadWorkspaces();
    },
    async updateConfiguration() {
      if (!available || !current || current.execution_configured !== false)
        return;
      const id = current.id;
      const epoch = activation;
      try {
        const snapshot = await api(`/api/execution/${id}/open`, {
          method: "POST",
          body: {},
        });
        if (current?.id === id && activation === epoch) render(snapshot);
      } catch (error) {
        if (current?.id === id && activation === epoch)
          $("execution-feedback").textContent = error.message;
      }
    },
    refresh,
  };
}
