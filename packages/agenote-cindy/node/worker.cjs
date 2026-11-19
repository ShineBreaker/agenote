// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
//
// SPDX-License-Identifier: MIT

/**
 * agenote 的 Cindy 适配插件 —— 随包 Node 工作进程。
 *
 * 只做两件事：把宿主事件翻译成一次 agenote CLI 调用（context），以及为插件
 * 工具执行只读子命令（run）。注入状态机（指纹 / query 去重 / 单会话累计
 * 预算）只有 CLI 一份实现，本文件不复制它。
 */

const { execFileSync } = require("node:child_process");
const readline = require("node:readline");

/** 归因：所有 CLI 调用都必须带，否则卡片会记到 CLI 的默认 agent 名下。 */
const AGENT_NAME = "cindy";

const BIN_CANDIDATES = [process.env.AGENOTE_BIN, "agenote", "agenote-cli"].filter(Boolean);

/** 插件工具允许透传的子命令白名单（只读入口，不是通用 shell）。 */
const ALLOWED_SUBCOMMANDS = new Set([
  "search",
  "health",
  "list",
  "get",
  "memory",
  "stats",
  "fields",
  "tags",
  "gaps",
  "review",
]);

const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;

let resolvedBin = null;
let hostSupport = null; // 'cindy' | 'generic'
let injectSupported = null; // boolean
const injected = new Map(); // sessionId -> Set(contentHash)

function reply(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function intOr(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(Math.trunc(parsed), min), max);
}

/** djb2：只用于会话内去重，不需要抗碰撞。 */
function hash(text) {
  let h = 5381;
  for (let i = 0; i < text.length; i += 1) h = ((h << 5) + h + text.charCodeAt(i)) | 0;
  return String(h >>> 0) + ":" + text.length;
}

function seenSet(sessionId) {
  let set = injected.get(sessionId);
  if (!set) {
    set = new Set();
    injected.set(sessionId, set);
    if (injected.size > 200) {
      for (const key of [...injected.keys()].slice(0, 100)) injected.delete(key);
    }
  }
  return set;
}

/** 定位 CLI：缓存首个能跑起来的候选。 */
function findBin() {
  if (resolvedBin) return resolvedBin;
  for (const candidate of BIN_CANDIDATES) {
    try {
      execFileSync(candidate, ["--version"], {
        timeout: 4000,
        stdio: ["ignore", "pipe", "pipe"],
        env: { ...process.env, AGENOTE_AGENT: AGENT_NAME },
      });
      resolvedBin = candidate;
      return resolvedBin;
    } catch {
      // 试下一个候选
    }
  }
  return null;
}

function hostName() {
  return hostSupport || "cindy";
}

/**
 * 跑一次 CLI。返回 {ok, stdout, stderr, code}。
 * 旧版 CLI 的 --host 是硬编码 choices，遇到「invalid choice」时退回 generic 并记住。
 */
function runCli(args, timeoutMs) {
  const bin = findBin();
  if (!bin) return { ok: false, code: -1, stdout: "", stderr: "agenote CLI 不在 PATH 里" };
  const env = { ...process.env, AGENOTE_AGENT: AGENT_NAME };
  const attempt = (argv) => {
    try {
      const stdout = execFileSync(bin, argv, {
        timeout: timeoutMs,
        maxBuffer: MAX_OUTPUT_BYTES,
        stdio: ["ignore", "pipe", "pipe"],
        env,
      });
      return { ok: true, code: 0, stdout: String(stdout), stderr: "" };
    } catch (err) {
      return {
        ok: false,
        code: typeof err.status === "number" ? err.status : -1,
        stdout: err.stdout ? String(err.stdout) : "",
        stderr: err.stderr ? String(err.stderr) : String(err.message || err),
      };
    }
  };

  let result = attempt(args);
  if (result.ok) return result;

  const rejectedHost =
    args.includes("--host") &&
    hostSupport !== "generic" &&
    result.code === 2 &&
    /invalid choice/.test(result.stderr) &&
    result.stderr.includes("'cindy'");
  if (rejectedHost) {
    hostSupport = "generic";
    const retry = args.map((arg) => (arg === "cindy" ? "generic" : arg));
    result = attempt(retry);
  }
  return result;
}

function injectAvailable(timeoutMs) {
  if (injectSupported !== null) return injectSupported;
  const probe = runCli(
    ["inject", "--host", hostName(), "--event", "prompt", "--prompt", "", "--format", "json"],
    timeoutMs,
  );
  injectSupported = probe.ok || probe.code !== 2 || !/invalid choice/.test(probe.stderr);
  return injectSupported;
}

