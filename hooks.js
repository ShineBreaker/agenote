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

import { createUserMessage, healthSummary, runKb } from "./lib.js";

export const name = "agenote-hooks";

const INJECT = [];

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

/** 显式完成信号触发的防抖冷却期：同一信号在冷却期内不重复触发。 */
const DEBOUNCE_MS = 5 * 60 * 1000;
/** 空闲兜底：turn 结束后静置超过此时长且本会话从未触发过 → 触发一次（覆盖无人值守）。 */
const IDLE_FALLBACK_MS = 5 * 60 * 1000;
/** 本插件注入提示的标记——用于排除自注入消息，断开自触发反馈环。 */
export const HOOK_MARKER = "<agenote-hook>";

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
		.filter((block) => block && typeof block === "object" && block.type === "text")
		.map((block) => block.text ?? "")
		.join(" ");
}

function normalize(cfg = {}) {
	return {
		enabled: cfg.enabled ?? true,
		/** 会话开始注入健康度摘要 */
		status: cfg.status ?? true,
		/** 完成信号 → 注入 review 提示 */
		completionSignals: cfg.completionSignals ?? true,
		/** 空闲兜底（无人值守场景） */
		idleFallback: cfg.idleFallback ?? true,
		signals: Array.isArray(cfg.signals) && cfg.signals.length > 0 ? cfg.signals : COMPLETION_SIGNALS,
		debounceMs: Number.isFinite(cfg.debounceMs) && cfg.debounceMs >= 0 ? cfg.debounceMs : DEBOUNCE_MS,
		idleMs: Number.isFinite(cfg.idleMs) && cfg.idleMs > 0 ? cfg.idleMs : IDLE_FALLBACK_MS,
	};
}

/**
 * 把提示投递到会话。
 *
 * 用 createUserMessage + agent.followup()：followup 把消息排成独立的后续 turn
 * 并唤醒 driver（"an idle driver starts a turn"），语义等同 pi 的
 * sendUserMessage({deliverAs:'followUp'})。
 *
 * 这里刻意不用 agent.steer()：steer 只作用于"最近的 step"，而本模块的两个触发点
 * 都没有待执行的 step（turn-stopping 时 turn 已收尾、agent/created 时会话刚起），
 * 用 steer 会把消息停在 inbox 里等下一次唤醒。agent.inject() 同理不适用——它不唤醒
 * driver。
 *
 * 消息 source 标为 plugin：既便于用户分辨"这不是我说的话"，也让
 * 'agent/inbox/inserted' 侧能干净地区分自注入。
 */
function deliver(agent, text, plugin = "agenote-hooks") {
	agent.followup(
		createUserMessage({
			content: [{ type: "text", text }],
			source: { kind: "plugin", plugin },
		}),
	);
}

export function apply(ctx, config = {}) {
	const cfg = normalize(config);
	if (!cfg.enabled) return;

	// ── 每会话触发状态 ──
	// 用 Map<sessionId, state> 而非模块级标量：一个进程里可以并存多个会话
	// （web 端多标签、subagent），模块级状态会串台——这正是 pi 版修过的 bug。
	/** @type {Map<string, {lastTrigger:number, signalFired:boolean, idleFired:boolean, workTurns:number, toolActivity:boolean, lastUserText:string, selfInjected:boolean, timer:any}>} */
	const sessions = new Map();

	function stateOf(agent) {
		const id = String(agent?.id ?? "unknown");
		let state = sessions.get(id);
		if (!state) {
			state = {
				lastTrigger: 0,
				signalFired: false,
				idleFired: false,
				workTurns: 0,
				toolActivity: false,
				lastUserText: "",
				selfInjected: false,
				timer: undefined,
			};
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

	// ── 'agent/created'：会话进入 —— 重置状态 + 注入健康度摘要 ──
	// 必须重置：agent 可能在 resumed/clear/compact 后复用同一 session id，
	// 上一轮的 timer 与 signalFired 不能带到新会话。
	ctx.on("agent/created", ({ agent }) => {
		const state = stateOf(agent);
		clearIdle(state);
		state.lastTrigger = 0;
		state.signalFired = false;
		state.idleFired = false;
		state.workTurns = 0;
		state.toolActivity = false;
		state.lastUserText = "";
		state.selfInjected = false;

		if (!cfg.status) return;
		try {
			const summary = healthSummary(runKb);
			if (summary.length > 0) deliver(agent, summary, "agenote-status");
		} catch (error) {
			ctx.logger.warn(`agenote-hooks: 状态注入失败: ${String(error)}`);
		}
	});

	// ── 'agent/inbox/inserted'：记住最近一条用户消息 ──
	// 只认真正的用户发言（source.kind === 'user'），排除插件注入与本插件自注入，
	// 断开"提示词自己含完成词 → 下轮又匹配到自己"的反馈环。
	ctx.on("agent/inbox/inserted", ({ agent, message }) => {
		const state = stateOf(agent);
		const kind = message?.source?.kind;
		if (kind !== "user") return;
		const text = extractText(message.content);
		if (text.includes(HOOK_MARKER)) {
			state.selfInjected = true;
			return;
		}
		state.selfInjected = false;
		state.lastUserText = text;
	});

	// ── 'agent/status'：新一轮开始即作废空闲计时 ──
	// idle 只能从 turn 真正安静下来起算；agent 又开始干活了，待发计时必须取消，
	// 否则上一轮武装的 timer 会在"正在流式输出"时到点误触发。
	ctx.on("agent/status", ({ agent, status }) => {
		if (status !== "running") return;
		clearIdle(stateOf(agent));
	});

	// ── 'tools/result'：记录「本轮是否真的干过活」──
	// 空闲兜底不能用裸 turn 数判定：纯对话轮次（回答提问、只读核对）也会推进 turn，
	// 把它当"一段工作已结束"会误报（本插件实测误报过两次）。只有真的跑过工具
	// 的 turn 才算"有工作的会话"，用它作为兜底门槛。
	ctx.on("tools/result", ({ agent }) => {
		const state = stateOf(agent);
		state.toolActivity = true;
	});

	// ── 'agent/turn-stopping'：turn 即将关闭 → 检测完成信号 + 武装空闲兜底 ──
	ctx.on("agent/turn-stopping", ({ agent }) => {
		const state = stateOf(agent);
		if (state.toolActivity) {
			state.workTurns += 1;
			state.toolActivity = false;
		}

		// 显式完成信号
		if (cfg.completionSignals && !state.selfInjected) {
			const text = state.lastUserText.toLowerCase();
			if (text.length > 0) {
				const now = Date.now();
				const hit = cfg.signals.some((s) => text.includes(String(s).toLowerCase()));
				if (hit && now - state.lastTrigger >= cfg.debounceMs) {
					state.lastTrigger = now;
					state.signalFired = true;
					state.lastUserText = ""; // 一条用户消息只触发一次
					deliver(agent, buildReviewPrompt("检测到任务完成信号"));
				}
			}
		}

		// 空闲兜底：覆盖夜间无人值守——用户 kick-off 后离开，无人说"完成"。
		// 一旦被显式信号触发过就永久禁用本条路径，避免与信号路径重复打扰。
		if (!cfg.idleFallback) return;
		clearIdle(state);
		state.timer = setTimeout(() => {
			state.timer = undefined;
			if (state.idleFired || state.signalFired) return;
			// 门槛：本会话至少有过一轮「跑过工具」的工作，否则纯聊天不该被兜底打扰。
			if (state.workTurns < 1) return;
			state.idleFired = true;
			state.lastTrigger = Date.now();
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

export { INJECT };
