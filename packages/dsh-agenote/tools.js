// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — tools 半边：宿主侧知识库写入工具。
//
// 为什么需要它：DSH 的文件沙箱只放行会话工作区与平台临时目录（
// @deepseek-ai/dsh-sandbox 的 writableRoots = workspaceRoot + /tmp + os.tmpdir()），
// 知识库却在 ~/Documents/Org/agenote。会话处于 workspace-write / read-only 时，
// agent 在 bash 里跑 `agenote add` 会被围栏拒绝（表现是「只读文件系统」）。
// 本工具在宿主进程里 spawn CLI，不经过 ctx.shell 的文件围栏，因此写入不受会话
// 文件策略影响——这是它存在的唯一理由，不要拿它替代沙箱内的普通命令。
//
// 边界：只透传白名单内的写命令，argv 数组传参（不经 shell）；卡片格式与写入
// 规范仍归 agenote-skills 与 agenote CLI，本文件不复制任何知识库逻辑。

import { createUserMessage, runKbAsync, schema } from "./lib.js";

export const name = "agenote-tools";

export const inject = ["tools"];

/** tools 半边的配置 schema（默认值单一真相源，由 index.js 组合成插件 Config）。 */
export const Config = schema.object({
  enabled: schema.boolean(true),
}, { default: {} });

/**
 * 允许经本工具执行的写命令。
 *
 * 只读命令（search/list/get/health/stats/fields/tags/context/doctor/gaps/trace/
 * review/help/completions）刻意不在表内：它们本来就能在沙箱里直接跑，放进来
 * 只会让工具面更模糊。`viz` 也不在表内——它会调 xdg-open 在宿主桌面上弹窗，
 * 不属于「写知识库」。
 */
const WRITE_COMMANDS = [
  // 卡片写入与维护
  "add",
  "update",
  "touch",
  "merge",
  "connect",
  "archive",
  "restore",
  "deduplicate",
  "sweep",
  // 捕获与批处理
  "inbox",
  "inbox-archive",
  "distill",
  "dream",
  "extract",
  "reconcile",
  "scan-memories",
  // 记忆与库级操作
  "memory",
  "commit",
  "init",
  "format",
  "lint",
  "config",
];

const WRITE_COMMAND_SET = new Set(WRITE_COMMANDS);

// ─── 被拦后的纠正提示 ───────────────────────────────────────────────────────
//
// skill 里的写入指引写的是 CLI 命令（`agenote add ...`）。模型照 skill 先跑 bash、
// 撞上文件围栏是常见路径——工具描述只能影响"要不要用它"，拦不住"先试 CLI"。
// 这里在 bash 结果带沙箱拒绝形态时补一条提示，把刚才那条写命令指回工具。

/** 知识库默认位置，只用于提示文本（agenote 的 kb_root 可配置，故不据此判定路径）。 */
const AGENOTE_KB_HINT = "~/Documents/Org/agenote";

/** 命令里是否出现 agenote 写命令；长名优先，避免 inbox 抢走 inbox-archive。 */
const WRITE_INVOCATION = new RegExp(
  String.raw`\bagenote\s+(?:--domain\s+\w+\s+)?(${[...WRITE_COMMANDS].sort((a, b) => b.length - a.length).join("|")})\b`,
);

