// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — commands 半边：斜杠命令快捷入口。
//
// 与 agenote-pi 的三个命令对等（/agenote-summarize、/agenote-curate、
// /agenote-health）。前两个不直接跑 CLI——流程由 agent 按对应 skill 主导，
// 命令只把任务提示投进会话；只有 /agenote-health 是纯只读、直接回显 CLI 输出。

import { buildCuratePrompt, buildReviewPrompt } from "./hooks.js";
import { createUserMessage, runKb } from "./lib.js";

export const name = "agenote-commands";

export const inject = ["commands"];

/** 提示注入用同一个投递路径（followup 排一轮后续 turn 并唤醒 driver）。 */
function deliver(agent, text, plugin) {
	agent.followup(
		createUserMessage({
			content: [{ type: "text", text }],
			source: { kind: "plugin", plugin },
		}),
	);
}

export function apply(ctx, config = {}) {
	if (config.enabled === false) return;

	// ── /agenote-summarize ──
	// 不开新会话——直接注入当前会话，让有完整上下文的 agent 做总结。
	ctx.commands.register({
		definitionId: "dsh-agenote.summarize",
		name: "agenote-summarize",
		description: "在当前会话触发 agenote 经验总结 + 留痕（按 agenote-review skill 执行）",
		handler: (invocation) => {
			deliver(invocation.agent, buildReviewPrompt("用户手动触发经验总结"), "agenote-summarize");
			return { kind: "success", text: "已注入经验总结提示，请查看下一轮对话。" };
		},
	});

	// ── /agenote-curate ──
	ctx.commands.register({
		definitionId: "dsh-agenote.curate",
		name: "agenote-curate",
		description: "在当前会话触发 KB 策展（按 agenote-curator skill 执行）",
		handler: (invocation) => {
			deliver(invocation.agent, buildCuratePrompt("用户手动触发策展"), "agenote-curate");
			return { kind: "success", text: "已注入策展任务提示，请查看下一轮对话。" };
		},
	});

	// ── /agenote-health ──
	// 纯只读查询，直接回显——不必绕一圈让 agent 再调 CLI。
	ctx.commands.register({
		definitionId: "dsh-agenote.health",
		name: "agenote-health",
		description: "显示 agenote 知识库健康度报告",
		handler: () => {
			const res = runKb(["health"], { timeoutMs: 20_000 });
			if (!res.ok) {
				return { kind: "error", text: `agenote health 执行失败：${res.stderr.trim() || "未知错误"}` };
			}
			return { kind: "success", text: res.stdout.trimEnd() };
		},
	});
}
