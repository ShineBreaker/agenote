// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
//
// SPDX-License-Identifier: MIT

// agenote → opencode 记忆注入 plugin（recipe 级模板，设计 C4 W2）
//
// 挂点：`experimental.chat.messages.transform`（每次 LLM 请求前改写全量
// messages ≙ pi 的 context 事件）——重写型注入，每请求幂等：先剥离旧
// agenote 块再注新缓存块，compact 后自动恢复，无需追加型三件套（无累积
// 风险），只需指纹缓存（零 spawn 重放）。
//
// ⚠️ experimental API 漂移风险（设计 R2）：本模板对齐
// @opencode-ai/plugin 1.18.18 的 `experimental.chat.messages.transform`
// 签名 `({}, output: { messages: { info: Message; parts: Part[] }[] })`。
// experimental 前缀 API 无稳定性承诺，升级 opencode 后如失效，观察新版
// d.ts 再迁；失效表现为「无注入」，不影响会话本身。
//
// 安装：拷到 `~/.config/opencode/plugin/agenote-context.ts`（全局）或项目
// `.opencode/plugin/`，免注册自动加载。前置：agenote CLI 含 context 子命令。
// 语义开关在 agenote SCHEMA [injection]（AGENOTE_INJECTION_ENABLED=false 一键全关）。
//
// 验收：`~/.local/share/opencode/log/opencode.log` 观察插件加载；会话内
// 提问记忆相关问题验证召回内容在场（详见 injectors/opencode/README.md）。

import { spawnSync } from "node:child_process";
import { readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";
import type { Plugin } from "@opencode-ai/plugin";

// ── 配置（env 覆盖；与 bash/mjs 注入器同款旋钮）─────────────────────────────
const BRIEF_BUDGET = Number(process.env.AGENOTE_INJECTION_BRIEF_BUDGET || 8000);
const CLI_TIMEOUT_MS = 6000;

// ── KB agent 域根（镜像 CLI 解析：KB_ROOT env > config.toml > 默认）──────────
function kbDomainRoot(): string {
  try {
    let kb = process.env.KB_ROOT || "";
    let agenoteDir = "agenote";
    if (!kb) {
      const cfgPath = join(
        process.env.XDG_CONFIG_HOME || join(homedir(), ".config"),
        "agenote",
        "config.toml",
      );
      let section = "";
      for (const line of readFileSync(cfgPath, "utf-8").split("\n")) {
        const sec = line.match(/^\s*\[([^\]]+)\]\s*$/);
        if (sec) {
          section = sec[1];
          continue;
        }
        const kv = line.match(/^\s*([A-Za-z0-9_-]+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*$/);
        if (kv && section === "paths") {
          if (kv[1] === "kb_root") kb = kv[2];
          if (kv[1] === "agenote_dir") agenoteDir = kv[2] || "agenote";
        }
      }
    }
    if (!kb) kb = join(homedir(), "Documents", "Org");
    return join(kb.replace(/^~(?=\/|$)/, homedir()), agenoteDir);
  } catch {
    return join(homedir(), "Documents", "Org", "agenote");
  }
}

// MEMORY.org 与 memories/ 的 mtime+size 指纹（设计 D8）；KB 缺失返回空串
function kbFingerprint(): string {
  try {
    const root = kbDomainRoot();
    const st = statSync(join(root, "MEMORY.org"));
    let fp = `${Math.floor(st.mtimeMs)}:${st.size}`;
    try {
      const dir = statSync(join(root, "memories"));
      fp += `|${Math.floor(dir.mtimeMs)}:${dir.size}`;
    } catch {
      fp += "|-";
    }
    return fp;
  } catch {
    return "";
  }
}

// `agenote context --mode session`（缺失/失败/非 ok 一律空串 = 不注）
// argv 全静态字面量；预算经 env 传递（AGENOTE_INJECTION_DEFAULT_BUDGET，
// 值已被上面的正则锁为纯数字），杜绝动态值被 argparse 当额外选项的窗口。
function contextContent(): string {
  try {
    const out = spawnSync(
      "agenote",
      ["context", "--mode", "session", "--host", "opencode", "--format", "json"],
      {
        encoding: "utf-8",
        timeout: CLI_TIMEOUT_MS,
        env: {
          ...process.env,
          AGENOTE_AGENT: "opencode",
          AGENOTE_INJECTION_DEFAULT_BUDGET: String(BRIEF_BUDGET),
        },
      },
    );
    const data = JSON.parse((out.stdout || "") || "{}");
    return data && data.status === "ok" ? data.content || "" : "";
  } catch {
    return "";
  }
}

// 进程内指纹缓存：{fp, content}（每请求重放零 spawn；KB 更新自动失效重跑）
const cache: { fp: string; content: string } = { fp: "", content: "" };

function cachedBrief(): string {
  const fp = kbFingerprint();
  if (!fp) return "";
  if (cache.fp === fp) return cache.content;
  const content = contextContent();
  cache.fp = fp;
  cache.content = content; // disabled/empty 的空串也缓存，避免每请求 spawn
  return content;
}

// 幂等改写：先从旧文本剥掉整个 agenote 块（非贪婪到块尾标记），再把新缓存块
// 接到末条 user 文本尾部。剥块用正则而非删 part——块可能混在用户文本里，
// 删 part 会连用户输入一起丢。
const BLOCK_RE = /<!-- agenote-context[\s\S]*?<!-- \/agenote-context -->/g;

function injectIntoParts(parts: any[], block: string): void {
  const last = parts[parts.length - 1];
  if (!last || typeof last.text !== "string") return; // 只注纯文本尾块
  const base = last.text.replace(BLOCK_RE, "").trimEnd();
  last.text = `${base}\n\n<!-- agenote-context block →\n${block}\n← /agenote-context -->`;
}

export const AgenoteContextPlugin: Plugin = async (_input) => {
  return {
    // experimental API（1.18.18）：每 LLM 请求前改写全量 messages，原地变更 output
    "experimental.chat.messages.transform": async (_input, output) => {
      const block = cachedBrief();
      if (!block || !Array.isArray(output?.messages)) return;
      // 只改最后一条 user 消息（注入点固定，重放天然幂等）
      for (let i = output.messages.length - 1; i >= 0; i--) {
        const m = output.messages[i];
        const info: any = m?.info ?? m;
        if (info?.role === "user") {
          injectIntoParts(m.parts ?? [], block);
          break;
        }
      }
    },
  };
};

export default AgenoteContextPlugin;
