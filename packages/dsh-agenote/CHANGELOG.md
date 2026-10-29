# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.4] - 2026-10-29

压缩不再让注入判定失效：召回 query 与「已触发过」都改读 durable log，压缩后
idle 兜底不会二次注入；`agenote_write` 顺带修掉最高频的一类工具失败。

---

### Fixed

- **压缩后注入判定失效**：`lastUserText` 与「已触发过」从 surface 投影改读 durable log（`session.snapshotEvents()`）。compact-checkpoint 会把 surface 上一整段节点 replace 掉，被替换区间里的注入消息与用户消息都不再出现在 `surface.nodes` 上——按 surface 判定会让「已触发过」归零、idle 兜底二次注入，召回 query 也会退回更早的消息。拿不到快照时退化为不注入，不抛错。（f13482a）
- **`agenote_write` 收到重复命令名**：归一化 args，丢掉与 `command` 重复的首个位置参数。模型常把整条 CLI 命令行照抄进 args，CLI 收到 `agenote connect connect ...` 即报 unrecognized arguments——实测 27 次工具失败里 10 次是这一形态。只丢首个且完全相同的参数，命令名不可能是合法卡片 ID 或标题，无误删风险。（f13482a）

## [0.1.3] - 2026-10-20

记忆简报与按最近用户消息的每轮召回进入 `systemPrompt.context` 动态上下文
快照——DSH 侧拿到与 pi / zcode / hermes 对等的记忆注入；顺带修掉纠偏
白名单、防抖识别与 env 名三处小坑。

---

### Added

- 记忆注入（`systemPrompt.context`）：会话简报（`--mode session`）与按最近用户消息的召回（`--mode recall`）写进同一动态上下文快照，重写型无累积风险；简报按「会话 cwd + KB 指纹」、召回按「指纹 + query」缓存（cwd 决定简报的项目段，多项目会话不得共用单槽），未变时零 spawn。新增 `hooks.brief` / `hooks.recall` 开关。补齐与 pi / zcode / hermes 的功能对等。（0f2a881 / 0f6d7e3）
- 功能基线文档 `docs/PLUGIN_CAPABILITIES.md`：11 项插件功能、7 个宿主对照与豁免记录；`spec/injection.toml` 增补 `[hosts.dsh]` 预算。（1b7382c / 0f2a881）

### Fixed

- **`MIN_QUERY` 幻影 env 名**：改读 `AGENOTE_INJECTION_RECALL_MIN_QUERY`，与 CLI SCHEMA 同源——原名 CLI 从不读取，单侧设阈值会让宿主预筛与 CLI 裁决不一致。（2ac10aa）
- **`agenote_write` 白名单缺 `reindex`**：策展流程第 5 步要跑它，索引写在沙箱外路径，bash 直下会被围栏拦。（2ac10aa）
- **手动 `/agenote-summarize` / `/agenote-curate` 不计入「已触发」**：`deliveredByUs` 改认三个投递名，手动策展后 idle 兜底不再补一条。（2ac10aa）

## [0.1.2] - 2026-10-20

`agenote_write` 把知识库写入从会话文件沙箱里解放出来——`workspace-write` / `read-only` 会话都能留痕，`never` 审批下也不再静默丢失；同时修掉插件在 dsh 0.2.x 被整行跳过的问题。

---

### Added

- 宿主侧 `agenote_write` 工具（`tools.js`）：白名单透传 agenote 写命令，`stdin` 承载正文，写入不受会话文件沙箱约束。
- `lib.js` 新增 `runKbAsync`：`spawn` + Promise，工具执行不阻塞宿主事件循环。
- `tools/post-execute` 纠偏：bash 跑 agenote 写命令被沙箱拒（`只读文件系统` / `[sandbox: file access denied`）时，注入提示指向 `agenote_write`；只读命令与成功写入不触发。skill 是跨宿主共用真相源，引导只落在插件侧。

### Fixed

- 兼容 cohort 抬到 dsh 0.2.1-rc.2，`peerDependencies` 与 `dsh.engines.dsh` 同步抬升；此前停在 `0.1.7-rc.2`，运行时 0.2.x 下整行 bundle 被跳过，健康度摘要与 review 提示全部静默失效。

## [0.1.1] - 2026-10-03

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
