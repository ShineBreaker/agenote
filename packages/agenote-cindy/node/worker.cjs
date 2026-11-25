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
const ALLOWED_SUBCOMMANDS = new Set(["search", "health"]);

const MAX_OUTPUT_BYTES = 4 * 1024 * 1024;
// Cindy 的 Node 桥对完整 JSON-RPC 单行有 1 MiB 上限；JSON 字符串中的控制
// 字符最多会膨胀为六字节转义，所以原始 stdout 只放到 128 KiB，给包络留余量。
const MAX_RPC_OUTPUT_BYTES = 128 * 1024;
const OUTPUT_TRUNCATION_MARKER = "\n...[output truncated]";

let resolvedBin = null;
let hostSupport = null; // 'cindy' | 'generic'
let injectSupported = null; // boolean

function reply(message) {
  process.stdout.write(JSON.stringify(message) + "\n");
}

function intOr(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(Math.max(Math.trunc(parsed), min), max);
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

function truncateOutput(value) {
  const text = String(value || "");
  const bytes = Buffer.from(text, "utf8");
  if (bytes.length <= MAX_RPC_OUTPUT_BYTES) return text;
  const limit = MAX_RPC_OUTPUT_BYTES - Buffer.byteLength(OUTPUT_TRUNCATION_MARKER, "utf8");
  let end = Math.max(0, Math.min(limit, bytes.length));
  // 不把 UTF-8 多字节字符截成 U+FFFD；边界最多回退 3 字节。
  while (end > 0 && (bytes[end - 1] & 0xc0) === 0x80) end -= 1;
  if (end > 0) {
    const lead = bytes[end - 1];
    const width = lead >= 0xf0 ? 4 : lead >= 0xe0 ? 3 : lead >= 0xc0 ? 2 : 1;
    const charEnd = end - 1 + width;
    end = charEnd <= limit ? charEnd : end - 1;
  }
  return `${bytes.subarray(0, end).toString("utf8")}${OUTPUT_TRUNCATION_MARKER}`;
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

  const hostIndex = args.indexOf("--host");
  const inlineHostIndex = args.findIndex((arg) => arg === "--host=cindy");
  const usesCindyHost =
    (hostIndex >= 0 && args[hostIndex + 1] === "cindy") || inlineHostIndex >= 0;
  const rejectedHost =
    hostSupport !== "generic" &&
    result.code === 2 &&
    /invalid choice/i.test(result.stderr) &&
    /["']cindy["']/.test(result.stderr) &&
    usesCindyHost;
  if (rejectedHost) {
    hostSupport = "generic";
    const retry = args.slice();
    if (hostIndex >= 0 && retry[hostIndex + 1] === "cindy") {
      retry[hostIndex + 1] = "generic";
    } else if (inlineHostIndex >= 0) {
      retry[inlineHostIndex] = "--host=generic";
    }
    result = attempt(retry);
  }
  return result;
}

function injectAvailable(timeoutMs) {
  if (injectSupported !== null) return injectSupported;
  if (!findBin()) {
    injectSupported = false;
    return injectSupported;
  }
  // 只探测帮助，不执行空 prompt，避免探针在 KB 缓存目录留下无会话状态文件。
  const probe = runCli(["inject", "--help"], timeoutMs);
  injectSupported = probe.ok;
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
      const brief = String(sessionBrief(sessionId, briefBudget, timeout) || "").trim();
      if (brief) parts.push(brief);
    }
  }
  if (prompt) {
    const timeout = timeoutFor(1200);
    if (timeout > 0) {
      const recall = String(promptRecall(sessionId, prompt, recallBudget, timeout) || "").trim();
      if (recall) parts.push(recall);
    }
  }
  // context 也走同一条 JSON-RPC 单行，不能只限制 run 工具的输出。
  return { text: truncateOutput(parts.join("\n\n")), parts: parts.length };
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
    const detail = truncateOutput((result.stderr || "").trim().split("\n").slice(-3).join(" "));
    return { ok: false, message: `agenote ${argv[0]} 失败（rc=${result.code}）：${detail || "无输出"}` };
  }
  return { ok: true, output: truncateOutput(result.stdout.trim()) };
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