/** 沙箱拒绝的两种可观察形态：bash 子进程内部 open 失败，或 harness 的显式标记。 */
const SANDBOX_DENIAL = /只读文件系统|Read-only file system|EROFS|\[sandbox: file access denied/i;

const INJECTION_SOURCE = { kind: "plugin:agenote-tools" };

/** 注入包裹（跨插件约定）：机器注入与用户真实输入靠 source 属性区分。 */
function wrapInjection(text, source) {
  // 命令原文由模型生成，闭合标签必须剔除，否则包裹会被截断。
  return `<injected source="${source}">\n${text.replace(/<\/injected/gi, "")}\n</injected>`;
}

function denialHintText(command) {
  return [
    `[agenote] 这条 \`agenote ${command}\` 被会话文件沙箱拦了：知识库在 ${AGENOTE_KB_HINT}，不在会话工作区内。`,
    `改用 agenote_write 工具重试——command="${command}"，args 与 CLI 参数相同，正文放进 stdin。`,
    "只读命令（search/list/get/health/stats）仍可直接用 bash。",
  ].join("\n");
}

function contentText(result) {
  return (result?.content ?? [])
    .filter((block) => block?.type === "text")
    .map((block) => String(block.text ?? ""))
    .join("\n");
}

/**
 * 监听 bash 结果：命令行是 agenote 写命令、且结果带沙箱拒绝形态时追加一条纠正
 * 提示。只在失败时触发，正常执行的 bash 不受影响；任何异常都回落为原结果。
 */
function installDenialHint(ctx) {
  ctx.on("tools/post-execute", async (exec, result, next) => {
    const downstream = await next();
    try {
      if (exec?.name !== "bash") return downstream;
      if (downstream.kind !== "accept" && downstream.kind !== "block") return downstream;
      const command = exec.arguments?.command;
      if (typeof command !== "string" || command.length === 0) return downstream;
      const hit = WRITE_INVOCATION.exec(command);
      if (hit === null) return downstream;
      if (!SANDBOX_DENIAL.test(contentText(result))) return downstream;
      const context = createUserMessage({
        content: [{ type: "text", text: wrapInjection(denialHintText(hit[1]), "agenote") }],
        source: INJECTION_SOURCE,
      });
      return {
        ...downstream,
        additionalContexts: [...(downstream.additionalContexts ?? []), context],
      };
    } catch {
      // 提示是尽力而为的旁路：任何异常都不能反噬工具结果本身。
      return downstream;
    }
  });
}

/** 工具结果截断上限：工具输出进上下文，留够诊断用即可。 */
const OUTPUT_MAX_CHARS = 20_000;
/** CLI 调用的协商超时；工具自身 timeoutMs 比它宽 10s，留出收尾余量。 */
const CLI_TIMEOUT_MS = 110_000;
const TOOL_TIMEOUT_MS = 120_000;

function clip(text) {
  if (text.length <= OUTPUT_MAX_CHARS) return text;
  return `${text.slice(0, OUTPUT_MAX_CHARS)}\n…（输出已截断，共 ${text.length} 字符）`;
}

const definition = {
  name: "agenote_write",
  description:
    "写入 agenote 知识库（宿主侧执行，不受会话文件沙箱限制）。" +
    "知识库位于 ~/Documents/Org/agenote，不在会话工作区内：workspace-write / read-only 会话里用 bash 跑 `agenote add` 会被文件沙箱拒绝（只读文件系统），写入一律走本工具。" +
    "只读查询（search/list/get/health/stats）仍可直接用 bash。\n" +
    "参数：command 是白名单内的写命令；args 逐项透传为 `agenote <command> <args...>` 的后续参数；stdin 作为子进程标准输入（`--stdin` 的正文）。\n" +
    '示例（写一张经验卡）：command="add"，args=["--title","标题","--category","guix","--tech","guix","--stdin"]，stdin="正文"。',
  parameters: {
    type: "object",
    properties: {
      command: {
        type: "string",
        enum: WRITE_COMMANDS,
        description: "要执行的 agenote 写命令",
      },
      args: {
        type: "array",
        items: { type: "string" },
        description: "命令参数（不含 command 本身），逐项透传，不经 shell",
      },
      stdin: {
        type: "string",
        description: "写入子进程标准输入的正文，供 `add --stdin`、`memory --add --stdin` 等使用",
      },
    },
    required: ["command"],
  },
  output: {
    schema: {
      type: "object",
      properties: {
        ok: { type: "boolean" },
        output: { type: "string" },
      },
      required: ["ok", "output"],
    },
    render(_args, value) {
      return [{ type: "text", text: value.output }];
    },
  },
  timeoutMs: TOOL_TIMEOUT_MS,
  isConcurrencySafe() {
    // 写入共享 index.json 与卡片目录：并发会撞 agenote 的 safeio 锁，串行更稳。
    return false;
  },
  async execute(args, exec) {
    const command = args?.command;
    if (typeof command !== "string" || !WRITE_COMMAND_SET.has(command)) {
      throw new Error(
        `agenote_write: 命令 ${JSON.stringify(command)} 不在写入白名单内（可用：${WRITE_COMMANDS.join(", ")}）`,
      );
    }
    const extra = Array.isArray(args.args) ? args.args.map(String) : [];
    const stdin = typeof args.stdin === "string" ? args.stdin : undefined;
    const argv = [command, ...extra];
    const res = await runKbAsync(argv, {
      stdin,
      timeoutMs: CLI_TIMEOUT_MS,
      signal: exec?.signal,
    });
    if (!res.ok) {
      throw new Error(`agenote ${argv.join(" ")} 失败：${clip(res.stderr.trim()) || "未知错误"}`);
    }
    const out = res.stdout.trim();
    return { ok: true, output: out.length > 0 ? clip(out) : "(agenote 无输出)" };
  },
};

export function apply(ctx, cfg) {
  if (!cfg.enabled) return;
  ctx.tools.register(definition);
  installDenialHint(ctx);
}
