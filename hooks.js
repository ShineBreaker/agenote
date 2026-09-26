// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — hooks 半边：事件触发。
//
// 与 agenote-pi/index.ts 对等，映射到 DSH 的事件模型：
//   pi session_start   → 'agent/created'（会话进入，per-agent 初始化）
//   pi agent_end       → 'agent/turn-stopping'（turn 即将关闭、模型不再欠响应）
//   pi agent_start     → 'agent/status'（running=新一轮开始，作废空闲计时）
//
// 本模块只做「事件触发」——检测到信号就往会话里投一条提示，具体怎么做由
// agent 按 agenote-review / agenote-curator skill 决定。行为规范不在代码里重复。
//
// 投递语义（DSH 三条通道，见 README「设计要点」）：
//   - 健康度摘要 → system-prompt section：静态背景知识，随请求进入 system prompt。
//     关键区别：它不唤醒 driver、不产生 turn——`agent/created` 时用 followup() 投
//     会让插件在用户开口前替用户"发言"，driver 被唤醒后模型把摘要当指令处理并
//     回复，表现为"新会话一打开 agent 就自说自话"（已修）。
//   - review/curate 提示 → agent.followup()：这才是"要 agent 立即执行任务"的场景，
//     等价 pi 的 sendUserMessage({deliverAs:'followUp'})。

import { createUserMessage, healthSummary, runKb, schema } from "./lib.js";

export const name = "agenote-hooks";

/** system-prompt 里的 section 名。 */
const SECTION_NAME = "agenote-health-summary";
/** 注入的 review/curate 提示的 plugin 名——也是 durable log 里识别自注入的依据。 */
const PLUGIN_SOURCE = "agenote-hooks";

/**
 * 任务完成信号（取自 agenote-review skill references/triggers.md — 单一真相源）。
 * 收紧到强完成词，避免"好了"等高频词误触发。
 * 改动此处需同步 agenote-review/references/triggers.md。
 */
