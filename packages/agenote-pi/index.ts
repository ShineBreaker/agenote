// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT

/**
 * agenote-hooks extension
 *
 * agent 专属记事本（agenote）集成钩子：
 * - session_start: 重置触发状态（健康度摘要由 pi-ui 扩展在欢迎框中显示，不经本插件注入）
 * - context:       每 LLM 调用前注入 agenote 记忆简报（指纹缓存幂等重写，先剥旧块再注新块）
 * - agent_end:     检测"任务完成信号"，命中时注入 agenote-review 评估提示（含留痕）
 * - /agenote-summarize: 在当前会话触发经验总结 + 留痕
 * - /agenote-curate:    注入策展任务提示（agent 按 agenote-curator skill 主导执行）
 * - /agenote-health:    显示 agenote 健康度报告
 *
 * 信号清单、写入流程、卡片格式由 agenote-{base,curator,review} skill 提供，
 * 本插件只做"事件触发 + 命令快捷入口"，避免与 skill 重复维护。
 *
 * 调用路径：agent 主循环通过 bash 调用 agenote CLI。本插件注入的提示词由
 * agent 执行；execSync 仅限只读 CLI 查询（/agenote-health 与 context 简报），
 * 走轻量 CLI shim（agenote-cli）。
 */

import { execSync } from "node:child_process";
import {
  appendFileSync,
  existsSync,
  readdirSync,
  readFileSync,
  statSync,
} from "node:fs";
import { homedir } from "node:os";
import { join, resolve } from "node:path";

const KB_SCRIPT = "agenote-cli";

// ─── 加载错误日志（omp 默认静默吞掉扩展错误，这里显式留痕）────────────────────
// omp 18：agent dir = <PI_CONFIG_DIR|默认 .config/omp>/agent（多一层 agent/）
const LOG_FILE = join(
  homedir(),
  process.env.PI_CONFIG_DIR || ".config/omp",
  "agent",
  "extensions",
  ".load-errors.log",
);
function logLoadError(ext: string, where: string, err: unknown): void {
  const msg =
    err instanceof Error ? `${err.message}\n${err.stack ?? ""}` : String(err);
  appendFileSync(
    LOG_FILE,
    `[${new Date().toISOString()}] [${ext}] ${where}: ${msg}\n`,
  );
}

/**
 * 任务完成信号（取自 agenote-review skill references/triggers.md — 单一真相源）
 * 收紧到强完成词，避免"好了"等高频词误触发。
 * 改动此处需同步 agenote-review/references/triggers.md。
 */
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

/** 注入到下一轮的 agenote-review 评估提示（含留痕环节）。reason 说明触发来源。 */
function buildReviewPrompt(reason: string): string {
  return [
    `<agenote-hook>${reason}，请按 agenote-review skill 流程评估本次对话：`,
    "（注意：这有可能是误报，如果当前任务没有完成的话，请忽略）",
    "1. 是否有可记录的经验信号（bug/踩坑/更优方案/用户纠正/项目决策）？",
    "2. 如有 → 通过 agenote CLI 写入（agenote add / agenote memory --add；调用时带 AGENOTE_AGENT=pi 前缀）",
    "3. 本轮用到的资料留痕：已有卡片 agenote touch，联网新知识 agenote add（type=note）",
    "4. 如无 → 明确回复'本次无可记录经验'</agenote-hook>",
  ].join("\n");
}

/** 注入到下一轮的策展任务提示——由 agent 按 agenote-curator skill 主导执行 */
function buildCuratePrompt(reason: string): string {
  return [
    `<agenote-hook>${reason}，请按 agenote-curator skill 流程对知识库执行策展：`,
    "（CLI 只提供检测报告与原子命令，流程编排与去留决策由你执行；先看候选清单，核实后再写盘）",
    "1. 诊断：agenote health --quality --duplicates / stats / gaps",
    "2. 状态重整：list --unused-days 找降级候选、archive --stale 找归档候选，逐项审查后显式 update/archive",
    "3. 去重合并 / type 聚拢 / 矛盾调和 / memory 维护（规则见 skill）",
    "4. 可选：reconcile + dream 综合（值得沉淀的候选用 agenote add 写入）",
    "5. 收尾：reindex + lint --fix + agenote commit（策展产物），输出策展报告</agenote-hook>",
  ].join("\n");
}

