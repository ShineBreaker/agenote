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

import { spawn, spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { basename, join } from "node:path";

// ─── 注入预算（规格见 spec/injection.toml [hosts.dsh]）──────────────────────
// ─── BEGIN GENERATED: hosts.dsh.budgets ───
// 本块由 tools/codegen 从 spec/injection.toml 生成，请勿手改；改 spec 后重跑 python3 tools/codegen/generate.py
export const BRIEF_BUDGET = Number(process.env.AGENOTE_INJECTION_BRIEF_BUDGET || 8000);
export const RECALL_BUDGET = Number(process.env.AGENOTE_INJECTION_RECALL_BUDGET || 4000);
export const CLI_TIMEOUT_MS = 6000;
// ─── END GENERATED: hosts.dsh.budgets ───
// query 有效长度与截断上限（spec [append] 的镜像；CLI 侧仍是最终裁决）。
export const MIN_QUERY = Number(process.env.AGENOTE_INJECTION_MIN_QUERY || 6);
export const QUERY_MAX_CHARS = 200;

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
    cwd: options.cwd || undefined,
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

// ─── 注入：KB 域根 / 指纹 / query / context 正文 ─────────────────────────────
// 重写型宿主（DSH 每次请求重写动态上下文快照）的**进程内预检镜像**：指纹与
// query 只用于决定「能不能复用上次正文」，省一次 spawn；内容、裁剪与门槛始终
// 由 CLI 决定。追加型宿主的状态机（去重/累计/状态文件）在 `agenote inject`，
// 不在本文件，也不得在这里补一份。

/** KB agent 域根：context 只读 agent 域，指纹也须定位到 KB_ROOT/<agenote_dir>。 */
export function kbDomainRoot() {
  try {
    let kb = process.env.KB_ROOT || "";
    let agenoteDir = "agenote";
    if (!kb) {
      const cfgPath = join(xdgConfigHome(), "agenote", "config.toml");
      let section = "";
      for (const line of readFileSync(cfgPath, "utf-8").split("\n")) {
        // 值先吃掉引号串再匹配行尾注释（# 在引号内不被误剥）；手写 TOML 近似，
        // 解析失败整体回退默认——CLI 侧仍以其自身配置为最终裁决。
        const sec = line.match(/^\s*\[([^\]]+)\]\s*(?:#.*)?$/);
        if (sec) {
          section = sec[1];
          continue;
        }
        const kv =
          line.match(/^\s*([A-Za-z0-9_-]+)\s*=\s*"((?:[^"\\]|\\.)*)"\s*(?:#.*)?$/) ||
          line.match(/^\s*([A-Za-z0-9_-]+)\s*=\s*'([^']*)'\s*(?:#.*)?$/);
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

/** MEMORY.org 与 memories/ 的 mtime+size 指纹；KB 缺失返回空串（= 不注入）。 */
export function kbFingerprint() {
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

/**
 * query = prompt 折叠空白取前 200 字符 + 会话 cwd 的 basename 伪词。
 * 门槛按 prompt 部分判（伪词不计入），否则「继续」+ 项目名也能过门槛。
 * 不过门槛返回 null。
 */
export function buildQuery(prompt, cwd) {
  const qText = String(prompt || "")
    .split(/\s+/)
    .filter(Boolean)
    .join(" ")
    .slice(0, QUERY_MAX_CHARS);
  if (qText.trim().length < MIN_QUERY) return null;
  let pseudo = "";
  try {
    pseudo = basename(String(cwd || "").replace(/[\\/]+$/, ""));
  } catch {
    pseudo = "";
  }
  return pseudo && pseudo !== "." ? `${qText} ${pseudo}` : qText;
}

/**
 * 调 `agenote context` 取正文；非 ok / 解析失败 / CLI 失败一律空串。
 * 语义开关（总开关与 per-host 开关）由 CLI 裁决，输出零字节即天然不注入。
 */
export function contextContent(host, mode, budget, query = "", cwd = "") {
  const args = [
    "context",
    "--mode",
    mode,
    "--budget",
    String(budget),
    "--host",
    host,
    "--format",
    "json",
  ];
  if (query) args.push("--query", query);
  const res = runKb(args, { timeoutMs: CLI_TIMEOUT_MS, cwd });
  if (!res.ok) return "";
  try {
    const data = JSON.parse(res.stdout || "{}");
    return data && data.status === "ok" ? data.content || "" : "";
  } catch {
    return "";
  }
}

/**
 * 异步调用 agenote CLI：spawn + Promise，不阻塞宿主事件循环。
 *
 * 工具执行走这里（写盘可能等 safeio 锁，最长按秒计）；hooks 的只读调用仍用
 * runKb（spawnSync，毫秒级）。契约与 runKb 一致：数组传参、AGENOTE_AGENT 归因、
 * 非零退出即失败。stdin 显式写入后关闭；超时、abort、输出超限都会终止子进程。
 */
export function runKbAsync(args, options = {}) {
  const timeout = options.timeoutMs ?? 60_000;
  const maxBuffer = options.maxBuffer ?? 4 * 1024 * 1024;
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(KB_SCRIPT, args, {
        env: { ...process.env, AGENOTE_AGENT: options.agent ?? "dsh" },
        stdio: ["pipe", "pipe", "pipe"],
      });
    } catch (err) {
      resolve({ ok: false, stdout: "", stderr: String(err?.message ?? err) });
      return;
    }

    let stdout = "";
    let stderr = "";
    let settled = false;
    let overflow = false;

    const settle = (result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      options.signal?.removeEventListener("abort", onAbort);
      resolve(result);
    };
    const kill = () => child.kill("SIGKILL");
    const onAbort = () => kill();

    const timer = setTimeout(() => {
      kill();
      settle({ ok: false, stdout, stderr: stderr || `超时 ${timeout}ms` });
    }, timeout);
    options.signal?.addEventListener("abort", onAbort, { once: true });
    // 已取消的调用不再起跑：addEventListener 对已 aborted 的信号不回调。
    if (options.signal?.aborted) onAbort();
    // 子进程不读 stdin 时会 EPIPE，属正常收尾，不让它冒成未捕获异常。
    child.stdin.on("error", () => {});

    child.stdout.on("data", (chunk) => {
      stdout += chunk;
      if (stdout.length > maxBuffer) {
        overflow = true;
        kill();
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk;
      if (stderr.length > maxBuffer) {
        overflow = true;
        kill();
      }
    });
    child.on("error", (err) => {
      settle({ ok: false, stdout: "", stderr: String(err?.message ?? err) });
    });
    child.on("close", (code, signal) => {
      if (overflow) {
        settle({ ok: false, stdout, stderr: stderr || "输出超过 maxBuffer 上限" });
        return;
      }
      if (code !== 0) {
        const why = signal ? `被信号 ${signal} 终止` : `退出码 ${code}`;
        settle({ ok: false, stdout, stderr: stderr || why });
        return;
      }
      settle({ ok: true, stdout, stderr });
    });

    if (options.stdin !== undefined) child.stdin.end(options.stdin);
    else child.stdin.end();
  });
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

// ─── Config schema（Standard Schema v1 手搓子集）────────────────────────────

/**
 * 把校验节点包成 Cordis 认的 Config schema。
 *
 * Cordis 加载插件时读运行时的 `Config`，调 `Config["~standard"].validate(config)`
 * （duck-typing，见 cordis 的 resolveConfig）：同步返回 { value } 或
 * { issues: [{ message, path }] }——后者在加载期抛 ValidationError，非法配置
 * 因此"响亮失败"，不会带着错值跑起来。
 *
 * 这里复刻 @deepseek-ai/schemastery 3.x 的最小子集（object/array/string/
 * number/boolean + default + minimum/minLength），语义逐条对齐上游：
 *   - undefined/null 输入走 default，克隆后继续跑类型校验（对象默认值里的
 *     字段默认值由此逐层填上）；
 *   - 对象的未知键放行（上游非 strict 模式的 merge 行为）；
 *   - 数组逐项校验，issue 路径带下标。
 * 上游改语义需手动同步。不 import schemastery 的原因同 createUserMessage：
 * link: 部署下 Node 解析不到 $DSH_HOME/profiles/node_modules。
 */
function schemaNode(kind, extra) {
  const node = { kind };
  // 只收录"确实给了"的选项——`"default" in node` 即"有默认值"的判据
  for (const [key, value] of Object.entries(extra)) {
    if (value !== undefined) node[key] = value;
  }
  node["~standard"] = {
    version: 1,
    vendor: "dsh-agenote",
    validate(value) {
      const issues = [];
      const resolved = resolveNode(node, value, [], issues);
      return issues.length > 0 ? { issues } : { value: resolved };
    },
  };
  return node;
}

/** 递归校验节点；issues 收集全部问题（不止报第一个），返回值仅在无问题时使用。 */
function resolveNode(node, value, path, issues) {
  if (value === undefined || value === null) {
    if (!("default" in node)) return value;
    value = structuredClone(node.default);
  }
  switch (node.kind) {
    case "boolean":
      if (typeof value !== "boolean") {
        issues.push({ message: `应为布尔值，收到 ${JSON.stringify(value)}`, path });
        return undefined;
      }
      return value;
    case "number":
      if (typeof value !== "number" || !Number.isFinite(value)) {
        issues.push({
          message: `应为有限数字，收到 ${JSON.stringify(value)}`,
          path,
        });
        return undefined;
      }
      if (node.minimum !== undefined && value < node.minimum) {
        issues.push({ message: `应 >= ${node.minimum}，收到 ${value}`, path });
        return undefined;
      }
      return value;
    case "string":
      if (typeof value !== "string") {
        issues.push({
          message: `应为字符串，收到 ${JSON.stringify(value)}`,
          path,
        });
        return undefined;
      }
      return value;
    case "array":
      if (!Array.isArray(value)) {
        issues.push({
          message: `应为数组，收到 ${JSON.stringify(value)}`,
          path,
        });
        return undefined;
      }
      if (node.minLength !== undefined && value.length < node.minLength) {
        issues.push({
          message: `至少需要 ${node.minLength} 项，收到 ${value.length} 项`,
          path,
        });
        return undefined;
      }
      return value.map((item, index) =>
        resolveNode(node.item, item, [...path, index], issues),
      );
    case "object": {
      if (typeof value !== "object" || Array.isArray(value)) {
        issues.push({
          message: `应为对象，收到 ${JSON.stringify(value)}`,
          path,
        });
        return undefined;
      }
      // 未知键放行（对齐 schemastery 非 strict 模式），已知键逐项校验
      const result = { ...value };
      for (const [key, child] of Object.entries(node.shape)) {
        result[key] = resolveNode(child, value[key], [...path, key], issues);
      }
      return result;
    }
    default:
      throw new TypeError(`unknown schema kind: ${node.kind}`);
  }
}

/** 配置 schema 构造器（最小子集，按需扩充）。 */
export const schema = {
  boolean(defaultValue) {
    return schemaNode("boolean", { default: defaultValue });
  },
  number({ default: defaultValue, minimum } = {}) {
    return schemaNode("number", { default: defaultValue, minimum });
  },
  string() {
    return schemaNode("string", {});
  },
  array(item, { default: defaultValue, minLength } = {}) {
    return schemaNode("array", { item, default: defaultValue, minLength });
  },
  object(shape, { default: defaultValue } = {}) {
    return schemaNode("object", { shape, default: defaultValue });
  },
};
