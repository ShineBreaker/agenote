// SPDX-FileCopyrightText: 2026 BrokenShine <xchai404@gmail.com>
// SPDX-License-Identifier: MIT
//
// dsh-agenote — bundle patch 挂载的 host 入口。
//
// 此入口必须由裸包名行挂载（见 cordis.patch.yml 文件头）：loader row 的
// specifier 需为精确包名，dsh.client 声明才会被客户端扫描到。
//
// config 形如 { hooks: {...}, commands: {...} }，经导出的 Config schema 校验
// 并填默认值后交给两半边；字段与默认值见 hooks.js / commands.js。

import * as commands from "./commands.js";
import * as hooks from "./hooks.js";
import { schema } from "./lib.js";

export const name = "dsh-agenote";

// hooks 半边需要 systemPrompt 服务（健康度摘要走 system-prompt section）；
// commands 半边需要 commands 服务。
export const inject = ["systemPrompt", "commands"];

/**
 * 插件配置 schema（Standard Schema v1，实现见 lib.js）。
 *
 * Cordis 加载时调 Config["~standard"].validate(config)：校验 cordis.yml 行
 * 下发的 config 并填默认值，非法配置在加载期抛 ValidationError（fail
 * loudly）。两个半边各自声明字段与默认值，这里只做组合。
 */
export const Config = schema.object({
  hooks: hooks.Config,
  commands: commands.Config,
}, { default: {} });

export function apply(ctx, config) {
  hooks.apply(ctx, config.hooks);
  commands.apply(ctx, config.commands);
}
