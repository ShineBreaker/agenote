// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
//
// SPDX-License-Identifier: MIT

/**
 * agenote 的 Cindy 适配插件（ghost 电子脑）。
 *
 * agenote 的状态机在 CLI，卡片的写入流程与格式在 agenote-skills。本插件只做
 * 三件事：读宿主事件 → 调 CLI → 把结果封回宿主协议。
 *
 * - will-compose-context：把「会话简报 + 按轮召回」作为宿主的**资料**挂到本轮
 *   消息上。宿主明文保证「不改变显示／落库的主人原话」（runtimePlugins.ts 的
 *   appendRuntimePluginContext 只改 wire 消息），所以气泡里看不到。
 *   代价：这个节点要求用户在任务的「任务插件」里选中本插件才会被询问。
 * - did-message-created：记录最后一条用户消息，供完成信号判定（这个 topic 是
 *   纯旁听，不需要任务选择，所以触发链路全局生效）。
 * - did-turn-end / did-turn-start：完成信号投递 review 提示、空闲兜底定时器。
 * - tools：agenote_search / agenote_health 两个只读入口。
 *
 * 所有 CLI 调用都在随包 Node worker 里执行（main.js 是浏览器沙箱，没有
 * Node 也没有文件系统），并带 AGENOTE_AGENT=cindy 归因。
 *
 * 曾经用过 will-user-message 改写来注入：Cindy 的用户气泡是 whitespace-pre-wrap
 * 纯文本（UserMessage.tsx），任何注入都会原样显示，所以改走资料通道。
 */

/** 本插件自注入提示的标识符：带它的消息不参与触发判定，断开反馈环。 */
const HOOK_MARKER = "<agenote-hook>";

/**
 * 空闲兜底「有工作」门槛（毫秒）。
 * Cindy 的订阅面不提供工具调用事件，拿不到「本轮跑过工具」的事实，只能用
 * 轮次时长近似：短问答轮不会武装定时器，长轮次才可能是真在干活。
 * 这是对 F6「不得在纯对话轮误报」的近似，不是精确判定。
 */
const IDLE_ARM_MIN_TURN_MS = 45000;

/**
 * 资料节点的总预算（毫秒）。宿主给 2 秒，留出 IPC 与结算余量：worker 必须在
 * 1.6 秒内交卷，否则本轮放弃注入，别让裁决踩到超时线。
 */
const HOOK_BUDGET_MS = 1600;

// ─── BEGIN GENERATED: signals.completion ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
const COMPLETION_SIGNALS = [
  // 中文显式完成
  "可以用了",
  "一切正常",
  "都没问题",
  "都正常",
  "搞定",
  "完成",
  "做完了",
  "测试通过",
  "就这些",
  "先这样",
  "暂时够了",
  "就这样",
  "没了",
  // 英文显式完成
  "done.",
  "done!",
  "looks good",
  "ship it",
];
// ─── END GENERATED: signals.completion ───

// ─── BEGIN GENERATED: timing.debounce ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
const DEBOUNCE_MS = 300000;
// ─── END GENERATED: timing.debounce ───

// ─── BEGIN GENERATED: timing.idle ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
const IDLE_FALLBACK_MS = 300000;
// ─── END GENERATED: timing.idle ───

// ─── BEGIN GENERATED: hosts.cindy.budgets ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
const BRIEF_BUDGET = 8000;
const RECALL_BUDGET = 4000;
const CLI_TIMEOUT_MS = 1200;
// ─── END GENERATED: hosts.cindy.budgets ───

/** sessionId → 本轮状态。会话是插件自己的簿记，不落盘。 */
const sessions = new Map();

function stateOf(sessionId) {
  let state = sessions.get(sessionId);
  if (!state) {
    state = {
      briefSent: false, // 本会话是否已经发过简报（首轮才发）
      lastUserText: "",
      lastTriggerAt: 0,
      signalTriggered: false, // 显式信号触发过 → 永久关闭空闲兜底
      idleTimer: null,
    };
    sessions.set(sessionId, state);
    // 会话表只用于进程内簿记，长跑时按插入序丢弃最旧的一半，避免无界增长。
    if (sessions.size > 200) {
      const stale = [...sessions.keys()].slice(0, 100);
      for (const key of stale) {
        const old = sessions.get(key);
        if (old && old.idleTimer) clearTimeout(old.idleTimer);
        sessions.delete(key);
      }
    }
  }
  return state;
}

