// Text-only projection of real execution state. No model HTML, JS or SVG is run.
export function createExecutionUI(api) {
  const $ = (id) => document.getElementById(id);
  let current = null,
    timer = null,
    available = false,
    polling = false,
    submitting = false;
  const events = new Map();
  const rendered = new Set();
  let artifactSignature = "";
  const labels = {
    created: "准备就绪",
    starting: "启动隔离环境",
    running: "正在执行",
    cancelling: "正在停止",
    completed: "本轮已完成",
    failed: "执行未完成",
    cancelled: "已停止",
  };
  const busy = () =>
    ["starting", "running", "cancelling"].includes(current?.status);
  const hasJobs = () =>
    current?.jobs?.some((job) => ["running", "stopping"].includes(job.status));
  function node(tag, cls, text) {
    const value = document.createElement(tag);
    if (cls) value.className = cls;
    if (text !== undefined) value.textContent = text;
    return value;
  }
  function controls() {
    $("execution-run").disabled =
      !available || busy() || submitting || Boolean(current?.storage_error);
    $("execution-new").disabled = !available || busy() || submitting;
    $("execution-stop").disabled =
      (!busy() && !hasJobs()) || current?.status === "cancelling";
    $("execution-input").disabled = busy() || submitting;
  }
  function project(event) {
    if (event.type === "turn.recovering")
      return {
        kind: "model",
        title: "继续未完成的执行",
        text: "上次模型输出达到单次上限，正在同一会话中接续；调用额度和截止时间保持累计。",
      };
    if (event.type === "turn.background_reply")
      return { kind: "final", title: "后台执行结果", text: event.text };
    if (event.type === "turn.completed" && event.final_response)
      return { kind: "final", title: "本轮结果", text: event.final_response };
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
      return { kind: "request", title: "你的任务", text: event.text };
    if (event.type === "model.request")
      return {
        kind: "model",
        title: "模型调用",
        text: `本轮第 ${event.index} 次 · HTTP ${event.status}`,
      };
    if (
      event.type === "runtime.event" &&
      event.notification?.method === "session.event"
    ) {
      const entry = event.notification.params?.event;
      if (!entry) return null;
      const data = entry.data || {};
      if (entry.type === "tool/call")
        return {
          kind: "tool",
          title: data.name || data.tool || "调用工具",
          text: JSON.stringify(data.arguments || data.input || data),
        };
      if (entry.type === "tool/result")
        return {
          kind: "result",
          title: "工具返回",
          text: JSON.stringify(data).slice(0, 2000),
        };
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
      : !busy() && hasJobs()
        ? "后台任务仍在运行"
        : labels[current.status] || current.status;
    $("execution-directory").textContent = current.directory;
    $("execution-model").textContent =
      `${current.model || "已配置模型"} · 真实执行`;
    $("execution-directory").title = "实际工作目录；已生成的文件会保留";
    $("execution-feedback").textContent = [current.error, current.storage_error]
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
    for (const event of events.values()) {
      if (rendered.has(event.sequence)) continue;
      const item = project(event);
      if (!item) continue;
      rendered.add(event.sequence);
      if (item.kind === "final") {
        const final = node("article", "execution-final");
        final.append(node("strong", "", item.title), node("p", "", item.text));
        result.append(final);
        continue;
      }
      const card = node("article", `execution-event execution-${item.kind}`);
      card.append(
        node(
          "span",
          "execution-event-dot",
          {
            request: "↗",
            model: "·",
            tool: "⌘",
            result: "↳",
            child: "⑂",
            failure: "!",
          }[item.kind] || "·",
        ),
        node("strong", "", item.title),
      );
      const detail = node("details");
      detail.append(
        node("summary", "", item.kind === "request" ? "任务内容" : "查看详情"),
        node("pre", "", item.text),
      );
      if (item.kind === "request") detail.open = true;
      card.append(detail);
      result.append(card);
    }
    if (nearEnd) result.scrollTop = result.scrollHeight;
    const signature = JSON.stringify(current.artifacts);
    if (signature !== artifactSignature) {
      artifactSignature = signature;
      $("execution-files").replaceChildren();
      $("execution-file-count").textContent = String(current.artifacts.length);
      for (const artifact of current.artifacts) {
        const file = node("button", "execution-file");
        file.type = "button";
        file.append(
          node("span", "", artifact.path),
          node("small", "", `${artifact.size} B`),
        );
        file.addEventListener("click", async () => {
          const id = current.id;
          try {
            const content = await api(
              `/api/execution/${id}/files?path=${encodeURIComponent(artifact.path)}`,
            );
            if (current?.id !== id) return;
            $("execution-preview-title").textContent = content.path;
            $("execution-preview").textContent = content.text;
          } catch (error) {
            if (current?.id !== id) return;
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
    if (!$("view-execution").hidden) schedule();
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, busy() || hasJobs() ? 700 : 2000);
  }
  async function refresh() {
    if (!current || polling) return;
    polling = true;
    const id = current.id;
    try {
      const snapshot = await api(
        `/api/execution/${id}?after=${Math.max(0, ...events.keys())}`,
      );
      if (current?.id === id) render(snapshot);
    } catch (error) {
      $("execution-feedback").textContent =
        `读取状态失败：${error.message}；可稍后重试或检查工作目录。`;
      if (busy() || hasJobs()) schedule();
    } finally {
      polling = false;
    }
  }
  async function create() {
    current = await api("/api/execution", { method: "POST", body: {} });
    events.clear();
    rendered.clear();
    artifactSignature = "";
    $("execution-results").replaceChildren();
    $("execution-preview-title").textContent = "文件预览";
    $("execution-preview").textContent = "选择一个文本或代码文件查看内容。";
    render(current);
  }
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
          body: { text, max_calls: Number($("execution-budget").value) },
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
    },
    refresh,
  };
}