/** 显式完成信号触发的防抖冷却期：同一信号在冷却期内不重复触发 */
const DEBOUNCE_MS = 5 * 60 * 1000; // 5 分钟
/** 空闲兜底：会话连续空闲超过此时长且本会话从未触发过总结 → 触发一次（覆盖夜间无人值守） */
const IDLE_FALLBACK_MS = 5 * 60 * 1000; // 5 分钟

let lastTriggerTime = 0;
/** 本会话是否已通过显式信号触发过总结（true 后禁用空闲兜底，避免重复打扰） */
let signalTriggered = false;
/** 本会话是否已触发过空闲兜底（至多一次） */
let idleFallbackFired = false;
/** 已处理的 agent_end 轮次计数（用于判断是否有真实工作） */
let turnCount = 0;
/** 空闲兜底定时器 */
let idleTimer: ReturnType<typeof setTimeout> | undefined;
/** 本扩展注入的 review 提示的标识符——用于排除自注入消息，断开自触发反馈环 */
const HOOK_MARKER = "<agenote-hook>";

/** 运行 agenote-cli 命令并返回 stdout
 *
 * agenote-cli 是轻量 CLI shim entry point（由 agenote 包的 console_scripts 产出），
 * 复用 agenote 内核，输出人类可读文本。本插件（ExtensionAPI 无 MCP 调用接口）
 * 只能 execSync 外部进程，故走此 shim。
 */
function runKb(...args: string[]): string {
  try {
    return execSync(`${KB_SCRIPT} ${args.join(" ")}`, {
      encoding: "utf-8",
      timeout: 30000,
      stdio: ["pipe", "pipe", "pipe"],
    }).trim();
  } catch (err: any) {
    return `(agenote-cli 命令失败: ${err.message?.split("\n")[0] || err})`;
  }
}

/** 获取简短的 agenote 状态摘要（session_start 注入用） */
function getAgenoteStatusSummary(): string {
  const health = runKb("health");
  if (health.startsWith("(agenote")) return ""; // 命令失败，静默

  const lines = health.split("\n");
  const summary: string[] = ["[agenote] 记事本状态:"];

  for (const line of lines) {
    const trimmed = line.trim();
    if (
      trimmed.startsWith("总卡片:") ||
      trimmed.startsWith("孤立率:") ||
      trimmed.startsWith("过时率:") ||
      trimmed.startsWith("stale") ||
      trimmed.includes("⚠️") ||
      trimmed.includes("❌")
    ) {
      summary.push(`  ${trimmed}`);
    }
  }

  return summary.join("\n");
}

/** 从 message.content 提取文本 */
function extractText(content: unknown): string {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) {
    return content
      .filter(
        (
          p,
        ): p is {
          type: "text";
          text: string;
        } =>
          p &&
          typeof p === "object" &&
          "type" in p &&
          (p as any).type === "text",
      )
      .map((p) => p.text)
      .join(" ");
  }
  return "";
}

/**
 * 当前进程是否是 subagent 工具 spawn 出来的独立 agent 进程。
 *
 * 背景：subagent 工具为每次委派 spawn 一个独立 agent 子进程（一次性 -p 模式），
 * 它也会加载本扩展。如果不识别并跳过，worker 内部对话里的"完成/搞定/done"等信号
 * 会污染 handoff 输出（review 提示被注入到 worker，主 agent 收到不干净的 handoff）。
 *
 * 旧 pi 的 subagent 用 `--no-session` 标志；omp 的 subagent（task 工具 / Advisor）
 * spawn 标志可能不同。这里做多重检测：
 *   - `--no-session`（旧 pi）
 *   - 环境变量 PI_BLOCKED_AGENT（omp task 会设置子进程标记）
 *   - 同时出现 `--mode json` + `-p`（非交互式一次性命令，可能是 subagent）
 *
 * 无法 100% 确认 omp 的确切标志时，保守起见——首次 session_start 记录 argv 到
 * 加载日志，便于后续根据真实运行情况收紧判定。
 */
let subagentFlagLogged = false;
function isSubagentProcess(): boolean {
  const argv = process.argv;
  if (argv.includes("--no-session")) return true;
  if (process.env.PI_BLOCKED_AGENT) return true;
  // omp subagent 通常以 --mode json -p 一次性运行；但普通 `omp -p "..."` 也是 -p。
  // 为避免误杀用户的一次性命令，仅在同时带 --mode json 时判定（subagent 用 JSON 协议）。
  if (argv.includes("--mode") && argv.includes("json") && argv.includes("-p")) {
    return true;
  }
  return false;
}

