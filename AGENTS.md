# AGENTS.md

> dsh-agenote 仓库协作约定

## 概述

DeepSeek Harness（DSH）的 agenote 集成插件：Cordis bundle，纯 ESM 无构建步骤。
职责仅限「事件触发 + 命令快捷入口」，行为规范由 `agenote-skills` 定义，知识库
操作由 `agenote` CLI 提供——**本仓库不重复实现这两者**。

## 协作约定

- 修改前先读本文件与 `README.md`（尤其「设计要点」一节，那里记录了刻意的取舍）。
- 保持改动最小化，沿用现有风格：中文注释、制表符缩进、`node:` 前缀内置模块。
- **不要 import `@deepseek-ai/*`**：本包经 `link:` 部署，realpath 落在源码目录，
  解析不到 `$DSH_HOME/profiles/node_modules`。需要上游纯函数时在 `lib.js` 复刻并注明出处。
- 所有 CLI 调用必须走 `lib.js` 的 `runKb`（自动带 `AGENOTE_AGENT=dsh` 归因、
  数组传参无注入面、统一超时与错误降级）。
- 投递消息用 `agent.followup()`，不要用 `steer()`/`inject()`——见 README「设计要点」。
- 会话状态一律放 per-session `Map`，禁止模块级可变标量（多会话会串台）。
- 信号清单改动需同步 `agenote-review/references/triggers.md`（单一真相源）。

## 验证

无自动化测试。改动的验证路径：

```bash
node --check index.js hooks.js commands.js lib.js   # 1. 语法
```

```bash
# 2. 逻辑：对真实 CLI 跑一遍（需 agenote 在 PATH）
node --input-type=module -e "import('./lib.js').then(m=>console.log(m.healthSummary(m.runKb)))"
```

```bash
# 3. 激活：新进程冷启动，确认无 "failed to import"
dsh --profile <profile> --no-open --port 3099
```

运行中进程的 `plugin_manager set_bundle` 可能报 `failed to import`——那是该次调用自身的
诊断，**不代表 bundle 未被采用**（profile 为 `patchReload: live` 时新行会热挂载）。判断依据是
「冷启动日志无失败」+「会话里是否真的看到注入」，不要据此改代码。反之，**改了源码**才必须
冷启动：运行中的进程持有旧模块图。

## 技能索引

- `.agents/skills/` — 项目技能与工作流