/** 会话简报：CLI 有 inject 就走状态机（指纹缓存由 CLI 负责），否则退回 context。 */
function sessionBrief(sessionId, budget, timeoutMs) {
  if (injectAvailable(timeoutMs)) {
    const result = runCli(
      [
        "inject",
        "--host",
        hostName(),
        "--session-id",
        sessionId,
        "--event",
        "session",
        "--budget",
        String(budget),
      ],
      timeoutMs,
    );
    return result.ok ? result.stdout : "";
  }
  const result = runCli(
    [
      "context",
      "--host",
      hostName(),
      "--mode",
      "session",
      "--budget",
      String(budget),
    ],
    timeoutMs,
  );
  return result.ok ? result.stdout : "";
}

/** 按轮召回：只有 CLI 带 inject 时才有；旧 CLI 不做次优近似。 */
function promptRecall(sessionId, prompt, budget, timeoutMs) {
  if (!injectAvailable(timeoutMs)) return "";
  const result = runCli(
    [
      "inject",
      "--host",
      hostName(),
      "--session-id",
      sessionId,
      "--event",
      "prompt",
      "--prompt",
      prompt,
      "--budget",
      String(budget),
    ],
    timeoutMs,
  );
  return result.ok ? result.stdout : "";
}

function collect(sessionId, text) {
  const body = String(text || "").trim();
  if (!body) return "";
  const set = seenSet(sessionId);
  const key = hash(body);
  if (set.has(key)) return "";
  set.add(key);
  return body;
}

function handleContext(params) {
  const sessionId = String(params.sessionId || "");
  const prompt = String(params.prompt || "");
  const callTimeout = intOr(params.timeoutMs, 1200, 300, 20000);
  const budgetMs = intOr(params.budgetMs, 2400, 500, 20000);
  const briefBudget = intOr(params.briefBudget, 8000, 200, 32000);
  const recallBudget = intOr(params.recallBudget, 4000, 200, 32000);

  // 钩子只有 3 秒裁决窗口：把总预算摊到两次调用上，宁可少注一次也不让裁决超时
  // （超时 = 宿主按 allow 放行，本轮白跑还拖慢用户）。
  const startedAt = Date.now();
  const remaining = () => budgetMs - (Date.now() - startedAt) - 100;
  const timeoutFor = (extra) => {
    const left = remaining();
    if (left < 200) return 0;
    return Math.min(callTimeout, extra ? Math.min(extra, left) : left);
  };

  const parts = [];
  if (params.first) {
    const timeout = timeoutFor(0);
    if (timeout > 0) {
      const brief = collect(sessionId, sessionBrief(sessionId, briefBudget, timeout));
      if (brief) parts.push(brief);
    }
  }
  if (prompt) {
    const timeout = timeoutFor(1200);
    if (timeout > 0) {
      const recall = collect(sessionId, promptRecall(sessionId, prompt, recallBudget, timeout));
      if (recall) parts.push(recall);
    }
  }
  return { text: parts.join("\n\n"), parts: parts.length };
}

function handleRun(params) {
  const argv = Array.isArray(params.argv) ? params.argv.map(String) : [];
  if (!argv.length || !ALLOWED_SUBCOMMANDS.has(argv[0])) {
    return { ok: false, message: "不支持的子命令：只允许只读查询入口。" };
  }
  if (argv.length > 16 || argv.some((arg) => arg.length > 512)) {
    return { ok: false, message: "参数过多或过长。" };
  }
  const timeoutMs = intOr(params.timeoutMs, 15000, 1000, 60000);
  const result = runCli(argv, timeoutMs);
  if (!result.ok) {
    const detail = (result.stderr || "").trim().split("\n").slice(-3).join(" ");
    return { ok: false, message: `agenote ${argv[0]} 失败（rc=${result.code}）：${detail || "无输出"}` };
  }
  return { ok: true, output: result.stdout.trim() };
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let request;
  try {
    request = JSON.parse(line);
  } catch {
    reply({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
    return;
  }

  try {
    if (request.method === "ping") {
      reply({ jsonrpc: "2.0", id: request.id, result: { running: true, bin: findBin() } });
      return;
    }
    if (request.method === "context") {
      reply({ jsonrpc: "2.0", id: request.id, result: handleContext(request.params || {}) });
      return;
    }
    if (request.method === "run") {
      reply({ jsonrpc: "2.0", id: request.id, result: handleRun(request.params || {}) });
      return;
    }
    reply({ jsonrpc: "2.0", id: request.id, error: { code: -32601, message: "Method not found" } });
  } catch (err) {
    // 任何异常都只能是「本轮不注入」，不能让 worker 崩溃拖垮宿主钩子。
    reply({
      jsonrpc: "2.0",
      id: request.id,
      result: { text: "", ok: false, message: String((err && err.message) || err) },
    });
  }
});