// ═══ C5: context 事件记忆简报注入（AGENOTE_INJECTION_DESIGN.md §3 C5）══════════
//
// 每 LLM 调用前把 `agenote context --mode session --host pi` 的输出以固定标签
// 包裹后重写进 messages（重写型：先剥旧块再注新块，幂等防累积）。
//
// 双层开关（改行为优先改 agenote SCHEMA，本开关只是快速物理断开）：
//   - 语义开关：agenote 配置 [injection].enabled / [injection.hosts].pi_enabled
//     ——CLI 自身遵守，关闭时输出零字节，本扩展剥掉旧块后不注新块；
//   - 物理开关：~/.config/omp/agenote-pi.json 的 injectEnabled ——false 时本扩展
//     完全不参与 context 事件（不 spawn CLI，已注入的历史块不再剥离）。
//
// 缓存（D8）：按 KB 侧 MEMORY.org + memories/ 的 mtime+size 指纹缓存 CLI 输出，
// 指纹未变直接重放、零 spawn；CLI 失败按空正文处理并记加载日志（失败同样进
// 缓存，避免每次 LLM 调用都重试炸出一个 spawn 风暴；KB 变更后指纹失效自然重试）。

/** 注入块的固定标签（剥离与注入共用；改动会导致旧块无法剥离，勿轻动） */
export const INJECT_START = "<!-- agenote-inject:start -->";
export const INJECT_END = "<!-- agenote-inject:end -->";
/** 完整块 + 紧随的一个空行分隔（与 wrapInjectionBlock 的拼接格式严格对应；
 *  标签由常量构造保证单一真相源——标签字符不含正则元字符，可直接内插） */
const INJECT_BLOCK_RE = new RegExp(
  `${INJECT_START}[\\s\\S]*?${INJECT_END}(?:\\n\\n)?`,
  "g",
);

/** 用固定标签包裹注入正文（供 stripInjectionBlock 对称剥离） */
export function wrapInjectionBlock(text: string): string {
  return `${INJECT_START}\n${text}\n${INJECT_END}`;
}

/** 剥离文本中的既有注入块（含紧随的空行分隔）；未闭合块自 START 起整段切除 */
export function stripInjectionBlock(text: string): string {
  let out = text.replace(INJECT_BLOCK_RE, "");
  const start = out.indexOf(INJECT_START);
  if (start !== -1) out = out.slice(0, start);
  return out;
}

/**
 * 重写 messages：先全量剥离旧注入块（任何 role、string 或 blocks 形态），
 * 再把非空简报注入第一条 user 消息头部（位置稳定、语义接近系统上下文）。
 * 空简报只剥不注——会话中记忆被清空时旧简报自动撤下。
 * event.messages 是宿主给的深拷贝，原地修改后随返回值交回。
 */
export function applyInjectionToMessages(
  messages: any[],
  briefing: string,
): any[] {
  for (const m of messages) {
    if (!m || typeof m !== "object") continue;
    if (typeof m.content === "string") {
      m.content = stripInjectionBlock(m.content);
    } else if (Array.isArray(m.content)) {
      for (const b of m.content) {
        if (
          b &&
          typeof b === "object" &&
          b.type === "text" &&
          typeof b.text === "string"
        ) {
          b.text = stripInjectionBlock(b.text);
        }
      }
    }
  }
  if (!briefing) return messages;

  const target = messages.find((m) => m && m.role === "user");
  if (!target) return messages; // 无 user 消息可挂载，放弃本轮注入
  const block = wrapInjectionBlock(briefing);
  if (typeof target.content === "string") {
    target.content = `${block}\n\n${target.content}`;
  } else if (Array.isArray(target.content)) {
    const tb = target.content.find(
      (b: any) => b && b.type === "text" && typeof b.text === "string",
    );
    if (tb) {
      tb.text = `${block}\n\n${tb.text}`;
    } else {
      target.content.unshift({ type: "text", text: block });
    }
  }
  return messages;
}

/** KB 侧记忆文件指纹（agenote 域 = $KB_ROOT|~/Documents/Org 下的 agenote/）。
 *
 * 覆盖 MEMORY.org 与 memories/ 目录树（context 命令当前只读 MEMORY.org，
 * memories/ 一并纳入是前向兼容——新增数据源时指纹自动失效）。指纹未变
 * ⇒ KB 未变 ⇒ 简报未变，这是零 spawn 重放的依据（R1：键含 mtime+size 双值）。
 */
