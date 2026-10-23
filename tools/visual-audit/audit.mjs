#!/usr/bin/env node
/**
 * 知微 · 界面树自动走查（visual audit）
 *
 * 按一棵声明式的「界面树」逐个节点走进真实界面：用真实交互（点按、输入、
 * 导航）到达每个界面/状态，截一张图，并做基础几何自检（横向溢出、问诊
 * 面板宽度是否被内容收缩）。产出：
 *   <out>/index.html      树形图库（缩略图 + 失败/异常标记）
 *   <out>/manifest.json   每个节点的路径、文件、几何数据与错误
 *   <out>/shots/*.png     截图
 *
 * 无第三方依赖：通过 CDP 驱动任意 Chromium 内核浏览器。
 *
 * 用法：
 *   node tools/visual-audit/audit.mjs \
 *     --python <项目虚拟环境的 python> \
 *     --chrome </path/to/chrome> \
 *     [--width 1440] [--height 900] [--port 18767] \
 *     [--out tools/visual-audit/runs/<时间戳>] [--only <节点 id 子串>]
 *
 * 退出码：有非可选节点失败时为 1，否则 0（可接入门禁）。
 */
import { spawn } from "node:child_process";
import { get as httpGet } from "node:http";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(SCRIPT_DIR, "..", "..");

function arg(name, fallback) {
  const i = process.argv.indexOf(`--${name}`);
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback;
}
const PORT = Number(arg("port", "18767"));
const CDP_PORT = PORT + 1;
const WIDTH = Number(arg("width", "1440"));
const HEIGHT = Number(arg("height", "900"));
const PYTHON = arg("python", "python3");
const CHROME = arg(
  "chrome",
  process.env.CHROME_BIN || "/opt/meta-chromium/chrome",
);
const ONLY = arg("only", "");
const stamp = new Date()
  .toISOString()
  .replace(/[:.]/g, "-")
  .replace("T", "_")
  .slice(0, 19);
const OUT = path.resolve(
  arg("out", path.join(SCRIPT_DIR, "runs", stamp)),
);
const SHOTS = path.join(OUT, "shots");
mkdirSync(SHOTS, { recursive: true });
mkdirSync(path.join(OUT, ".data"), { recursive: true });
mkdirSync(path.join(OUT, ".profile"), { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const children = [];
function launch(cmd, args, options = {}) {
  const child = spawn(cmd, args, { stdio: "ignore", ...options });
  children.push(child);
  return child;
}
function cleanup() {
  for (const c of children) {
    try {
      c.kill("SIGKILL");
    } catch {}
  }
}
process.on("exit", cleanup);
process.on("SIGINT", () => {
  cleanup();
  process.exit(130);
});

function httpGetText(url) {
  return new Promise((resolve, reject) => {
    httpGet(url, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () => resolve({ status: res.statusCode, body: data }));
    }).on("error", reject);
  });
}
async function waitHttp(url, timeoutMs = 30000) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeoutMs) {
    try {
      const res = await httpGetText(url);
      if (res.status >= 200 && res.status < 400) return true;
    } catch {}
    await sleep(400);
  }
  throw new Error(`服务未就绪：${url}`);
}

