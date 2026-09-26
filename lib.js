// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — 共享工具层。
//
// 本包经 link: 部署时，模块 realpath 落在仓库源码目录，Node 的 node_modules
// 父级检索够不到 $DSH_HOME/profiles/node_modules 共享 fallback，因此
// @deepseek-ai/* 一律不导入；这里只用 node: 内置模块。
//
// 职责边界：本层只做「进程调用 + 文本解析」的纯机械工作，不含任何行为规范。
// 信号清单、写入流程、卡片格式由 agenote-{base,curator,review} skill 定义。

import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

// ─── CLI 调用 ───────────────────────────────────────────────────────────────

/**
 * 解析 CLI 可执行名：AGENOTE_BIN 是环境变量，可被外部注入——裸采用等于
 * 让环境决定执行哪个程序。只接受指向「已存在的可执行普通文件」的路径
 * （覆盖 shim 的正当用法），其余一律回退 "agenote" 走 PATH 解析。
 */
function resolveKbScript() {
  const fromEnv = process.env.AGENOTE_BIN?.trim();
  if (fromEnv) {
    try {
      const st = statSync(fromEnv);
      if (st.isFile() && st.mode & 0o111) return fromEnv;
    } catch {
      // 路径不存在 / 不可访问 → 回退内置名
    }
  }
  return "agenote";
}

/** CLI 可执行名。可用环境变量 AGENOTE_BIN 指向源码仓库 shim（须为可执行文件路径）。 */
export const KB_SCRIPT = resolveKbScript();

/**
 * 调用 agenote CLI，返回 { ok, stdout, stderr }。
 *
 * 一律带 AGENOTE_AGENT=dsh 前缀：该变量只给卡片打归因标签，不做写入隔离，
 * 但不带的话归因会错误落到默认 agent（omp）。
 *
 * spawnSync 走数组传参而非 shell 字符串拼接——参数里的空格/引号不会被
 * shell 解释，也就不存在注入面。
 */
export function runKb(args, options = {}) {
  const timeout = options.timeoutMs ?? 30_000;
  const res = spawnSync(KB_SCRIPT, args, {
    encoding: "utf8",
    timeout,
    maxBuffer: options.maxBuffer ?? 4 * 1024 * 1024,
    env: { ...process.env, AGENOTE_AGENT: options.agent ?? "dsh" },
  });
  if (res.error) {
    return {
      ok: false,
      stdout: "",
      stderr: String(res.error.message ?? res.error),
    };
  }
  const stdout = res.stdout ?? "";
  const stderr = res.stderr ?? "";
  // agenote CLI 用退出码表达成败；非零即失败（lint --check 亦如此，故仅作提示用）。
  if (res.status !== 0) {
    return { ok: false, stdout, stderr: stderr || `退出码 ${res.status}` };
  }
  return { ok: true, stdout, stderr };
}

/** 取 CLI 输出的一行摘要，失败时返回单行错误说明（用于命令回显）。 */
export function runKbText(args, options = {}) {
  const res = runKb(args, options);
  if (!res.ok)
    return `(agenote ${args.join(" ")} 失败: ${firstLine(res.stderr)})`;
  return res.stdout.trimEnd();
}

function firstLine(text) {
  const line = String(text)
    .split("\n")
    .find((l) => l.trim().length > 0);
  return line ? line.trim() : "(无输出)";
}

// ─── 健康度摘要 ─────────────────────────────────────────────────────────────

/**
 * 从 `agenote health` 输出里挑出值得放进 system prompt 的行。
 *
 * 依赖自由文本措辞（与 agenote-el 对 CLI 输出的耦合同类）；CLI 改措辞会
 * 静默少注入几行，但不会报错——失败降级为「不注入」，绝不影响会话启动。
 */
export function healthSummary(run = runKb) {
  const res = run(["health"], { timeoutMs: 15_000 });
  if (!res.ok) return "";
  const lines = res.stdout.split("\n");
  const picked = [];
  for (const raw of lines) {
    const line = raw.trim();
    if (line.length === 0) continue;
    if (
      line.startsWith("总卡片") ||
      line.startsWith("孤立率") ||
      line.startsWith("过时率") ||
      line.startsWith("类型偏斜") ||
      line.startsWith("薄弱类别") ||
      line.startsWith("feedback:") ||
      line.startsWith("project:")
    ) {
      picked.push(`  ${line}`);
    }
  }
  if (picked.length === 0) return "";
  return ["[agenote] 知识库状态:", ...picked].join("\n");
}

// ─── 消息构造 ───────────────────────────────────────────────────────────────

function deepFreeze(value) {
  if (value === null || typeof value !== "object") return value;
  for (const key of Object.keys(value)) deepFreeze(value[key]);
  return Object.freeze(value);
}

/**
 * 构造一条 UserMessage —— 复刻 @deepseek-ai/dsh-llm 的 createUserMessage 语义
 * （id + structuredClone + deepFreeze）。
 *
 * 为什么复刻而非 import：本包经 link: 部署，模块 realpath 落在源码目录，
 * 解析不到 $DSH_HOME/profiles/node_modules；上游改语义需手动同步。
 */
export function createUserMessage(input) {
  return deepFreeze(
    structuredClone({ ...input, role: "user", id: randomUUID() }),
  );
}

// ─── 会话配置 ───────────────────────────────────────────────────────────────

export function xdgConfigHome() {
  return process.env.XDG_CONFIG_HOME || join(homedir(), ".config");
}

/**
 * 与 omp/pi 扩展共享的配置文件（单一真相源）。
 *
 * 复用同一个 JSON 是为了让「信号清单」这类行为参数只维护一份；读不到就
 * 用内置默认值，cordis 行的 config 始终可以覆盖。
 */
export function sharedConfig() {
  try {
    return (
      JSON.parse(
        readFileSync(
          join(xdgConfigHome(), "omp", "agenote-hooks.json"),
          "utf8",
        ),
      ) ?? {}
    );
  } catch {
    return {};
  }
}