/** 调随包 Node worker。失败一律返回 null，调用方按「本轮不注入」降级。 */
async function askWorker(method, params, timeoutMs) {
  try {
    const response = await cindy.node.request({
      method,
      params,
      timeoutMs: timeoutMs || CLI_TIMEOUT_MS + 500,
    });
    if (!response || !response.ok) return null;
    return response.result || null;
  } catch (err) {
    return null;
  }
}

function verdict(msg, action, extra) {
  return cindy.send(
    Object.assign({ type: "event-verdict", hookId: msg.hookId, action }, extra || {}),
  );
}

function buildReviewPrompt(reason) {
  return [
    HOOK_MARKER + reason + "，请按 agenote-review skill 流程评估本次对话：",
    "（注意：这有可能是误报，如果当前任务没有完成的话，请忽略）",
    "1. 是否有可记录的经验信号（bug / 踩坑 / 更优方案 / 用户纠正 / 项目决策）？",
    "2. 如有 → 用 agenote CLI 写入（agenote add / agenote memory --add），调用时带 AGENOTE_AGENT=cindy 前缀",
    "3. 本轮用到的资料留痕：已有卡片 agenote touch；联网新知识 agenote add --type note",
    "4. 如无 → 明确回复「本次无可记录经验」</agenote-hook>",
  ].join("\n");
}

/** 把提示投递回本会话（插件来源，宿主会标成插件发的消息）。 */
async function deliverNudge(sessionId, reason) {
  try {
    const result = await cindy.host.call("cindy_helper", "send_to_session", {
      target_session_id: sessionId,
      message: buildReviewPrompt(reason),
    });
    return !(result && result.isError);
  } catch (err) {
    return false;
  }
}

// ─── 资料节点：把简报与召回挂到本轮消息上（不改原话）───────────────

async function onComposeContext(msg) {
  const data = msg.data || {};
  const sessionId = data.sessionId;
  const text = typeof data.text === "string" ? data.text : "";
  if (!sessionId) return verdict(msg, "allow");

  // 自己投递的提示（插件消息）不该再带上资料。
  if (text.indexOf(HOOK_MARKER) !== -1) return verdict(msg, "allow");

  const state = stateOf(sessionId);
  const first = !state.briefSent;

  const context = await askWorker(
    "context",
    {
      sessionId,
      prompt: text,
      first,
      briefBudget: BRIEF_BUDGET,
      recallBudget: RECALL_BUDGET,
      timeoutMs: CLI_TIMEOUT_MS,
      budgetMs: HOOK_BUDGET_MS,
    },
    HOOK_BUDGET_MS + 200,
  );

  const body = context && typeof context.text === "string" ? context.text.trim() : "";
  if (!body) return verdict(msg, "allow");

  state.briefSent = true;
  return verdict(msg, "context", { blocks: [{ id: "agenote-brief", text: body }] });
}

// ─── 用户消息旁听：只记最后一条，供完成信号判定 ─────────────────────

function contentText(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter((item) => item && item.type === "text" && typeof item.text === "string")
      .map((item) => item.text)
      .join("\n");
  }
  return "";
}

function onMessageCreated(msg) {
  const data = msg.data || {};
  const sessionId = data.sessionId;
  if (!sessionId || data.role !== "user") return;
  const text = contentText(data.content);
  if (!text) return;
  // 插件自己投递的 review 提示不算用户发言。
  if (text.indexOf(HOOK_MARKER) !== -1) return;
  stateOf(sessionId).lastUserText = text;
}

// ─── 轮次事件：完成信号 + 空闲兜底 ─────────────────────────────────────

function clearIdle(state) {
  if (state.idleTimer) {
    clearTimeout(state.idleTimer);
    state.idleTimer = null;
  }
}

