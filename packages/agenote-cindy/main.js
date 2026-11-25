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
 * - tools：agenote_search / agenote_health 两个只读入口，以及带可选试投的 agenote_diag。
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
 * Cindy 的 tool-execution topic 在可用时会提供工具调用/结果事件；拿不到这些事件时，才用
 * 轮次时长近似「有工作」。因此短工具轮也能武装定时器，纯短问答轮不会武装。
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

/**
 * 自诊断簿记：宿主到底把哪些事件投给了本插件、收尾提示投递失败的原因。
 * 这些是「静默失败」的唯一现场证据——钩子超时、权限未批、事件没到，表面上都只是
 * 「什么都没发生」。agenote_diag 工具把它原样吐出来。
 */
const diag = {
  startedAt: new Date().toISOString(),
  events: {},
  lastTurnEnd: null,
  lastNudge: null,
  lastCompose: null,
};

/** sessionId → 本轮状态。会话是插件自己的簿记，不落盘。 */
const sessions = new Map();

function stateOf(sessionId) {
  let state = sessions.get(sessionId);
  if (!state) {
    state = {
      briefSent: false, // 本会话是否已经发过简报（首轮才发）
      lastUserText: "",
      toolUses: 0, // 本轮保存下来的工具调用条数：比轮次时长更准的「有工作」证据
      lastTriggerAt: 0,
      signalTriggered: false, // 成功投递过显式信号 → 永久关闭空闲兜底
      nudgeInFlight: false,
      nudgePromise: null,
      nudgeGeneration: null, // 当前投递属于哪一轮，供并发请求共享回执
      turnGeneration: 0, // 使跨轮次的异步定时器失效
      turnActive: false,
      idleTimer: null,
    };
    sessions.set(sessionId, state);
    // 会话表只用于进程内簿记，长跑时优先丢弃没有活动轮次、投递或定时器的旧状态。
    // 不能按插入序硬删：旧状态可能仍有异步回执，删掉后同一 session 的新事件会
    // 创建第二份簿记，造成重复投递或漏掉定时器。
    if (sessions.size > 200) {
      let removed = 0;
      for (const [key, old] of sessions) {
        if (removed >= 100) break;
        if (old.turnActive || old.nudgeInFlight || old.idleTimer) continue;
        sessions.delete(key);
        removed += 1;
      }
    }
  }
  return state;
}

