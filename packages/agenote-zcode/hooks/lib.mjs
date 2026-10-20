// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
//
// SPDX-License-Identifier: MIT

// agenote-zcode hooks 共享库 — `agenote inject` 的薄协议适配层
//
// 注入策略（指纹缓存、recall 门槛、单会话累计预算、状态文件、会话重置）全部在
// CLI 的 `agenote inject` 内（ADR 0005：三件套曾有四份语言副本，现在只有一份）。
// 本文件只负责：把宿主事件转成 CLI 参数、解析 JSON、返回正文。
//
// 语义开关仍由 CLI 裁决（AGENOTE_INJECTION_ENABLED / [injection.hosts]）；
// 本库只保留每宿主预算（生成块）与 spawn 超时。
//
// 手动冒烟：
//   node -e 'import("./lib.mjs").then(m => console.log(m.recallTurn("t", process.cwd(), "查询文本足够长")))'

import { spawnSync } from "node:child_process";

/** 单次预算（字符）：简报 8000 / recall 4000（spec/injection.toml） */
// ─── BEGIN GENERATED: hosts.zcode.budgets ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
export const BRIEF_BUDGET = Number(process.env.AGENOTE_INJECTION_BRIEF_BUDGET || 8000);
export const RECALL_BUDGET = Number(process.env.AGENOTE_INJECTION_RECALL_BUDGET || 4000);
const CLI_TIMEOUT_MS = 6000;
// ─── END GENERATED: hosts.zcode.budgets ───

/**
 * 调 `agenote inject` 取正文；CLI 缺失/失败/非 inject 态一律空串。
 * cwd 传给 CLI 进程（session 模式的 P 类项目匹配与 query 伪词都靠它）。
 */
function injectContent(host, event, sessionId, prompt = "", cwd = "") {
  const args = [
    "inject",
    "--host",
    host,
    "--session-id",
    String(sessionId || ""),
    "--event",
    event,
    "--format",
    "json",
  ];
  if (event === "prompt") {
    args.push("--prompt", String(prompt || ""), "--budget", String(RECALL_BUDGET));
  } else {
    args.push("--budget", String(BRIEF_BUDGET));
  }
  try {
    const out = spawnSync("agenote", args, {
      encoding: "utf-8",
      timeout: CLI_TIMEOUT_MS,
      cwd: cwd || undefined,
      env: { ...process.env, AGENOTE_AGENT: host },
    });
    const data = JSON.parse((out.stdout || "") || "{}");
    return data && data.action === "inject" ? data.content || "" : "";
  } catch {
    return "";
  }
}

/** 会话通道（SessionStart/compact）：CLI 侧完成状态重置 + 简报缓存重放。 */
export function sessionBrief(sessionId, cwd) {
  return injectContent("zcode", "session", sessionId, "", cwd);
}

/** 每轮通道（UserPromptSubmit）：CLI 侧完成三件套判定。 */
export function recallTurn(sessionId, cwd, prompt) {
  return injectContent("zcode", "prompt", sessionId, prompt, cwd);
}
