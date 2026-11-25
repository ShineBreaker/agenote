// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
//
// SPDX-License-Identifier: MIT

/**
 * agenote-cindy 的离线自检：不装进宿主，用桩替身跑一遍 main.js 的分支。
 *
 * 覆盖：入口注入（首条 / 无内容 / 自注入放行）、完成信号触发、防抖、空闲兜底
 * 的武装与取消、工具调用的参数与失败降级。
 *
 * 用法：node packages/agenote-cindy/selftest.mjs
 */

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import vm from "node:vm";

const HERE = dirname(fileURLToPath(import.meta.url));
const SOURCE = readFileSync(join(HERE, "main.js"), "utf8");

let failures = 0;
function check(name, condition, detail) {
  if (condition) {
    console.log(`  ok   ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL ${name}${detail ? ` — ${detail}` : ""}`);
  }
}

/** 装上 main.js：返回宿主桩的句柄。 */
function boot({ context = null, hostCall } = {}) {
  const sent = [];
  const nodeCalls = [];
  const timers = [];
  const handlers = [];
  const hostCalls = [];

  const cindy = {
    onHostMessage(fn) {
      handlers.push(fn);
    },
    async send(message) {
      sent.push(message);
      return { ok: true };
    },
    async request() {
      return { ok: true, context: {} };
    },
  };
  cindy.node = {
    async request(request) {
      nodeCalls.push(request);
      if (context === null) return { ok: false, message: "stub: no worker" };
      const value = typeof context === "function" ? context(request) : context;
      if (value && value.ok === false) return value;
      return { ok: true, result: value };
    },
  };
  cindy.host = {};
  cindy.host.call = async (server, tool, args) => {
    hostCalls.push({ server, tool, args });
    if (hostCall) return hostCall(server, tool, args);
    return { content: [{ type: "text", text: JSON.stringify({ ok: true }) }] };
  };

  const timersStub = {
    setTimeout(fn, ms) {
      const entry = { fn, ms, cleared: false };
      timers.push(entry);
      return entry;
    },
    clearTimeout(entry) {
      if (entry) entry.cleared = true;
    },
  };

  const sandbox = {
    cindy,
    setTimeout: timersStub.setTimeout,
    clearTimeout: timersStub.clearTimeout,
    console,
    Date,
    Math,
    Number,
    String,
    Object,
    Array,
    JSON,
    Promise,
    Error,
  };
  vm.runInNewContext(SOURCE, sandbox, { filename: "main.js" });

  return {
    sent,
    nodeCalls,
    timers,
    hostCalls,
    emit: (message) => {
      const handler = handlers[0];
      if (!handler) throw new Error("main.js 没有注册 onHostMessage");
      return handler(message);
    },
  };
}

const sleep = () => new Promise((resolve) => setImmediate(resolve));