/* ---------------- CDP page driver ---------------- */
class Page {
  constructor(ws) {
    this.ws = ws;
    this.seq = 0;
    this.pending = new Map();
    ws.addEventListener("message", (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id && this.pending.has(m.id)) {
        this.pending.get(m.id)(m);
        this.pending.delete(m.id);
      }
    });
  }
  send(method, params = {}) {
    const id = ++this.seq;
    return new Promise((resolve) => {
      this.pending.set(id, resolve);
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  async evl(expression) {
    const res = await this.send("Runtime.evaluate", {
      expression,
      returnByValue: true,
      awaitPromise: true,
    });
    if (res.result?.exceptionDetails) {
      throw new Error(
        "页面求值异常：" +
          JSON.stringify(res.result.exceptionDetails).slice(0, 300),
      );
    }
    return res.result?.result?.value;
  }
  async waitFor(expression, label, timeoutMs = 15000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeoutMs) {
      if (await this.evl(`Boolean(${expression})`)) return;
      await sleep(250);
    }
    throw new Error(`等待超时：${label}`);
  }
  /** 点击文本包含 text 的按钮/可点元素（scope 为 CSS 选择器范围）。 */
  async clickText(text, { scope = "body", exact = false } = {}) {
    const ok = await this.evl(`(() => {
      const root = document.querySelector(${JSON.stringify(scope)}) || document.body;
      const match = (e) => {
        const t = (e.innerText || "").trim();
        return ${exact ? `t === ${JSON.stringify(text)}` : `t.includes(${JSON.stringify(text)})`};
      };
      const els = [...root.querySelectorAll('button, [role="button"], a, label, .choice-card, .option-card, .question-cue, .visual-choice')];
      let el = els.find(match);
      if (!el) {
        // 兜底：找文本命中的最深元素（线索卡等非按钮控件）
        const all = [...root.querySelectorAll("*")].filter((e) => e.offsetParent !== null && match(e));
        el = all.find((e) => ![...e.children].some((c) => match(c)));
      }
      if (!el) return false;
      el.scrollIntoView({ block: "center" });
      el.click();
      return true;
    })()`);
    if (!ok) throw new Error(`找不到可点击元素：${text}`);
    await sleep(350);
  }
  async clickSel(selector) {
    const ok = await this.evl(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return false;
      el.scrollIntoView({ block: "center" });
      el.click();
      return true;
    })()`);
    if (!ok) throw new Error(`找不到元素：${selector}`);
    await sleep(350);
  }
  async typeAndSend(text) {
    await this.evl(`(() => {
      const input = document.querySelector("#message-input");
      input.focus();
      input.value = ${JSON.stringify(text)};
      input.dispatchEvent(new Event("input", { bubbles: true }));
      document.querySelector("#send-button").click();
      return true;
    })()`);
    await sleep(600);
  }
  async shot(file) {
    const res = await this.send("Page.captureScreenshot", {
      format: "png",
      captureBeyondViewport: false,
    });
    writeFileSync(file, Buffer.from(res.result.data, "base64"));
  }
  async metrics() {
    return this.evl(`(() => {
      const grid = document.querySelector(".consult-grid");
      const panel = document.querySelector(".conversation-panel");
      const w = (el) => (el ? Math.round(el.getBoundingClientRect().width) : null);
      return {
        viewport: [innerWidth, innerHeight],
        overflowX: document.documentElement.scrollWidth > innerWidth + 1,
        gridWidth: w(grid),
        panelWidth: w(panel),
        panelShrunk:
          grid && panel ? w(panel) < w(grid) - 2 : false,
      };
    })()`);
  }
}

/* ---------------- 流程推进器 ----------------
 * 问诊是自适应状态机：步骤顺序随回答变化。推进器回答沿途任何问题
 * （文本题答"没有"，选项题点安全项，量表选中间值并确认，任务跳过），
 * 直到 stop(page) 成立或步数耗尽。返回是否到达。
 */
async function drive(p, stop, max = 12) {
  for (let i = 0; i < max; i++) {
    if (await stop(p)) return true;
    const acted = await p.evl(`(() => {
      const vis = (el) => el && el.offsetParent !== null;
      if ([...document.querySelectorAll("button")].some(b => vis(b) && b.innerText.includes("确认摘要并结束"))) return "at-summary";
      const task = document.querySelector("#active-task");
      if (vis(task)) {
        const skip = [...task.querySelectorAll("button")].find(b => /跳过|说不清|没有体温计|暂时/.test(b.innerText));
        if (skip) { skip.click(); return "task-skip"; }
      }
      const q = document.querySelector("#active-question");
      if (vis(q)) {
        const sev = q.querySelector(".severity-options, .severity-scale");
        if (sev) {
          const confirmSev = [...q.querySelectorAll("button")].find(b => /^确认/.test(b.innerText.trim()) && !b.disabled);
          if (confirmSev) { confirmSev.click(); return "severity-confirm"; }
          const d = [...sev.querySelectorAll("button")].find(b => b.innerText.trim() === "5" || b.innerText.trim() === "5 分");
          if (d) { d.click(); return "severity-pick"; }
        }
        const btns = [...q.querySelectorAll("button")].filter(vis);
        const confirm = btns.find(b => b.innerText.trim().startsWith("确认"));
        const safe = btns.find(b => /跳过|说不清|都没有|没有|不确定|变化不大/.test(b.innerText));
        if (safe) { safe.click(); return "choice"; }
        if (confirm) { confirm.click(); return "confirm"; }
      }
      const input = document.querySelector("#message-input");
      if (vis(input) && !input.disabled) {
        const qtext = (document.querySelector("#active-question")?.innerText || "") +
          (document.querySelector("#messages")?.innerText || "").slice(-400);
        let answer = "没有";
        if (/什么时候开始|多久了|何时开始/.test(qtext)) answer = "昨天逐渐出现";
        else if (/呼吸/.test(qtext)) answer = "能正常呼吸";
        else if (/咳嗽/.test(qtext)) answer = "干咳";
        else if (/喝水|吃饭|进食|吞咽/.test(qtext)) answer = "能正常喝水吃饭";
        input.focus();
        input.value = answer;
        input.dispatchEvent(new Event("input", { bubbles: true }));
        document.querySelector("#send-button").click();
        return "text";
      }
      return "";
    })()`);
    if (acted === "at-summary") return stop(p);
    await sleep(acted ? 700 : 500);
  }
  return stop(p);
}
const atSummary = async (p) =>
  p.evl(
    `[...document.querySelectorAll("button")].some(b => b.offsetParent !== null && b.innerText.includes("确认摘要并结束"))`,
  );
const taskWith = (keyword) => async (p) =>
  p.evl(
    `(() => { const t = document.querySelector("#active-task"); return Boolean(t && t.offsetParent !== null && (t.innerText || "").includes(${JSON.stringify(keyword)})); })()`,
  );

/* ---------------- 界面树 ----------------
 * 按执行顺序声明（问诊是状态机，必须顺流而下）。
 * branch 用 "/" 分隔层级，图库按此渲染树形。
 * run(page) 负责到达该界面；返回前应已停在目标状态，截图由运行器统一拍。
 * optional 节点失败只记录不中断、不影响退出码。
 */
const TREE = [
  {
    id: "consult-landing",
    branch: "问诊",
    title: "落地页",
    run: async (p) => {
      await p.waitFor(
        `document.querySelector("#start-consultation")`,
        "开始问诊按钮",
      );
    },
  },
  {
    id: "consult-subject",
    branch: "问诊/进行问诊",
    title: "问诊对象",
    run: async (p) => {
      await p.clickSel("#start-consultation");
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.trim() === "我自己")`,
        "对象卡片",
      );
    },
  },
  {
    id: "consult-age",
    branch: "问诊/进行问诊",
    title: "年龄步",
    run: async (p) => {
      await p.clickText("我自己", { exact: true });
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.trim() === "女")`,
        "性别选项",
      );
      await p.clickText("女", { exact: true });
      await p.waitFor(
        `document.querySelector("#message-input") && !document.querySelector("#message-input").disabled`,
        "年龄输入",
      );
    },
  },
  {
    id: "consult-emergency",
    branch: "问诊/进行问诊",
    title: "紧急信号筛查",
    run: async (p) => {
      await p.typeAndSend("32岁");
      await p.waitFor(
        `document.querySelector("#message-input") && !document.querySelector("#message-input").disabled`,
        "主诉输入",
      );
      await p.typeAndSend("头痛");
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.trim().startsWith("都没有"))`,
        "紧急筛查选项",
      );
    },
  },
  {
    id: "consult-urgent-dialog",
    branch: "问诊/弹窗",
    title: "紧急求助",
    optional: true,
    run: async (p) => {
      await p.clickSel("#urgent-help");
      await p.waitFor(`document.querySelector("dialog[open]")`, "求助弹窗");
    },
    after: async (p) => {
      await p.evl(
        `document.querySelector("dialog[open]")?.close()`,
      ).catch(() => {});
      await sleep(300);
    },
  },
  {
    id: "consult-associated",
    branch: "问诊/进行问诊",
    title: "伴随症状",
    run: async (p) => {
      await p.clickText("都没有");
      await p.waitFor(
        `(document.querySelector("#active-question") || {}).innerText?.includes("其他不舒服") || (document.querySelector("#active-question") || {}).innerText?.includes("伴随")`,
        "伴随症状问题",
      );
    },
    after: async (p) => {
      await p.typeAndSend("没有");
      await sleep(500);
    },
  },
  {
    id: "consult-onset",
    branch: "问诊/进行问诊",
    title: "发病（视觉问答）",
    run: async (p) => {
      // 伴随症状之后先是头痛定向细节题，答完才到发病步
      await p.typeAndSend("前额胀痛，慢慢加重的，没有呕吐，没有怕光怕吵");
      await p.waitFor(
        `(document.querySelector("#active-question h2") || {}).innerText?.includes("什么时候开始")`,
        "发病问题",
      );
    },
    after: async (p) => {
      // 发病步靠输入框作答（线索卡只是提示，不是选项）
      await p.typeAndSend("昨天下午开始的，慢慢出现的");
      await sleep(500);
    },
  },
  {
    id: "consult-course",
    branch: "问诊/进行问诊",
    title: "变化趋势",
    run: async (p) => {
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.includes("变化不大")) || (document.querySelector("#active-question") || {}).innerText?.includes("怎么样了")`,
        "变化趋势问题",
      );
    },
    after: async (p) => {
      await p.clickText("变化不大");
      await sleep(500);
    },
  },
  {
    id: "consult-severity",
    branch: "问诊/进行问诊",
    title: "严重程度量表",
    run: async (p) => {
      await p.waitFor(
        `document.querySelector(".severity-options") || (document.querySelector("#active-question") || {}).innerText?.includes("严重")`,
        "严重程度",
      );
    },
    after: async (p) => {
      await p.evl(`(() => {
        const d = [...document.querySelectorAll(".severity-options button, .severity-options [role='button']")].find(b => b.innerText.trim() === "7");
        if (d) d.click();
        return Boolean(d);
      })()`);
      await sleep(400);
      await p.clickText("确认", { scope: "#active-question" }).catch(() => {});
      await sleep(500);
    },
  },
  {
    id: "consult-location",
    branch: "问诊/自检任务",
    title: "部位观察（本路径出现时）",
    optional: true,
    run: async (p) => {
      const found = await drive(p, taskWith("位置"), 10);
      if (!found)
        throw new Error("本回答路径未出现部位观察（由发热/疼痛路径决定）");
    },
    after: async (p) => {
      for (const t of ["暂时跳过", "说不清", "跳过"]) {
        try {
          await p.clickText(t);
          return;
        } catch {}
      }
    },
  },
  {
    id: "consult-temperature",
    branch: "问诊/自检任务",
    title: "体温自检（本路径出现时）",
    optional: true,
    run: async (p) => {
      const found = await drive(p, taskWith("体温"), 10);
      if (!found)
        throw new Error("本回答路径未出现体温自检（发热路径见文末专属节点）");
    },
    after: async (p) => {
      for (const t of ["没有体温计", "暂时跳过", "跳过"]) {
        try {
          await p.clickText(t);
          return;
        } catch {}
      }
    },
  },
  {
    id: "consult-followup",
    branch: "问诊/进行问诊",
    title: "定向追问（及通往摘要）",
    run: async (p) => {
      // 停在当时的问题上拍一张；若推进器已把流程带到摘要，也算到达
      // （本路径问题较少时没有独立的定向追问步）。
      await p.waitFor(
        `(document.querySelector("#active-question") && document.querySelector("#active-question").offsetParent !== null) || [...document.querySelectorAll("button")].some(b => b.innerText.includes("确认摘要并结束"))`,
        "定向问题或摘要",
      );
      await sleep(400);
    },
    // 如实标注实际拍到的状态：本路径若已到摘要，图库不得冒充追问覆盖（F12）
    extra: async (p) => {
      const atQuestion = await p.evl(
        `Boolean(document.querySelector("#active-question") && document.querySelector("#active-question").offsetParent !== null)`,
      );
      return [
        atQuestion
          ? "实际拍到：定向追问题面"
          : "实际拍到：摘要（本路径无独立追问步，与摘要节点同屏）",
      ];
    },
    after: async (p) => {
      const ok = await drive(p, atSummary, 14);
      if (!ok) throw new Error("推进 14 步仍未到达摘要");
    },
  },
  {
    id: "consult-summary",
    branch: "问诊/进行问诊",
    title: "摘要待确认",
    run: async (p) => {
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.includes("确认摘要并结束"))`,
        "摘要确认按钮",
      );
    },
  },
  {
    id: "consult-transcript",
    branch: "问诊/进行问诊",
    title: "展开对话记录",
    optional: true,
    run: async (p) => {
      await p.clickText("展开对话记录");
      await sleep(600);
    },
    after: async (p) => {
      await p.clickText("当前这一步").catch(() => {});
      await sleep(400);
    },
  },
  {
    id: "consult-drawer",
    branch: "问诊/抽屉",
    title: "已记录信息抽屉",
    run: async (p) => {
      await p.clickSel("#toggle-facts");
      await p.waitFor(
        `document.querySelector(".facts-panel.open, .facts-panel[data-open='true']") || getComputedStyle(document.querySelector(".facts-panel")).transform === "none"`,
        "抽屉打开",
      );
      await sleep(500);
    },
  },
  {
    id: "consult-correct-dialog",
    branch: "问诊/弹窗",
    title: "更正信息",
    optional: true,
    run: async (p) => {
      await p.clickText("更正", { scope: "#facts-panel" }).catch(() =>
        p.clickText("修正", { scope: "#facts-panel" }),
      );
      await p.waitFor(`document.querySelector("dialog[open]")`, "更正弹窗");
    },
    after: async (p) => {
      await p.evl(
        `document.querySelector("dialog[open]")?.close()`,
      ).catch(() => {});
      await p.clickSel("#facts-close").catch(() => {});
      await sleep(300);
    },
  },
  {
    id: "consult-closed",
    branch: "问诊",
    title: "已关闭 · 结论单",
    run: async (p) => {
      await p.clickSel("#facts-close").catch(() => {});
      await p.clickText("确认摘要并结束");
      await p.waitFor(
        `(document.body.innerText || "").includes("结论与后续安排") || (document.body.innerText || "").includes("下一步建议")`,
        "结论单",
        20000,
      );
      await sleep(600);
    },
  },
  {
    id: "fever-temperature",
    branch: "问诊/自检任务",
    title: "体温自检（发热路径）",
    run: async (p) => {
      // 另起一次问诊，主诉发热，确定性地走到体温自检任务。
      await p.clickText("新的问诊", { scope: "#sidebar" });
      await p.waitFor(
        `document.querySelector("#start-consultation")`,
        "开始问诊",
      );
      await p.clickSel("#start-consultation");
      await p.clickText("我自己", { exact: true });
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.trim() === "女")`,
        "性别选项",
      );
      await p.clickText("女", { exact: true });
      await p.waitFor(
        `document.querySelector("#message-input") && !document.querySelector("#message-input").disabled`,
        "年龄输入",
      );
      await p.typeAndSend("32岁");
      await sleep(400);
      await p.typeAndSend("发热");
      await p.waitFor(
        `[...document.querySelectorAll("button")].some(b => b.innerText.trim().startsWith("都没有"))`,
        "紧急筛查",
      );
      await p.clickText("都没有");
      const found = await drive(p, taskWith("体温"), 14);
      if (!found) throw new Error("发热路径 14 步内未到达体温自检");
    },
  },
  {
    id: "history-list",
    branch: "会谈记录",
    title: "会谈记录列表",
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="history"]');
      await p.waitFor(
        `!document.querySelector("#view-history")?.hidden`,
        "记录视图",
      );
      await sleep(500);
    },
  },
  {
    id: "records-home",
    branch: "健康档案",
    title: "健康档案页",
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="records"]');
      await p.waitFor(
        `!document.querySelector("#view-records")?.hidden`,
        "档案视图",
      );
      await sleep(500);
    },
  },
  {
    id: "records-glucose-dialog",
    branch: "健康档案/弹窗",
    title: "记录一次血糖",
    optional: true,
    run: async (p) => {
      await p.clickText("记录血糖", { exact: true });
      await p.waitFor(`document.querySelector("dialog[open]")`, "血糖弹窗");
    },
    after: async (p) => {
      await p.evl(
        `document.querySelector("dialog[open]")?.close()`,
      ).catch(() => {});
      await sleep(300);
    },
  },
  {
    id: "records-image-dialog",
    branch: "健康档案/弹窗",
    title: "上传检查图片",
    optional: true,
    run: async (p) => {
      await p.clickText("上传医疗图片");
      await p.waitFor(`document.querySelector("dialog[open]")`, "上传弹窗");
    },
    after: async (p) => {
      await p.evl(
        `document.querySelector("dialog[open]")?.close()`,
      ).catch(() => {});
      await sleep(300);
    },
  },
  {
    id: "settings-appearance",
    branch: "设置",
    title: "设置 · 外观与界面",
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="settings"]');
      await p.waitFor(
        `!document.querySelector("#view-settings")?.hidden`,
        "设置视图",
      );
      await sleep(500);
    },
  },
  {
    id: "settings-model-dialog",
    branch: "设置/弹窗",
    title: "模型服务配置",
    optional: true,
    run: async (p) => {
      await p.clickText("打开模型与运行配置");
      await p.waitFor(`document.querySelector("dialog[open]")`, "配置弹窗");
    },
    after: async (p) => {
      await p.evl(
        `document.querySelector("dialog[open]")?.close()`,
      ).catch(() => {});
      await sleep(300);
    },
  },
  {
    id: "settings-themes",
    branch: "设置/主题",
    title: "主题变体（逐个）",
    optional: true,
    run: async (p) => {
      // 枚举外观主题单选（仅 name=appearance，排除字号单选），逐个应用并各拍一张。
      const labels = await p.evl(`(() => {
        const inputs = [...document.querySelectorAll("#view-settings input[name='appearance']")];
        return inputs
          .map((i) => {
            const lab = document.querySelector('label[for="' + i.id + '"]') || i.closest("label");
            return (lab?.innerText || i.value || "").trim().split("\\n")[0];
          })
          .filter(Boolean);
      })()`);
      p._themes = [...new Set(labels)];
      if (!p._themes.length) throw new Error("未找到主题单选");
    },
    /* 主题枚举在 run 里完成：逐个应用并用 page.shotExtra 补拍。 */
    extra: async (p) => {
      const made = [];
      for (const theme of p._themes) {
        await p.evl(`(() => {
          const inputs = [...document.querySelectorAll("#view-settings input[name='appearance']")];
          const input = inputs.find((i) => {
            const lab = document.querySelector('label[for="' + i.id + '"]') || i.closest("label");
            return (lab?.innerText || "").includes(${JSON.stringify(theme)});
          });
          if (input) { input.click(); }
          return Boolean(input);
        })()`);
        await sleep(600);
        await p.shotExtra(theme);
        made.push(theme);
        // 深色主题额外拍一张问诊落地页：对比度问题最容易藏在主表面上。
        if (theme.includes("夜航")) {
          await p.evl(
            `document.querySelector('.nav-item[data-view="consultation"]')?.click()`,
          );
          await sleep(700);
          await p.shotExtra("夜航-问诊");
          await p.evl(
            `document.querySelector('.nav-item[data-view="settings"]')?.click()`,
          );
          await sleep(500);
        }
      }
      return made;
    },
  },
  {
    id: "dev-consult",
    branch: "研发模式",
    title: "问诊（研发态）",
    run: async (p) => {
      await p.evl(`(() => {
        const sel = document.querySelector("#workspace-mode");
        if (sel) { sel.value = "developer"; sel.dispatchEvent(new Event("change", { bubbles: true })); }
        return Boolean(sel);
      })()`);
      await sleep(600);
      await p.clickSel('.nav-item[data-view="consultation"]');
      await sleep(500);
    },
  },
  {
    id: "dev-execution",
    branch: "研发模式",
    title: "执行视图",
    optional: true,
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="execution"]');
      await sleep(600);
    },
  },
  {
    id: "dev-tasks",
    branch: "研发模式",
    title: "任务视图",
    optional: true,
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="tasks"]');
      // SVG 图资产无固有尺寸，隐藏→可见翻转后需要一拍完成最终布局，
      // 否则截图会抓到未沉降的小图（曾误报为产品问题 F11a）。
      await p
        .waitFor(
          `[...document.querySelectorAll(".library-image")].every((i) => i.complete && i.naturalWidth > 0)`,
          "任务库图片加载",
        )
        .catch(() => {});
      await sleep(900);
    },
  },
  {
    id: "dev-capabilities",
    branch: "研发模式",
    title: "能力视图",
    optional: true,
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="capabilities"]');
      await sleep(600);
    },
  },
  {
    id: "dev-inspect",
    branch: "研发模式",
    title: "检查视图",
    optional: true,
    run: async (p) => {
      await p.clickSel('.nav-item[data-view="inspect"]');
      await sleep(600);
    },
  },
];

/* ---------------- 运行器 ---------------- */
async function main() {
  if (!existsSync(CHROME)) {
    console.error(`找不到浏览器：${CHROME}（用 --chrome 指定）`);
    process.exit(2);
  }
  console.log(`▶ 启动应用服务（:${PORT}）…`);
  launch(PYTHON, ["-m", "medagent.server", "--host", "127.0.0.1", "--port", String(PORT)], {
    cwd: REPO_ROOT,
    env: {
      ...process.env,
      PYTHONPATH: path.join(REPO_ROOT, "src"),
      MEDAGENT_PATIENT_DATA_DIR: path.join(OUT, ".data"),
    },
  });
  await waitHttp(`http://127.0.0.1:${PORT}/`);

  console.log(`▶ 启动浏览器 ${WIDTH}×${HEIGHT} …`);
  launch(CHROME, [
    "--headless=new",
    "--disable-gpu",
    "--no-sandbox",
    `--remote-debugging-port=${CDP_PORT}`,
    `--user-data-dir=${path.join(OUT, ".profile")}`,
    `--window-size=${WIDTH},${HEIGHT}`,
    `--ip-address-space-overrides=127.0.0.1:${PORT}=public`,
    "--disable-features=LocalNetworkAccessCheck",
    "about:blank",
  ]);
  await waitHttp(`http://127.0.0.1:${CDP_PORT}/json/list`);
  const targets = JSON.parse(
    (await httpGetText(`http://127.0.0.1:${CDP_PORT}/json/list`)).body,
  );
  const target = targets.find((t) => t.type === "page");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  const page = new Page(ws);
  await page.send("Page.enable");
  await page.send("Emulation.setDeviceMetricsOverride", {
    width: WIDTH,
    height: HEIGHT,
    deviceScaleFactor: 1,
    mobile: false,
  });
  await page.send("Page.navigate", {
    url: `http://127.0.0.1:${PORT}/`,
  });
  await sleep(1800);
  await page.evl(`fetch("/api/settings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ backend: "demo" }) }).then(() => true).catch(() => false)`);
  await sleep(400);

  const results = [];
  let counter = 0;
  const nodes = ONLY
    ? TREE.filter((n) => n.id.includes(ONLY) || n.title.includes(ONLY))
    : TREE;
  console.log(`▶ 界面树共 ${nodes.length} 个节点，开始走查\n`);
  for (const node of nodes) {
    counter += 1;
    const label = `${node.branch}/${node.title}`;
    process.stdout.write(`  ${label} … `);
    const entry = {
      n: counter,
      id: node.id,
      branch: node.branch,
      title: node.title,
      optional: Boolean(node.optional),
      ok: true,
      files: [],
    };
    page.shotExtra = async (suffix) => {
      const safe = String(suffix).replace(/[^\p{L}\p{N}-]+/gu, "_");
      const file = `${String(counter).padStart(2, "0")}-${node.id}-${safe}.png`;
      await page.shot(path.join(SHOTS, file));
      entry.files.push(file);
      return file;
    };
    try {
      await node.run(page);
      const file = `${String(counter).padStart(2, "0")}-${node.id}.png`;
      await page.shot(path.join(SHOTS, file));
      entry.files.push(file);
      entry.metrics = await page.metrics().catch(() => null);
      if (node.extra) {
        const subs = await node.extra(page);
        entry.note = `子项：${subs.join("、")}`;
      }
      if (node.after) await node.after(page);
      console.log("✓");
    } catch (error) {
      entry.ok = false;
      entry.error = String(error?.message || error).slice(0, 300);
      console.log(`✗ ${entry.error}`);
      try {
        const file = `${String(counter).padStart(2, "0")}-${node.id}.FAILED.png`;
        await page.shot(path.join(SHOTS, file));
        entry.files.push(file);
      } catch {}
      if (node.after) await node.after(page).catch(() => {});
      if (!node.optional && ONLY === "") {
        // 非可选节点失败：后续节点多半连锁失败，仍继续以便看清范围
      }
    }
    results.push(entry);
  }

  const failed = results.filter((r) => !r.ok && !r.optional);
  const failedOptional = results.filter((r) => !r.ok && r.optional);
  const shrunk = results.filter((r) => r.metrics?.panelShrunk);
  const overflow = results.filter((r) => r.metrics?.overflowX);

  writeFileSync(
    path.join(OUT, "manifest.json"),
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        viewport: [WIDTH, HEIGHT],
        results,
        summary: {
          total: results.length,
          ok: results.filter((r) => r.ok).length,
          failed: failed.map((r) => r.id),
          failedOptional: failedOptional.map((r) => r.id),
          panelShrunk: shrunk.map((r) => r.id),
          overflowX: overflow.map((r) => r.id),
        },
      },
      null,
      2,
    ),
  );

  // 图库（按 branch 分组的树形页面）
  const groups = new Map();
  for (const r of results) {
    if (!groups.has(r.branch)) groups.set(r.branch, []);
    groups.get(r.branch).push(r);
  }
  const esc = (s) =>
    String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  let body = "";
  for (const [branch, items] of groups) {
    body += `<h2>${esc(branch)}</h2><div class="grid">`;
    for (const r of items) {
      const badges = [
        r.ok ? "" : `<span class="badge bad">失败${r.optional ? "（可选）" : ""}</span>`,
        r.metrics?.panelShrunk ? `<span class="badge warn">面板被收缩</span>` : "",
        r.metrics?.overflowX ? `<span class="badge warn">横向溢出</span>` : "",
      ].join(" ");
      const img = r.files.length
        ? r.files
            .slice(0, 4)
            .map(
              (f) =>
                `<a href="shots/${f}" target="_blank"><img loading="lazy" src="shots/${f}" alt="${esc(r.title)}"></a>`,
            )
            .join("")
        : `<div class="noimg">无截图</div>`;
      body += `<figure>${img}<figcaption><strong>${esc(r.title)}</strong> ${badges}<br><small>${esc(r.id)}${r.error ? " · " + esc(r.error) : ""}</small></figcaption></figure>`;
    }
    body += `</div>`;
  }
  writeFileSync(
    path.join(OUT, "index.html"),
    `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>知微界面树走查 ${stamp}</title><style>
body{font-family:system-ui,"PingFang SC","Microsoft YaHei",sans-serif;margin:24px;background:#f6f7f4;color:#1c2420}
h1{font-size:20px}h2{font-size:16px;margin-top:28px;border-left:4px solid #2e5940;padding-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
figure{margin:0;background:#fff;border:1px solid #dde3dc;border-radius:10px;overflow:hidden}
figure img{width:100%;display:block}
figcaption{padding:8px 10px;font-size:13px}
.badge{font-size:11px;padding:1px 6px;border-radius:8px;color:#fff}
.badge.bad{background:#b3382e}.badge.warn{background:#b97e1e}
.noimg{padding:40px;text-align:center;color:#8a938c}
small{color:#6a726c}</style></head><body>
<h1>知微 · 界面树自动走查</h1>
<p>${new Date().toLocaleString("zh-CN")} · 视口 ${WIDTH}×${HEIGHT} · 节点 ${results.length} · 成功 ${results.filter((r) => r.ok).length} · 失败 ${failed.length}（可选 ${failedOptional.length}）· 面板收缩 ${shrunk.length} · 横向溢出 ${overflow.length}</p>
${body}</body></html>`,
  );

  console.log(`\n■ 完成：${results.length} 节点，成功 ${results.filter((r) => r.ok).length}，失败 ${failed.length}（可选 ${failedOptional.length}），面板收缩 ${shrunk.length}，横向溢出 ${overflow.length}`);
  console.log(`■ 图库：${path.join(OUT, "index.html")}`);
  cleanup();
  process.exit(failed.length ? 1 : 0);
}

main().catch((error) => {
  console.error("走查中断：", error);
  cleanup();
  process.exit(2);
});