export function computeMemoryFingerprint(): string {
  const kbRoot = process.env.KB_ROOT || join(homedir(), "Documents", "Org");
  const domainRoot = join(kbRoot, "agenote");
  const parts: string[] = ["v1"];
  try {
    const st = statSync(join(domainRoot, "MEMORY.org"));
    parts.push(`mem:${st.mtimeMs}:${st.size}`);
  } catch {
    parts.push("mem:missing");
  }
  try {
    const dirents = readdirSync(join(domainRoot, "memories"), {
      withFileTypes: true,
      recursive: true,
    });
    const files: string[] = [];
    for (const d of dirents) {
      if (!d.isFile()) continue;
      try {
        // parentPath 在旧 node typings 里叫 path，运行时取 parentPath 兜底
        const dir: string = (d as any).parentPath ?? "";
        const st = statSync(join(dir, d.name));
        files.push(`${join(dir, d.name)}:${st.mtimeMs}:${st.size}`);
      } catch {
        files.push(`${d.name}:err`);
      }
    }
    files.sort();
    // 上限截断只为封顶开销；记忆树超过 256 个文件时尾部变更不触发刷新（可接受）
    parts.push(`tree:${files.slice(0, 256).join(",")}`);
  } catch {
    parts.push("tree:missing");
  }
  return parts.join("|");
}

// ─── C5 配置（扩展自管——omp ExtensionAPI 无配置读取接口）────────────────────

interface InjectConfig {
  injectEnabled: boolean;
  budget: number;
}
const DEFAULT_INJECT_CONFIG: InjectConfig = { injectEnabled: true, budget: 8000 };

/** 读 <omp 配置根>/agenote-pi.json；缺文件 = 默认开；解析失败容错回退默认 + 日志 */
function loadInjectConfig(): InjectConfig {
  // resolve 而非 join：PI_CONFIG_DIR 是「home 下相对目录名」，但若被设为绝对路径，
  // join(homedir(), "/abs") 会错误拼成 home 下的嵌套路径（global-context 同款坑，
  // 其 getOmpConfigDir 注释有档）。resolve 对两种形态都正确。
  const configRoot = resolve(
    homedir(),
    process.env.PI_CONFIG_DIR || join(".config", "omp"),
  );
  const path = join(configRoot, "agenote-pi.json");
  if (!existsSync(path)) return { ...DEFAULT_INJECT_CONFIG };
  try {
    const raw = JSON.parse(readFileSync(path, "utf8"));
    const cfg = { ...DEFAULT_INJECT_CONFIG };
    if (typeof raw.injectEnabled === "boolean") cfg.injectEnabled = raw.injectEnabled;
    if (
      typeof raw.budget === "number" &&
      Number.isInteger(raw.budget) &&
      raw.budget > 0
    ) {
      cfg.budget = raw.budget;
    }
    return cfg;
  } catch (err) {
    logLoadError("agenote-hooks", "load agenote-pi.json", err);
    return { ...DEFAULT_INJECT_CONFIG };
  }
}

/**
 * 跑 `agenote-cli context --mode session --host pi --budget N` 取简报正文。
 * 返回 null 表示 CLI 缺失/失败/超时（记加载日志，本轮按空正文跳过注入）。
 * 与 runKb 不同：失败绝不能把错误文本当正文注入，故单独成函数、独立 5s 超时。
 */
const BRIEFING_TIMEOUT_MS = 5000;
function fetchBriefing(budget: number): string | null {
  try {
    const stdout = execSync(
      `${KB_SCRIPT} context --mode session --host pi --budget ${budget}`,
      {
        encoding: "utf-8",
        timeout: BRIEFING_TIMEOUT_MS,
        stdio: ["pipe", "pipe", "pipe"],
        env: { ...process.env, AGENOTE_AGENT: "pi" },
      },
    );
    return stdout.trim();
  } catch (err: any) {
    logLoadError("agenote-hooks", "context-brief", err);
    return null;
  }
}

/** 简报缓存：单槽指纹 → 正文。指纹未变直接重放（零 spawn，D8）。 */
let briefingCache: { fingerprint: string; text: string } | undefined;

/** 依据指纹取简报：命中缓存重放，失效才重跑 CLI（fetchFn 参数化供测试注入） */
export function resolveBriefing(
  fingerprint: string,
  fetchFn: (budget: number) => string | null,
  budget: number,
): string {
  if (briefingCache && briefingCache.fingerprint === fingerprint) {
    return briefingCache.text;
  }
  const text = fetchFn(budget) ?? "";
  briefingCache = { fingerprint, text };
  return text;
}