async function main() {
  console.log("agenote-cindy selftest");

  // ── 入口注入 ────────────────────────────────────────────────────────
  {
    console.log("\nwill-compose-context");
    const h = boot({ context: { text: "BRIEF-BODY", parts: 1 } });
    await h.emit({
      type: "event",
      name: "will-compose-context",
      hookId: "hook-1",
      data: { sessionId: "s1", text: "帮我看下 kiro 的包管理" },
    });
    const verdict = h.sent.at(-1);
    check("首轮返回资料裁决", verdict.action === "context");
    check("资料块带简报正文", verdict.blocks?.[0]?.text === "BRIEF-BODY");
    check("资料块 id 固定", verdict.blocks?.[0]?.id === "agenote-brief");
    const firstContext = h.nodeCalls.find((call) => call.method === "context");
    check("worker 收到 first=true", firstContext?.params.first === true);

    // 同一会话第二条消息：简报不再重复（由 first=false 与 CLI 去重共同保证）
    h.nodeCalls.length = 0;
    await h.emit({
      type: "event",
      name: "will-compose-context",
      hookId: "hook-2",
      data: { sessionId: "s1", text: "继续" },
    });
    check(
      "第二条消息 first=false",
      h.nodeCalls.filter((call) => call.method === "context").at(-1)?.params.first === false,
    );
  }

  {
    const h = boot({ context: { text: "", parts: 0 } });
    await h.emit({
      type: "event",
      name: "will-compose-context",
      hookId: "hook-3",
      data: { sessionId: "s2", text: "空库时的普通消息" },
    });
    check("无内容时放行", h.sent.at(-1).action === "allow");
  }

  {
    const h = boot({ context: { text: "SHOULD-NOT-RUN", parts: 1 } });
    await h.emit({
      type: "event",
      name: "will-compose-context",
      hookId: "hook-4",
      data: { sessionId: "s3", text: "<agenote-hook>自己投递的提示</agenote-hook>" },
    });
    check("自注入消息直接放行", h.sent.at(-1).action === "allow");
    check(
      "自注入不调 worker",
      !h.nodeCalls.some((call) => call.method === "context"),
    );
  }

  {
    const h = boot({ context: null });
    await h.emit({
      type: "event",
      name: "will-compose-context",
      hookId: "hook-5",
      data: { sessionId: "s4", text: "worker 挂了的消息" },
    });
    check("worker 不可用时放行", h.sent.at(-1).action === "allow");
  }

  // ── 完成信号 ────────────────────────────────────────────────────────
  {
    console.log("\n完成信号");
    const h = boot({ context: { text: "", parts: 0 } });
    await h.emit({
      type: "event",
      name: "did-message-created",
      data: {
        sessionId: "s5",
        messageId: "m1",
        role: "user",
        content: "都搞定了，测试通过",
      },
    });
    await h.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s5", durationMs: 3000, endReason: "completed" },
    });
    await sleep();
    check("命中信号后投递提示", h.hostCalls.length === 1);
    check(
      "投递走 send_to_session",
      h.hostCalls[0]?.tool === "send_to_session" &&
        h.hostCalls[0]?.args?.target_session_id === "s5",
    );
    check("提示带自注入标记", String(h.hostCalls[0]?.args?.message).includes("<agenote-hook>"));
    check("短轮次不武装空闲定时器", h.timers.length === 0);

    // 第二轮再说一次「完成」：防抖内不再投递
    await h.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "s5", messageId: "m2", role: "user", content: "完成了" },
    });
    await h.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s5", durationMs: 3000, endReason: "completed" },
    });
    await sleep();
    check("防抖期内不重复投递", h.hostCalls.length === 1);
  }

  // ── 完成信号边界 ────────────────────────────────────────────────────
  {
    console.log("\n完成信号边界");
    for (const text of ["还没完成", "完成了吗？", "如果完成了再告诉我", "not done.", "if done!"]) {
      const h = boot({ context: { text: "", parts: 0 } });
      await h.emit({
        type: "event",
        name: "did-message-created",
        data: { sessionId: "negative-" + text, messageId: "m", role: "user", content: text },
      });
      await h.emit({
        type: "event",
        name: "did-turn-end",
        data: { sessionId: "negative-" + text, durationMs: 3000, endReason: "completed" },
      });
      check(`不误触发：${text}`, h.hostCalls.length === 0);
    }
  }

  // ── 空闲兜底 ────────────────────────────────────────────────────────
  {
    console.log("\n空闲兜底");

    // 投递失败不能永久封存兜底；下一次到点应继续尝试。
    let attempts = 0;
    const retry = boot({
      context: { text: "", parts: 0 },
      hostCall: async () => {
        attempts += 1;
        return attempts === 1 ? { ok: false, errorCode: "TEMPORARY" } : { ok: true };
      },
    });
    await retry.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "retry", messageId: "m", role: "user", content: "跑一轮长任务" },
    });
    await retry.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "retry", durationMs: 60000, endReason: "completed" },
    });
    await retry.timers[0].fn();
    check("投递失败后保留重试定时器", retry.timers.length === 2 && retry.hostCalls.length === 1);
    await retry.timers[1].fn();
    check("重试成功后只投递两次", retry.hostCalls.length === 2);

    // 同一个定时器被重复调度时，并发请求应共享失败回执，不立即重复投递。
    let resolveConcurrent;
    let concurrentCalls = 0;
    const concurrent = boot({
      context: { text: "", parts: 0 },
      hostCall: async () => {
        concurrentCalls += 1;
        return new Promise((resolve) => {
          resolveConcurrent = resolve;
        });
      },
    });
    await concurrent.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "concurrent", messageId: "m", role: "user", content: "跑一轮长任务" },
    });
    await concurrent.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "concurrent", durationMs: 60000, endReason: "completed" },
    });
    const concurrentTimer = concurrent.timers[0];
    const firstAttempt = concurrentTimer.fn();
    const secondAttempt = concurrentTimer.fn();
    await sleep();
    check("并发失败投递只调用一次宿主", concurrentCalls === 1);
    resolveConcurrent({ ok: false, errorCode: "TEMPORARY" });
    await Promise.all([firstAttempt, secondAttempt]);
    check("并发失败后只保留一条重试链", concurrent.timers.length === 2);

    // 显式信号投递等待期间开始新轮次时，旧轮次不得重新武装定时器。
    let resolveNudge;
    const race = boot({
      context: { text: "", parts: 0 },
      hostCall: () => new Promise((resolve) => {
        resolveNudge = resolve;
      }),
    });
    await race.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "race", messageId: "m1", role: "user", content: "完成了" },
    });
    const pendingEnd = race.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "race", durationMs: 3000, endReason: "completed" },
    });
    await sleep();
    await race.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "race", messageId: "m2", role: "user", content: "继续修改" },
    });
    resolveNudge({ ok: true });
    await pendingEnd;
    check("新轮次不会被旧收尾重新武装", race.timers.length === 0);
    await race.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "race", durationMs: 60000, endReason: "completed" },
    });
    check("旧回执不会封存新轮次的空闲兜底", race.timers.length === 1);

    // 短问答但本轮真的跑过工具：工具调用条数是比时长更硬的「有工作」证据。
    const h = boot({ context: { text: "", parts: 0 } });
    await h.emit({ type: "event", name: "did-turn-start", data: { sessionId: "s8" } });
    await h.emit({
      type: "event",
      name: "did-tool-execution",
      data: { sessionId: "s8", eventType: "tool_use", payload: {} },
    });
    await h.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "s8", messageId: "m5", role: "user", content: "再跑一次测试" },
    });
    await h.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s8", durationMs: 8000, endReason: "completed" },
    });
    check("有工具调用的短轮次也武装定时器", h.timers.length === 1);

    // 纯对话短轮次：不该被提醒去复盘。
    const h9 = boot({ context: { text: "", parts: 0 } });
    await h9.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "s9", messageId: "m6", role: "user", content: "谢谢，聊聊天" },
    });
    await h9.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s9", durationMs: 5000, endReason: "completed" },
    });
    check("纯对话短轮次不武装定时器", h9.timers.length === 0);

    const h6 = boot({ context: { text: "", parts: 0 } });
    await h6.emit({
      type: "event",
      name: "did-message-created",
      data: {
        sessionId: "s6",
        messageId: "m3",
        role: "user",
        content: "把这个仓库的 CI 修好",
      },
    });
    await h6.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s6", durationMs: 60000, endReason: "completed" },
    });
    check("长轮次后武装定时器", h6.timers.length === 1);
    check("定时器用的是 spec 的空闲阈值", h6.timers[0].ms > 0);

    await h6.timers[0].fn();
    await sleep();
    check("到点后投递一次", h6.hostCalls.length === 1);

    // 新的轮次开始要取消定时器。
    const h7 = boot({ context: { text: "", parts: 0 } });
    await h7.emit({
      type: "event",
      name: "did-message-created",
      data: { sessionId: "s7", messageId: "m4", role: "user", content: "跑一轮长任务" },
    });
    await h7.emit({
      type: "event",
      name: "did-turn-end",
      data: { sessionId: "s7", durationMs: 90000, endReason: "completed" },
    });
    await h7.emit({ type: "event", name: "did-turn-start", data: { sessionId: "s7" } });
    check("did-turn-start 取消定时器", h7.timers[0].cleared === true);
  }

  // ── 工具 ────────────────────────────────────────────────────────────
  {
    console.log("\n工具");
    const h = boot({
      context: (request) =>
        request.method === "run" ? { ok: true, output: "hit-1\nhit-2" } : { text: "" },
    });
    await h.emit({
      type: "tool-call",
      tool: "agenote_search",
      callId: "c1",
      args: { query: "wayland 输入法", limit: 3 },
    });
    const result = h.sent.at(-1);
    check("search 交卷成功", result.ok === true && result.result.output.includes("hit-1"));
    check(
      "search 参数被规整",
      JSON.stringify(h.nodeCalls.at(-1).params.argv) ===
        JSON.stringify(["search", "wayland 输入法", "--limit", "3"]),
    );

    await h.emit({ type: "tool-call", tool: "agenote_search", callId: "c2", args: {} });
    check("空 query 直接报错", h.sent.at(-1).ok === false);

    const h2 = boot({
      context: (request) =>
        request.method === "run" ? { ok: false, message: "CLI 不在 PATH 里" } : { text: "" },
    });
    await h2.emit({ type: "tool-call", tool: "agenote_health", callId: "c3", args: {} });
    check("CLI 失败时返回可读错误", h2.sent.at(-1).ok === false);

    const diag = boot({
      context: (request) =>
        request.method === "run" ? { ok: true, output: "health-ok" } : { text: "" },
    });
    await diag.emit({
      type: "tool-call",
      tool: "agenote_diag",
      callId: "c4",
      args: { send_test: true },
    });
    const noTarget = JSON.parse(diag.sent.at(-1).result.output);
    check("send_test 不默认跨会话投递", noTarget.sendTest.ok === false && diag.hostCalls.length === 0);

    await diag.emit({
      type: "tool-call",
      tool: "agenote_diag",
      callId: "c5",
      args: { send_test: true, target_session_id: "other-session" },
    });
    const explicitTarget = JSON.parse(diag.sent.at(-1).result.output);
    check(
      "send_test 使用显式目标会话",
      explicitTarget.sendTest.ok === true &&
        diag.hostCalls.at(-1)?.args?.target_session_id === "other-session",
    );
  }

  console.log(failures === 0 ? "\n全部通过" : `\n${failures} 项失败`);
  process.exit(failures === 0 ? 0 : 1);
}

await main();