export const COMPLETION_SIGNALS = [
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

/** 显式完成信号触发的防抖冷却期：冷却期内的后续信号不再触发。 */
const DEBOUNCE_MS = 5 * 60 * 1000;
/** 空闲兜底：turn 结束后静置超过此时长且本会话从未触发过 → 触发一次（覆盖无人值守）。 */
const IDLE_FALLBACK_MS = 5 * 60 * 1000;
/** 健康度摘要缓存时长：system prompt 每个请求都拼装，摘要无需每步重算。 */
const STATUS_TTL_MS = 5 * 60 * 1000;
/** 本插件注入提示的标记——用于排除自注入消息，断开自触发反馈环。 */
export const HOOK_MARKER = "<agenote-hook>";

/**
 * hooks 半边的配置 schema——本半边默认值与合法范围的单一真相源。
 *
 * 由 index.js 组合成插件的 `Config` 导出；Cordis 加载时据此校验 cordis.yml
 * 行下发的 config 并填默认值，非法值（debounceMs 为负、signals 为空数组等）
 * 在加载期抛 ValidationError，不会被静默回退。新增可调项必须加到这里，
 * 不要在 apply() 里另写一套默认值。
 */
export const Config = schema.object({
  enabled: schema.boolean(true),
  /** system prompt 注入健康度摘要 */
  status: schema.boolean(true),
  /** 完成信号 → 注入 review 提示 */
  completionSignals: schema.boolean(true),
  /** 空闲兜底（无人值守场景） */
  idleFallback: schema.boolean(true),
  /** 覆盖内置信号清单（默认值即上方 COMPLETION_SIGNALS，上游真相源在 agenote-skills） */
  signals: schema.array(schema.string(), {
    default: COMPLETION_SIGNALS,
    minLength: 1,
  }),
  /** 显式完成信号触发的防抖冷却期 */
  debounceMs: schema.number({ default: DEBOUNCE_MS, minimum: 0 }),
  /** 空闲兜底阈值 */
  idleMs: schema.number({ default: IDLE_FALLBACK_MS, minimum: 1 }),
  /** 健康度摘要缓存时长；0 = 每个请求重算 */
  statusTtlMs: schema.number({ default: STATUS_TTL_MS, minimum: 0 }),
}, { default: {} });

/** 注入到下一轮的 agenote-review 评估提示（含留痕环节）。reason 说明触发来源。 */
export function buildReviewPrompt(reason) {
  return [
    `${HOOK_MARKER}${reason}，请按 agenote-review skill 流程评估本次对话：`,
    "（注意：这有可能是误报，如果当前任务没有完成的话，请忽略）",
    "1. 是否有可记录的经验信号（bug/踩坑/更优方案/用户纠正/项目决策）？",
    "2. 如有 → 通过 agenote CLI 写入（agenote add / agenote memory --add；调用时带 AGENOTE_AGENT=dsh 前缀）",
    "3. 本轮用到的资料留痕：已有卡片 agenote touch，联网新知识 agenote add（type=note）",
    "4. 如无 → 明确回复'本次无可记录经验'",
    "</agenote-hook>",
  ].join("\n");
}

/** 注入到下一轮的策展任务提示——由 agent 按 agenote-curator skill 主导执行。 */
export function buildCuratePrompt(reason) {
  return [
    `${HOOK_MARKER}${reason}，请按 agenote-curator skill 流程对知识库执行策展：`,
    "（CLI 只提供检测报告与原子命令，流程编排与去留决策由你执行；先看候选清单，核实后再写盘）",
    "1. 诊断：agenote health --quality --duplicates / stats / gaps",
    "2. 状态重整：list --unused-days 找降级候选、archive --stale 找归档候选，逐项审查后显式 update/archive",
    "3. 去重合并 / type 聚拢 / 矛盾调和 / memory 维护（规则见 skill）",
    "4. 可选：reconcile + dream 综合（值得沉淀的候选用 agenote add 写入）",
    "5. 收尾：reindex + lint --fix + agenote commit（策展产物），输出策展报告",
    "</agenote-hook>",
  ].join("\n");
}

/** 从 message.content 提取纯文本（DSH 的 content 是 ContentBlock[]）。 */
export function extractText(content) {
  if (typeof content === "string") return content;
  if (!Array.isArray(content)) return "";
  return content
    .filter(
      (block) => block && typeof block === "object" && block.type === "text",
    )
    .map((block) => block.text ?? "")
    .join(" ");
}

/** 本插件投递的消息统一带这个 source（durable log 里以此识别自注入）。 */
function ownMessage(text) {
  return createUserMessage({
    content: [{ type: "text", text }],
    // 会话格式 v4：producer-owned source kind，第三方插件写 `plugin:<名>`
    source: { kind: `plugin:${PLUGIN_SOURCE}` },
  });
}

/**
 * 把「要 agent 执行任务」的提示投递到会话（followup 排独立后续 turn 并唤醒
 * driver）。只用于 review/curate 这类任务提示；纯背景知识走 system prompt。
 */
function deliver(agent, text) {
  agent.followup(ownMessage(text));
}

/**
 * DSH 的 subagent 是同进程独立 agent（session.header.origin === 'subagent'，
 * 持久化字段，见 dsh-session SessionHeader），主进程插件对其事件一律豁免：
 * worker 的内部对话不该投 review 提示（污染 handoff），也不该各自武装 idle
 * 计时器（大量委派时的"反复触发"正来源于此）。pi 版靠 argv/env 嗅探子进程，
 * DSH 有权威字段，直接读。
 */
function isSubagent(agent) {
  return agent?.session?.header?.origin === "subagent";
}

/**
 * 防抖与"本会话已触发过"的判定一律从 durable log 推导，不存内存标志。
 *
 * 内存标志（signalFired/idleFired）在 resume/clear/compact 后会被 agent/created
 * 重置——web 刷新即 resume，同一会话干完活又触发一次；进程重启同理。而
 * durable log 是会话的单一真相源：扫一遍历史，本插件注入过几条、最后一条
 * 何时落盘，resume/重启后答案不变，防抖天然稳定。
 */
function deliveredByUs(session) {
  const events = [];
  const surface = session?.surface?.nodes;
  if (surface === undefined) return events;
  for (const seq of surface) {
    const event = session.eventAt(seq);
    if (
      event?.type === "user/message" &&
      // source 形态：v3 旧日志 {kind:"plugin", plugin}；v4 {kind:"plugin:<名>"}
      ((event.data?.source?.kind === "plugin" &&
        event.data.source.plugin === PLUGIN_SOURCE) ||
        event.data?.source?.kind === `plugin:${PLUGIN_SOURCE}`)
    ) {
      events.push(event);
    }
  }
  return events;
}

export function apply(ctx, cfg) {
  // cfg 已由 index.js 导出的 Config schema 校验并填默认值——非法配置在加载期
  // 就已失败，这里直接使用，不再另写一套默认值或静默降级。
  if (!cfg.enabled) return;

  // ── 健康度摘要 → system-prompt section ──
  // system prompt 每个请求都重新拼装，text 回调若每次 spawnSync 会把子进程
  // 开销叠加到每个 step——加 TTL 缓存，摘要本就是粗粒度状态，无需每步新鲜。
  if (cfg.status) {
    let cached = "";
    let cachedAt = 0;
    ctx.systemPrompt.section({
      name: SECTION_NAME,
      order: 10300,
      interpolate: false,
      text: () => {
        const now = Date.now();
        if (now - cachedAt < cfg.statusTtlMs) return cached;
        try {
          cached = healthSummary(runKb);
        } catch {
          cached = "";
        }
        cachedAt = now;
        return cached;
      },
    });
  }

  // ── 每会话触发状态 ──
  // 只存「驱动 timer 所需的最小瞬态」；防抖与"已触发过"从 durable log 推导。
  /** @type {Map<string, {timer:any, toolActivity:boolean, lastUserText:string}>} */
  const sessions = new Map();

  function stateOf(agent) {
    const id = String(agent?.id ?? "unknown");
    let state = sessions.get(id);
    if (!state) {
      state = { timer: undefined, toolActivity: false, lastUserText: "" };
      sessions.set(id, state);
    }
    return state;
  }

  function clearIdle(state) {
    if (state.timer !== undefined) {
      clearTimeout(state.timer);
      state.timer = undefined;
    }
  }

  // ── 'agent/created'：清瞬态（timer 随进程消亡，resume 后不能残留）──
  ctx.on("agent/created", ({ agent }) => {
    if (isSubagent(agent)) return;
    const state = stateOf(agent);
    clearIdle(state);
    state.toolActivity = false;
    state.lastUserText = "";
  });

  // ── 'agent/inbox/inserted'：记住最近一条用户消息 ──
  // 只认真正的用户发言（source.kind === 'user'）。本插件的自注入 source.kind
  // 是 'plugin'，天然被排除——反馈环在这里就断开，无需文本标记判断。
  ctx.on("agent/inbox/inserted", ({ agent, message }) => {
    if (isSubagent(agent)) return;
    if (message?.source?.kind !== "user") return;
    stateOf(agent).lastUserText = extractText(message.content);
  });

  // ── 'agent/status'：新一轮开始即作废空闲计时 ──
  // idle 只能从 turn 真正安静下来起算；agent 又开始干活了，待发计时必须取消，
  // 否则上一轮武装的 timer 会在"正在流式输出"时到点误触发。
  ctx.on("agent/status", ({ agent, status }) => {
    if (status !== "running") return;
    if (isSubagent(agent)) return;
    clearIdle(stateOf(agent));
  });

  // ── 'tools/result'：记录「本轮是否真的干过活」──
  // 空闲兜底不能用裸 turn 数判定：纯对话轮次（回答提问、只读核对）也会推进 turn，
  // 把它当"一段工作已结束"会误报。只有真的跑过工具的 turn 才算"有工作的轮次"。
  //
  // 判据必须绑在**当轮**上，不能用累计计数：累计值一旦 ≥1 就永久成立，
  // 之后任何纯对话轮都会照样越过门槛（本插件的第一次修复就踩了这个坑）。
  ctx.on("tools/result", ({ agent }) => {
    if (isSubagent(agent)) return;
    stateOf(agent).toolActivity = true;
  });

  // ── 'agent/turn-stopping'：turn 即将关闭 → 检测完成信号 + 武装空闲兜底 ──
  ctx.on("agent/turn-stopping", ({ agent }) => {
    if (isSubagent(agent)) return;
    const state = stateOf(agent);
    // 本轮的产出，先落定再清标志——它要随该轮武装的 timer 一起带到回调里。
    const workedThisTurn = state.toolActivity;
    state.toolActivity = false;

    // durable log 推导：本插件在本会话的既往注入（防抖依据，跨 resume 稳定）
    const own = deliveredByUs(agent.session);
    const lastOwn = own[own.length - 1];
    const lastTrigger = lastOwn?.time ?? 0;
    const everFired = own.length > 0;

    // 显式完成信号
    if (cfg.completionSignals) {
      const text = state.lastUserText.toLowerCase();
      if (text.length > 0) {
        const now = Date.now();
        const hit = cfg.signals.some((s) =>
          text.includes(String(s).toLowerCase()),
        );
        if (hit && now - lastTrigger >= cfg.debounceMs) {
          state.lastUserText = ""; // 一条用户消息只触发一次
          deliver(agent, buildReviewPrompt("检测到任务完成信号"));
          return; // 已触发，本轮不再武装 idle
        }
      }
    }

    // 空闲兜底：覆盖夜间无人值守——用户 kick-off 后离开，无人说"完成"。
    // 一旦触发过（信号或兜底，见 durable log）就永久禁用本条路径，
    // 避免与信号路径重复打扰。内存判定（idleFired/signalFired）在 resume
    // 后会被重置，这里改读 durable log——刷新/重启后不会二次触发。
    if (!cfg.idleFallback) return;
    clearIdle(state);
    if (everFired) return;
    // 只有"刚结束的这一轮真的干过活"才武装：纯对话轮不该在 5 分钟后收到
    // "一段工作已结束"的提示。workedThisTurn 随闭包进入本次 timer 的回调，
    // 因此判定的是**触发本轮**，不受后续轮次影响。
    if (!workedThisTurn) return;
    state.timer = setTimeout(() => {
      state.timer = undefined;
      // timer 到点时日志里可能有新的注入（信号路径在别的轮次触发过），
      // 重新推导，已经触发过就静默退出。
      if (deliveredByUs(agent.session).length > 0) return;
      try {
        deliver(
          agent,
          buildReviewPrompt(
            "会话已空闲 5 分钟且未检测到显式完成信号——可能是一段工作（含夜间自动任务）已结束",
          ),
        );
      } catch (error) {
        ctx.logger.warn(`agenote-hooks: 空闲兜底注入失败: ${String(error)}`);
      }
    }, cfg.idleMs);
    // timer 不 unref：会话存活期间就该触发；agent 销毁时随会话状态一并丢弃。
  });

  // ── 会话销毁：清掉计时器，避免进程退出前留下悬挂 timer ──
  ctx.on("agent/disposed", ({ agent }) => {
    const id = String(agent?.id ?? "unknown");
    const state = sessions.get(id);
    if (state) clearIdle(state);
    sessions.delete(id);
  });
}