async function onTurnEnd(msg) {
  const data = msg.data || {};
  const sessionId = data.sessionId;
  if (!sessionId) return;

  const state = stateOf(sessionId);
  clearIdle(state);

  const lastText = state.lastUserText || "";
  const lower = lastText.toLowerCase();
  const isSelfInjection = lastText.indexOf(HOOK_MARKER) !== -1;
  const hasSignal =
    !isSelfInjection &&
    lower.length > 0 &&
    COMPLETION_SIGNALS.some((signal) => lower.indexOf(signal.toLowerCase()) !== -1);

  const now = Date.now();
  if (hasSignal && now - state.lastTriggerAt >= DEBOUNCE_MS) {
    state.lastTriggerAt = now;
    state.signalTriggered = true;
    await deliverNudge(sessionId, "检测到任务完成信号");
    return;
  }

  // 空闲兜底：本会话从未被显式信号触发过，且这一轮足够长（像是在干活）。
  if (state.signalTriggered) return;
  if ((data.durationMs || 0) < IDLE_ARM_MIN_TURN_MS) return;

  state.idleTimer = setTimeout(() => {
    state.idleTimer = null;
    if (state.signalTriggered) return;
    state.lastTriggerAt = Date.now();
    state.signalTriggered = true;
    deliverNudge(
      sessionId,
      "会话已空闲且未检测到显式完成信号——可能是一段工作（含夜间自动任务）已结束",
    );
  }, IDLE_FALLBACK_MS);
}

// ─── 工具：只读入口 ────────────────────────────────────────────────────

const TOOL_SUBCOMMANDS = {
  agenote_search: "search",
  agenote_health: "health",
};

async function onToolCall(msg) {
  const subcommand = TOOL_SUBCOMMANDS[msg.tool];
  if (!subcommand) {
    return cindy.send({
      type: "tool-result",
      callId: msg.callId,
      ok: false,
      errorCode: "UNKNOWN_TOOL",
      message: `未知工具 ${msg.tool}：可用的是 agenote_search / agenote_health。`,
    });
  }

  let argv;
  if (subcommand === "search") {
    const query = String((msg.args && msg.args.query) || "").trim();
    if (!query) {
      return cindy.send({
        type: "tool-result",
        callId: msg.callId,
        ok: false,
        errorCode: "INVALID_ARGS",
        message: "query 不能为空：传入要检索的关键词。",
      });
    }
    const limit = Math.min(Math.max(Number(msg.args.limit) || 5, 1), 20);
    argv = ["search", query, "--limit", String(limit)];
  } else {
    argv = ["health"];
    if (msg.args && msg.args.duplicates === true) argv.push("--duplicates");
  }

  const result = await askWorker(
    "run",
    { argv, timeoutMs: subcommand === "health" ? 30000 : 15000 },
    35000,
  );
  if (!result || result.ok !== true) {
    return cindy.send({
      type: "tool-result",
      callId: msg.callId,
      ok: false,
      errorCode: "AGENOTE_CLI_FAILED",
      message:
        (result && result.message) ||
        "agenote CLI 没跑起来：确认 agenote 已安装并在 PATH 里，或设置 AGENOTE_BIN 指向它。",
    });
  }
  return cindy.send({
    type: "tool-result",
    callId: msg.callId,
    ok: true,
    result: { output: result.output },
  });
}

// ─── 分发 ──────────────────────────────────────────────────────────────

cindy.onHostMessage(async function (msg) {
  try {
    if (msg.type === "tool-call") return await onToolCall(msg);
    if (msg.type !== "event") return;

    if (msg.name === "will-compose-context") {
      try {
        return await onComposeContext(msg);
      } catch (err) {
        // 超时会被宿主按 allow 放行；这里显式放行，别让异常拖满 2 秒窗口。
        return verdict(msg, "allow");
      }
    }
    if (msg.name === "did-message-created") return onMessageCreated(msg);
    if (msg.name === "did-turn-start") {
      const sessionId = (msg.data && msg.data.sessionId) || "";
      if (sessionId) clearIdle(stateOf(sessionId));
      return;
    }
    if (msg.name === "did-turn-end") return await onTurnEnd(msg);
  } catch (err) {
    // 旁听/工具回调里的异常不打断宿主会话：静默降级。
  }
});

// 预热点亮 Node worker：第一条用户消息不该把冷启动算进 2 秒裁决窗口。
askWorker("ping", {}, 5000);