/** 调随包 Node worker。失败一律返回 null，调用方按「本轮不注入」降级。 */
async function askWorker(method, params, timeoutMs, options) {
  try {
    const request = {
      method,
      params,
      timeoutMs: timeoutMs || CLI_TIMEOUT_MS + 500,
    };
    // 工具调用中的 Node 请求绑定当前 call，取消/交卷后由 Host 回收子进程；
    // 旁听钩子没有 callId，保持独立的短请求生命周期。
    if (options && options.callId) {
      request.callId = options.callId;
      request.cancelWithCall = true;
    }
    const response = await cindy.node.request(request);
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

function safeJson(value, maxLength) {
  try {
    const text = JSON.stringify(value);
    return String(text === undefined ? value : text).slice(0, maxLength);
  } catch (err) {
    return String((err && err.message) || err).slice(0, maxLength);
  }
}

function hostResultError(result) {
  if (!result) return "宿主没有返回成功回执";
  if (result.errorCode || result.error) return result.errorCode || result.error;
  const text =
    result.content && Array.isArray(result.content)
      ? result.content.find((item) => item && item.type === "text" && typeof item.text === "string")
      : null;
  if (text) {
    try {
      const payload = JSON.parse(text.text);
      if (payload && (payload.errorCode || payload.error)) {
        return payload.errorCode || payload.error;
      }
      if (payload && payload.ok === false) return "宿主返回 ok=false";
      if (payload && payload.isError === true) return "宿主返回 isError=true";
    } catch (err) {
      // 普通文本回执也是合法成功回执；结构化错误才需要拒绝。
    }
  }
  if (result.isError === true || result.ok === false) return "宿主返回失败回执";
  return null;
}

function hostResultOk(result) {
  return hostResultError(result) === null;
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
  const record = { at: new Date().toISOString(), sessionId, reason, ok: false, error: null };
  diag.lastNudge = record;
  try {
    const result = await cindy.host.call("cindy_helper", "send_to_session", {
      target_session_id: sessionId,
      message: buildReviewPrompt(reason),
    });
    record.receipt = safeJson(result, 1200);
    record.ok = hostResultOk(result);
    if (!record.ok) {
      record.error = hostResultError(result) || safeJson(result && result.content, 400);
    }
    return record.ok;
  } catch (err) {
    record.error = String((err && err.message) || err);
    return false;
  }
}

/** 防止同一事件并发投递；跨轮次时等待旧回执后再投递，不丢新的完成信号。 */
async function requestNudge(state, sessionId, reason, generation) {
  if (state.nudgeInFlight && state.nudgePromise) {
    const inFlightPromise = state.nudgePromise;
    const inFlightGeneration = state.nudgeGeneration;
    const ok = await inFlightPromise;
    // 同一轮的并发请求共享旧回执，包括失败回执；否则两个同时到达的收尾事件
    // 会在第一次失败后立即重复投递。旧轮次的成功不能封存新轮次状态。
    if (
      generation === undefined ||
      inFlightGeneration === null ||
      inFlightGeneration === generation
    ) {
      return ok;
    }
    // generation 变了：这是新轮次的独立请求，旧回执结束后继续向下投递。
  }
  if (state.nudgeInFlight) return false;

  state.nudgeInFlight = true;
  state.nudgeGeneration = generation === undefined ? null : generation;
  const promise = deliverNudge(sessionId, reason);
  state.nudgePromise = promise;
  try {
    const ok = await promise;
    // 旧轮次的异步回执不能封存新轮次的状态或清掉它的定时器。
    if (ok && (generation === undefined || state.turnGeneration === generation)) {
      state.lastTriggerAt = Date.now();
      state.signalTriggered = true;
      clearIdle(state);
    }
    return ok;
  } finally {
    if (state.nudgePromise === promise) {
      state.nudgePromise = null;
      state.nudgeGeneration = null;
      state.nudgeInFlight = false;
    }
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
  diag.lastCompose = { at: new Date().toISOString(), sessionId, first, chars: text.length };

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

function hasCompletionSignal(text) {
  const lower = String(text || "").toLowerCase();
  for (const rawSignal of COMPLETION_SIGNALS) {
    const signal = rawSignal.toLowerCase();
    const englishSignal = /^[a-z]/.test(signal);
    let from = 0;
    while (from < lower.length) {
      const start = lower.indexOf(signal, from);
      if (start < 0) break;
      const end = start + signal.length;
      if (
        englishSignal &&
        ((start > 0 && /[a-z0-9_]/.test(lower[start - 1])) ||
          (end < lower.length && /[a-z0-9_]/.test(lower[end])))
      ) {
        from = end;
        continue;
      }
      const before = lower.slice(Math.max(0, start - 32), start);
      const after = lower.slice(end, end + 32);

      // 完成词出现在否定、条件或不确定问句里时，不算用户确认完成。
      const negated =
        /(?:不算|不一定|未必|并未|不曾|不再|不|没|未|无|尚未|还没|没有|不要)\s*$/.test(before) ||
        /(?:不了|不成|不行)\s*$/.test(after);
      const conditional = /(?:如果|若|假如|要是|等到|直到|当|一旦)[^。！？!?]{0,24}$/.test(before);
      const question = /^[^\n。！？!?]{0,20}(?:[?？]|(?:吧|呢|吗|么|没有|没)(?:[?？]|\s*$))/.test(after);
      const conditionalAfter = /^(?:了)?\s*(?:后|之后|以后|再|才)/.test(after);
      const contradictionAfter = /^(?:了)?\s*[,，;；:：]\s*(?:但|但是|不过|仍|还有|还没|未)/.test(after);
      const englishNegated =
        /\b(?:not|never|no|isn't|isnt|wasn't|wasnt|don't|dont|didn't|didnt|without)\s+(?:\w+\s+){0,3}$/.test(
          before,
        );
      const englishConditional =
        /\b(?:if|when|unless|once|until|should)\s+(?:\w+\s+){0,5}$/.test(before);
      const englishConditionalAfter = /^(?:\s|,|:)*(?:if|when|unless|once|until|should)\b/.test(after);
      const englishQuestion = /^(?:\s|,|:)*(?:[?？]|or not\b|(?:right|correct|okay|ok)\b[?？]?)/.test(after);
      const englishPraise = /\bwell\s+$/.test(before);
      const englishContradiction =
        /^(?:\s|,|;|:)*(?:but|still|yet)\s+(?:not|unfinished|incomplete)\b/.test(after);

      if (
        !negated &&
        !conditional &&
        !conditionalAfter &&
        !contradictionAfter &&
        !question &&
        !englishNegated &&
        !englishConditional &&
        !englishConditionalAfter &&
        !englishQuestion &&
        !englishPraise &&
        !englishContradiction
      ) {
        return true;
      }
      from = end;
    }
  }
  return false;
}

function onMessageCreated(msg) {
  const data = msg.data || {};
  const sessionId = data.sessionId;
  if (!sessionId || data.role !== "user") return;
  const text = contentText(data.content);
  // 插件自己投递的 review 提示既不算用户发言，也不应凭空开启一轮本地簿记。
  if (!text || text.indexOf(HOOK_MARKER) !== -1) return;
  const state = stateOf(sessionId);

  // 用户消息本身也能标记新轮次；即使宿主漏发 did-turn-start，旧定时器也会失效。
  if (!state.turnActive) {
    state.turnGeneration += 1;
    clearIdle(state);
    state.toolUses = 0;
    state.turnActive = true;
  }
  state.lastUserText = text;
}

function onToolExecution(msg) {
  const data = msg.data || {};
  if (!data.sessionId) return;
  const state = sessions.get(data.sessionId);
  // tool-execution 是全局主题；只有已经进入本地记录的主会话轮次才计入，避免把
  // subagent/cron 工具调用误当成当前会话的工作，也不为陌生会话创建簿记。
  if (!state || !state.turnActive) return;
  if (data.eventType === "tool_use" || data.eventType === "tool_result") {
    state.toolUses += 1;
  }
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

  const state = sessions.get(sessionId);
  // 不为没有任何本地轮次证据的孤立收尾创建簿记；这类事件不能安全触发兜底。
  if (!state) return;
  const hadTurnEvidence = state.turnActive || Boolean(state.lastUserText) || state.toolUses > 0;
  clearIdle(state);
  state.turnActive = false;
  // 只收到孤立的 turn-end（例如插件刚启动或事件丢失）时没有本地轮次证据，
  // 不能仅凭 durationMs 给陌生会话武装空闲提醒。
  if (!hadTurnEvidence) return;
  diag.lastTurnEnd = {
    at: new Date().toISOString(),
    sessionId,
    durationMs: data.durationMs,
    endReason: data.endReason,
    toolUses: state.toolUses,
  };

  const lastText = state.lastUserText || "";
  const lower = lastText.toLowerCase();
  const isSelfInjection = lastText.indexOf(HOOK_MARKER) !== -1;
  const hasSignal = !isSelfInjection && lower.length > 0 && hasCompletionSignal(lastText);
  const turnGeneration = state.turnGeneration;

  const now = Date.now();
  const mayTrigger = hasSignal && now - state.lastTriggerAt >= DEBOUNCE_MS;
  // 先保存本轮证据，再清掉快照，避免异步投递期间下一轮消息被旧快照覆盖。
  const workedThisTurn = state.toolUses > 0 || (data.durationMs || 0) >= IDLE_ARM_MIN_TURN_MS;
  state.lastUserText = "";
  state.toolUses = 0;
  if (mayTrigger) {
    const delivered = await requestNudge(
      state,
      sessionId,
      "检测到任务完成信号",
      turnGeneration,
    );
    if (delivered) return;
  }

  // 投递等待期间可能已经开始了新轮次；不要让旧轮次在 await 后重新武装定时器。
  if (state.turnGeneration !== turnGeneration) return;

  // 空闲兜底：本会话从未被显式信号触发过，且这一轮确实在干活。
  // 优先用本轮保存下来的工具调用条数；拿不到时退回轮次时长近似。
  if (state.signalTriggered) return;
  const shouldArmIdle = workedThisTurn || mayTrigger;
  diag.lastTurnEnd = Object.assign(diag.lastTurnEnd || {}, { armed: shouldArmIdle });
  if (!shouldArmIdle) return;

  function armIdleNudge(reason, generation) {
    if (state.idleTimer || state.signalTriggered || state.turnGeneration !== generation) return;
    state.idleTimer = setTimeout(async () => {
      state.idleTimer = null;
      if (state.signalTriggered || state.turnGeneration !== generation) return;
      const delivered = await requestNudge(state, sessionId, reason, generation);
      if (
        !delivered &&
        !state.signalTriggered &&
        state.turnGeneration === generation &&
        !state.idleTimer
      ) {
        // 多个异常触发同一个定时器时，只保留一条失败重试链。
        armIdleNudge(reason, generation);
      }
    }, IDLE_FALLBACK_MS);
  }

  armIdleNudge(
    mayTrigger
      ? "检测到完成信号但上次投递未确认——请按 agenote-review skill 流程评估本次对话"
      : "会话已空闲且未检测到显式完成信号——可能是一段工作（含夜间自动任务）已结束",
    turnGeneration,
  );
}

// ─── 工具：只读入口与诊断入口 ─────────────────────────────────────────

const TOOL_SUBCOMMANDS = {
  agenote_search: "search",
  agenote_health: "health",
};

/** 把插件这一侧看到的事实原样吐出来：事件计数、最近一次收尾、投递失败原因。 */
async function onDiagTool(msg) {
  const args = msg.args || {};
  // 顺手试一次只读 CLI，确认 worker 通道本身是通的。
  const cli = await askWorker(
    "run",
    { argv: ["health"], timeoutMs: 8000 },
    12000,
    { callId: msg.callId },
  );
  let sendTest = null;
  if (args.send_test === true) {
    const rawTarget = args.target_session_id;
    const target = typeof rawTarget === "string" ? rawTarget.trim() : "";
    sendTest = { target, at: new Date().toISOString() };
    if (!target) {
      sendTest.ok = false;
      sendTest.error = "send_test 必须显式传 target_session_id，避免误投到另一个会话。";
    } else if (target.length > 256 || /[\u0000-\u001f\u007f]/.test(target)) {
      sendTest.ok = false;
      sendTest.error = "target_session_id 过长或含控制字符。";
    } else {
      const startedAt = Date.now();
      try {
        const raw = await cindy.host.call("cindy_helper", "send_to_session", {
          target_session_id: target,
          message: HOOK_MARKER + "这是 agenote 插件的投递自检，收到即说明 handoff 通道可用。</agenote-hook>",
        });
        sendTest.elapsedMs = Date.now() - startedAt;
        sendTest.raw = safeJson(raw, 1200);
        sendTest.ok = hostResultOk(raw);
        if (!sendTest.ok) sendTest.error = hostResultError(raw);
      } catch (err) {
        sendTest.elapsedMs = Date.now() - startedAt;
        sendTest.ok = false;
        sendTest.thrown = String((err && err.stack) || err).slice(0, 1200);
      }
    }
  }
  const payload = {
    env: {
      setTimeout: typeof setTimeout,
      clearTimeout: typeof clearTimeout,
      cindyNode: typeof (cindy && cindy.node),
      cindyHost: typeof (cindy && cindy.host),
      hostTools: typeof (cindy && cindy.host && cindy.host.call),
    },
    sendTest,
    startedAt: diag.startedAt,
    events: diag.events,
    dropped: diag.dropped || 0,
    lastCompose: diag.lastCompose,
    lastSessionId: diag.lastSessionId || null,
    lastTurnEnd: diag.lastTurnEnd,
    lastNudge: diag.lastNudge,
    sessions: sessions.size,
    cliProbe: cli
      ? { ok: cli.ok === true, output: String(cli.output || cli.message || "").slice(0, 200) }
      : null,
  };
  return cindy.send({
    type: "tool-result",
    callId: msg.callId,
    ok: true,
    result: { output: JSON.stringify(payload, null, 2) },
  });
}

async function onToolCall(msg) {
  if (msg.tool === "agenote_diag") return onDiagTool(msg);
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
    const requestedLimit = Number(msg.args.limit);
    const limit = Number.isFinite(requestedLimit)
      ? Math.min(Math.max(Math.trunc(requestedLimit), 1), 20)
      : 5;
    argv = ["search", query, "--limit", String(limit)];
  } else {
    argv = ["health"];
    if (msg.args && msg.args.duplicates === true) argv.push("--duplicates");
  }

  const result = await askWorker(
    "run",
    { argv, timeoutMs: subcommand === "health" ? 30000 : 15000 },
    35000,
    { callId: msg.callId },
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
    diag.events[msg.name] = (diag.events[msg.name] || 0) + 1;
    if (msg.data && msg.data.sessionId) diag.lastSessionId = msg.data.sessionId;
    if (msg.dropped) {
      diag.dropped = (diag.dropped || 0) + msg.dropped;
      // 丢事件后本轮边界与工具证据不可恢复；保留 signalTriggered，避免因丢包重复提醒。
      for (const state of sessions.values()) {
        clearIdle(state);
        state.turnGeneration += 1;
        state.turnActive = false;
        state.lastUserText = "";
        state.toolUses = 0;
      }
    }

    if (msg.name === "will-compose-context") {
      try {
        return await onComposeContext(msg);
      } catch (err) {
        // 超时会被宿主按 allow 放行；这里显式放行，别让异常拖满 2 秒窗口。
        return verdict(msg, "allow");
      }
    }
    if (msg.name === "did-message-created") return onMessageCreated(msg);
    if (msg.name === "did-tool-execution") return onToolExecution(msg);
    if (msg.name === "did-turn-start") {
      const sessionId = (msg.data && msg.data.sessionId) || "";
      if (sessionId) {
        const state = stateOf(sessionId);
        // did-turn-start 是新的轮次边界；即使上一轮的 end 丢了，也不能沿用旧证据。
        clearIdle(state);
        state.turnGeneration += 1;
        state.lastUserText = "";
        state.toolUses = 0;
        state.turnActive = true;
      }
      return;
    }
    if (msg.name === "did-turn-end") return await onTurnEnd(msg);
  } catch (err) {
    // 旁听/工具回调里的异常不打断宿主会话：静默降级。
  }
});

// 预热点亮 Node worker：第一条用户消息不该把冷启动算进 2 秒裁决窗口。
askWorker("ping", {}, 5000);
