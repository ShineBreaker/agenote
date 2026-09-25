// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — bundle patch 挂载的 host 入口。
//
// 此入口必须由裸包名行挂载（见 cordis.patch.yml 文件头）：loader row 的
// specifier 需为精确包名，dsh.client 声明才会被客户端扫描到。
//
// config 形如 { hooks: {...}, commands: {...} }，见 README。

import * as commands from "./commands.js";
import * as hooks from "./hooks.js";

export const name = "dsh-agenote";

// hooks 半边需要 systemPrompt 服务（健康度摘要走 system-prompt section）；
// commands 半边需要 commands 服务。
export const inject = ["systemPrompt", "commands"];

export function apply(ctx, config = {}) {
  hooks.apply(ctx, config.hooks ?? {});
  commands.apply(ctx, config.commands ?? {});
}
