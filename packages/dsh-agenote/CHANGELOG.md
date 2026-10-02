# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **`private: true` 已移除**，本包可正式发布到 npm。此前该字段使 `npm publish`
  被拒，等于从未真正对外分发过；tag 触发方式见 monorepo 的
  `docs/adr/0005-monorepo-unify-seven-components.md`（`dsh-agenote-v*` → npm +
  GitHub Release）。
- **迁入 monorepo**：目录 `packages/dsh-agenote/`，原独立仓保留完整历史。
- **`hooks.js` 的 `COMPLETION_SIGNALS` / `DEBOUNCE_MS` / `IDLE_FALLBACK_MS` 改为
  生成块**：真相源是仓库根 `spec/injection.toml`，由 `tools/codegen/generate.py`
  产出并提交进仓。迁移前信号在本插件、pi、zcode、hermes 四处逐字复制。
  数值未变（`5*60*1000` 统一写成 `300000`）。

## [0.1.0] - 2026-09-27

首个发布版本。DeepSeek Harness 的 agenote 集成插件：把 agenote 知识库接进 DSH 会话。行为规范由 `agenote-skills` 定义，本插件只做事件触发与命令快捷入口，不重复实现知识库逻辑。

### Added

- 会话进入时经 `systemPrompt.section` 注入 `agenote health` 摘要（背景知识，不产生 turn），带 TTL 缓存（`hooks.statusTtlMs`，0 = 每个请求重算）。
- turn 收尾（`agent/turn-stopping`）检测任务完成信号，转交 agenote-review 流程；防抖与「已触发」从 durable session log 推导，跨 resume/重启稳定。
- 空闲兜底：跑过工具的轮次结束后静置超时（`hooks.idleMs`）且本会话从未触发过时提示一次，覆盖无人值守场景。
- subagent 事件一律豁免（`session.header.origin === 'subagent'`），其内部对话不进信号检测、不武装 idle 计时器。
- `/agenote-summarize`、`/agenote-curate`、`/agenote-health` 三个斜杠命令；前两个投递任务提示由 agent 按 skill 主导，第三个纯只读回显。
- CLI 调用统一收敛在 `lib.js` 的 `runKb`：数组传参（无 shell 注入面）、`AGENOTE_AGENT=dsh` 归因、统一超时与错误降级。
- 配置 schema（Standard Schema v1，`index.js` 导出）：`hooks.{enabled,status,completionSignals,idleFallback,signals,debounceMs,idleMs,statusTtlMs}` 与 `commands.enabled`。Cordis 加载期校验 cordis.yml 行下发的 config 并填默认值，非法配置（如 `debounceMs: -1`、`signals: []`）在加载期报错而非静默回退。
- `package.json` 声明 `@deepseek-ai/dsh` 兼容 cohort（`^0.1.7-rc.2`，peerDependencies + optional 标记）与 `dsh.engines.dsh` 展示位（`>=0.1.7-rc.2`），并补 `repository` 字段。