export default function init(pi: any): void {
  try {
    initBody(pi);
  } catch (err) {
    logLoadError("agenote-hooks", "factory", err);
    throw err;
  }
}

function initBody(pi: ExtensionAPI): void {
  // 扩展自管配置只在 init 时读一次（与 global-context 同法）：改 injectEnabled
  // 需重启 omp 会话生效；语义开关（agenote SCHEMA）则是每轮 CLI 生效、无需重启。
  const injectConfig = loadInjectConfig();

  // ── context：每 LLM 调用前注入记忆简报（重写型，幂等）──────────────────────
  // 先剥离旧注入块再注新块——即便宿主把注入过的消息持久化进会话历史，
  // 重复处理也不会累积；CLI 零字节（disabled/empty）时只剥不注，旧简报自动撤下。
  pi.on("context", async (event, _ctx) => {
    // 子代理进程不参与注入（延续 agent_end 同款守卫）
    if (isSubagentProcess()) return;
    // 物理开关：false 时本扩展对 context 事件完全透明
    if (!injectConfig.injectEnabled) return;
    try {
      const messages = (event as any).messages;
      if (!Array.isArray(messages)) return;
      const fingerprint = computeMemoryFingerprint();
      const briefing = resolveBriefing(
        fingerprint,
        fetchBriefing,
        injectConfig.budget,
      );
      applyInjectionToMessages(messages, briefing);
      return { messages };
    } catch (err) {
      // 注入是纯增益路径，任何异常都不允许炸宿主会话
      logLoadError("agenote-hooks", "context-inject", err);
      return;
    }
  });

  // session_start：状态显示已交给 pi-ui 扩展（欢迎框中显示）。
  // 原逻辑在这里 console.log 会导致 stdout 在 TUI 之前打印多行文本，
  // 且与 pi-ui 欢迎框重复。pi-ui 已调用 kb agenote health 解析后
  // 在欢迎框中显示。需要独立查看请使用 /agenote-health 命令。

  // ── session_start：新会话彻底重置触发状态（修 bug：状态跨会话残留）──
  // 模块级状态（idleTimer / idleFallbackFired / turnCount 等）在 /new 切换会话时
  // 不随进程重启而清除——旧会话中断时那次 agent_end 武装的 5 分钟定时器会被
  // "串台"到新会话，导致新会话刚开、agent 正干活时旧定时器到点误触发。
  // 故每次新会话开始：清定时器 + 归零所有触发状态。
  pi.on("session_start", () => {
    // 首次记录一次 argv，便于确认 omp 下 subagent 判定是否准确
    // （PI_BLOCKED_AGENT / --mode json 等信号是否如预期出现）
    if (!subagentFlagLogged) {
      subagentFlagLogged = true;
      try {
        appendFileSync(
          LOG_FILE,
          `[${new Date().toISOString()}] [agenote-hooks] session_start argv=${JSON.stringify(process.argv)} subagent=${isSubagentProcess()} mode=${process.env.PI_BLOCKED_AGENT ? "blocked-agent" : "main"}\n`,
        );
      } catch {
        /* 日志失败不影响主流程 */
      }
    }
    if (isSubagentProcess()) return;
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = undefined;
    idleFallbackFired = false;
    signalTriggered = false;
    turnCount = 0;
    lastTriggerTime = 0;
  });

  // ── agent_start：新一轮 turn 开始 = 会话不再空闲，取消空闲定时器 ──
  // （修 bug：定时器只在 agent_end 武装，长输出 turn 期间从不取消，
  //  上一轮武装的定时器会在"正在流式输出"时到点误触发——截图所示现象。）
  // 语义：idle 计时只能从 agent_end（会话真正安静下来）起算；
  //  一旦 agent_start（新一轮开始），任何待发的 idle 定时器都作废。
  pi.on("agent_start", () => {
    if (isSubagentProcess()) return;
    if (idleTimer) {
      clearTimeout(idleTimer);
      idleTimer = undefined;
    }
  });

  // ── agent_end: 检测完成信号 + 空闲兜底，提示 agent 进入总结流程 ──
  pi.on("agent_end", async (event, ctx) => {
    // subagent 进程的内部对话不应触发本扩展——见 isSubagentProcess 注释。
    // 这是修复"hook 在 worker 进程触发，污染 handoff"问题的关键守卫。
    if (isSubagentProcess()) return;

    turnCount++;

    const messages = (event as any).messages || [];
    // 只看最近一条用户消息：完成信号应来自用户"刚说"的话，
    // 而非全会话历史的拼接（旧实现把历史拼成一坠，任何完成词说一次就永久上膛）。
    let lastUserText = "";
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i]?.role === "user") {
        lastUserText = extractText(messages[i].content);
        break;
      }
    }
    const lastLower = lastUserText.toLowerCase();

    // 排除本扩展自注入的 review 提示（含 HOOK_MARKER）——断开自触发反馈环。
    // 旧实现的 SUMMARIZE_PROMPT 自身含"完成"/"通过"，注入后下轮又匹配到自己，造成重复触发。
    const isSelfInjection = lastUserText.includes(HOOK_MARKER);

    // ── 显式完成信号触发 ──
    if (!isSelfInjection && lastLower) {
      const now = Date.now();
      const hasCompletion = COMPLETION_SIGNALS.some((s) =>
        lastLower.includes(s.toLowerCase()),
      );
      // 防抖：冷却期内不重复触发
      if (hasCompletion && now - lastTriggerTime >= DEBOUNCE_MS) {
        lastTriggerTime = now;
        signalTriggered = true;
        // deliverAs: followUp — 若 agent 仍在 streaming 则安全排队，idle 时立即交付
        pi.sendUserMessage(buildReviewPrompt("检测到任务完成信号"), {
          deliverAs: "followUp",
        });
        ctx.ui.notify(
          "[agenote] 任务完成信号已检测，建议运行 /agenote-summarize 总结经验",
          "info",
        );
      }
    }

    // ── 空闲兜底：会话连续空闲超过阈值且从未被信号触发 → 触发一次 ──
    // 覆盖夜间无人值守工作：用户 kick-off 后离开，agent 自主完成，无人说"完成"。
    // 仅当本会话从未被显式信号触发过时启用（signalTriggered 为 true 则永久禁用，避免与信号路径重复）。
    if (idleTimer) clearTimeout(idleTimer);
    idleTimer = setTimeout(() => {
      if (idleFallbackFired || signalTriggered) return;
      if (turnCount < 1) return; // 至少处理过一轮真实工作
      idleFallbackFired = true;
      lastTriggerTime = Date.now();
      // 定时器回调在 pi 事件流之外执行，session 状态可能已变，故守护异常。
      try {
        pi.sendUserMessage(
          buildReviewPrompt(
            "会话已空闲 5 分钟且未检测到显式完成信号——可能是一段工作（含夜间自动任务）已结束",
          ),
          { deliverAs: "followUp" },
        );
      } catch (err) {
        console.warn("[agenote] 空闲兜底注入失败:", err);
      }
      // 空闲兜底在定时器回调中执行，ctx 可能已失效，故不调 ctx.ui.notify。
    }, IDLE_FALLBACK_MS);
  });

  // ── /agenote-summarize 命令 ──（原 /kb-summarize）
  // 不开新会话——直接注入到当前会话让 agent（有完整上下文）做总结
  pi.registerCommand("agenote-summarize", {
    description: "在当前会话中触发 agenote 经验总结 + 留痕",
    handler: async (_args, ctx) => {
      ctx.ui.notify("[agenote] 触发经验总结，请在下一轮对话中查看结果", "info");
      pi.sendUserMessage(buildReviewPrompt("用户手动触发经验总结"), {
        deliverAs: "followUp",
      });
    },
  });

  // ── /agenote-curate 命令 ──（原 /curate）
  // 策展不直接 execSync CLI——流程由 agent 按 agenote-curator skill 主导
  // （编排原子命令 + 逐项审查），此处只注入任务提示（对齐 /agenote-summarize 模式）。
  pi.registerCommand("agenote-curate", {
    description: "在当前会话触发 KB 策展（agent 按 agenote-curator skill 执行）",
    handler: async (_args, ctx) => {
      ctx.ui.notify("[agenote] 触发策展任务，请在下一轮对话中查看结果", "info");
      pi.sendUserMessage(buildCuratePrompt("用户手动触发策展"), {
        deliverAs: "followUp",
      });
    },
  });

  // ── /agenote-health 命令 ──（原 /kb-health）
  pi.registerCommand("agenote-health", {
    description: "显示 agenote 健康度报告",
    handler: async (_args, _ctx) => {
      const health = runKb("health");
      console.log(health);
    },
  });
}
