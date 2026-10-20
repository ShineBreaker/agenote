# Agent 插件功能基线

本文定义「agenote 接进一个 agent 宿主」时，插件层**应当实现哪些功能**，以及每个宿主
当前的实现状态。新增或修改宿主插件前先对照本表；改完把矩阵一起更新，别让文档成为
第二处过期真相源。

分层依据见 [ADR 0005](adr/0005-monorepo-unify-seven-components.md)：行为规范归
`agenote-skills`，知识库逻辑归 `agenote` CLI，插件只做**事件触发 + 注入通道 + 命令
快捷入口**。本文列的每一项都必须落在这条边界内——插件不复制卡片格式、写入流程或
检索算法。

## 两类组件，两把尺子

| 类型 | 成员 | 职责 |
|---|---|---|
| **完整插件** | `agenote-zcode` / `agenote-hermes` / `agenote-pi` / `dsh-agenote` | 注入 + 触发 + 命令入口 |
| **注入器** | `injectors/claude` / `injectors/codex` / `injectors/opencode` | 只承担记忆注入，不承担完成信号与命令 |

拿完整插件的基线去要求注入器，会逼着注入器复制行为规范；反过来拿注入器的基线去
要求完整插件，则会漏掉触发与命令。**先按类型选尺子，再看缺口。**

## 功能基线（11 项）

「必选」指该类型组件在宿主能力允许时必须实现。宿主能力确实缺失的，记入
[豁免记录](#豁免记录)，并在矩阵里标 `N/A`；不能默默不写。

| # | 功能 | 完整插件 | 注入器 | 判定依据 |
|---|---|---|---|---|
| F1 | 记忆简报注入（会话级） | 必选 | 必选 | 会话可见 `agenote-context v1 mode=session` 标记的内容；KB 未变时缓存重放、不重复生成 |
| F2 | 每轮召回注入（按用户消息） | 必选（重写型可只做简报，见豁免） | 追加型必选；重写型可选 | 用户消息变化后出现 `mode=recall` 内容；同 query 同指纹不重注 |
| F3 | 注入状态与缓存 | 必选 | 必选 | 追加型调 `agenote inject`（状态机唯一实现在 CLI：指纹、query 去重、累计预算、会话重置）；重写型可有进程内指纹缓存，只省 spawn |
| F4 | 语义开关委托 CLI | 必选 | 必选 | `AGENOTE_INJECTION_ENABLED=false` / `[injection.hosts].<host>_enabled=false` 时零字节、不注入 |
| F5 | 完成信号检测 | 必选 | 不适用 | 命中 `spec/injection.toml` 的显式信号后注入 review 提示；带防抖与自注入防护 |
| F6 | 空闲兜底 | 条件必选（宿主有静默结束信号） | 不适用 | 静置 `idle_ms` 且本会话从未触发过时提示一次；不得在纯对话轮误报 |
| F7 | 命令三件套 | 必选 | 不适用 | `/agenote-summarize`、`/agenote-curate`、`/agenote-health` 可达 |
| F8 | 归因 | 必选 | 必选 | 所有 CLI 调用带 `AGENOTE_AGENT=<host>`；不得落到默认 `omp` |
| F9 | 子代理/非交互会话豁免 | 必选 | 不适用 | subagent（宿主有 cron 概念时含 cron）会话不注入、不武装计时器、不触发 review |
| F10 | 故障静默 | 必选 | 必选 | CLI 缺失、超时、非零退出、非法 JSON 一律降级为空/跳过，不炸宿主会话 |
| F11 | 写侧适配 | 条件必选（宿主沙箱禁止写 KB） | 不适用 | 提供宿主侧写入通道（或纠偏提示），使 `agenote add` 在 workspace-write 下可用 |

### 判据补充

- **F2 的两种注入模式**：追加型每轮追加正文，靠 CLI 的三件套防膨胀；重写型每请求
  幂等重写，只需简报 + query 缓存。重写型宿主做召回不是错，但不做也不算缺口——见
  豁免记录。
- **F3 的单一实现**：三件套与状态文件只在 `agenote inject` 里实现，注入器不得
  复制。插件侧出现指纹/累计/状态文件逻辑即为架构回退——那正是 ADR 0005 记录的
  四份语言副本。
- **F5 的防抖**：显式信号触发后 `timing.debounce_ms` 内不再触发；自注入提示必须能被
  识别（`<agenote-hook>` 标记或消息 source），否则会形成反馈环。
- **F6 的「有工作」门槛**：不能拿裸 turn 数判定；只有跑过工具的轮次才算完成了一段
  工作，否则纯问答轮会在 5 分钟后误报。
- **F8 是静默失败的重灾区**：`AGENOTE_AGENT` 只打归因标签、不做写入隔离。漏了不会
  报错，只会让卡片都记到默认 agent 名下。
- **F10 的底线**：注入是纯增益路径，任何异常都只能让本轮少注入，不能让宿主会话失败。

## 当前对照

`✅` 已实现；`—` 未实现（缺口）；`N/A` 宿主能力不支持或组件类型不适用（理由见
[豁免记录](#豁免记录)）。

| 功能 | zcode | hermes | pi | dsh | claude | codex | opencode |
|---|---|---|---|---|---|---|---|
| F1 记忆简报 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F2 每轮召回 | ✅ | ✅ | N/A | ✅ | ✅ | ✅ | N/A |
| F3 状态与缓存 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F4 语义开关 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F5 完成信号 | ✅ | ✅ | ✅ | ✅ | N/A | N/A | N/A |
| F6 空闲兜底 | N/A | N/A | ✅ | ✅ | N/A | N/A | N/A |
| F7 命令三件套 | ✅ | ✅ | ✅ | ✅ | N/A | N/A | N/A |
| F8 归因 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F9 子代理豁免 | N/A | ✅ | ✅ | ✅ | N/A | N/A | N/A |
| F10 故障静默 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| F11 写侧适配 | N/A | N/A | N/A | ✅ | N/A | N/A | N/A |

注入器（claude / codex / opencode）的 `N/A` 是组件类型边界：它们不是插件，没有命令
注册与 turn 生命周期挂点。完整插件的 `N/A` 是宿主能力边界，逐条记在下面。

## 豁免记录

豁免必须写清「为什么不做」与「什么条件下要重开」。新增豁免同样入表。

| 宿主 | 功能 | 理由 | 重开条件 |
|---|---|---|---|
| zcode | F6 空闲兜底 | 无 `agent_end` 挂点；`Stop` hook 的 continuation request 会干扰正常会话结束 | ZCode 提供静默结束事件 |
| zcode | F9 子代理豁免 | `UserPromptSubmit` 只在主会话的用户输入上触发，事件模型本身不会收到子代理内部消息 | 事件模型改变 |
| hermes | F6 空闲兜底 | `pre_llm_call` 只在用户消息进入时触发，没有「会话静置」事件 | 宿主提供 idle/agent_end 钩子 |
| pi | F2 每轮召回 | 重写型注入：每 LLM 调用重写简报块，已覆盖长期记忆；召回由 agent 按需 `agenote search` 补足 | 需要按用户消息做逐轮检索时 |
| opencode | F2 每轮召回 | 同上（`experimental.chat.messages.transform` 重写型） | 同上 |
| claude / codex / opencode | F5 / F6 / F7 / F9 | 注入器边界：只做记忆注入，不承担完成信号、命令入口与 turn 生命周期 | 升级为完整插件（新增触发与命令实现） |
| zcode / hermes / pi / claude / codex / opencode | F11 写侧适配 | 宿主无文件沙箱或沙箱不拦 KB 路径，`agenote` CLI 经 bash 直接可写；条件不成立，功能不适用 | 宿主引入文件围栏挡住 KB_ROOT 时（dsh 即此形态） |

## 新增一个宿主插件时

1. 在 `spec/injection.toml` 增加 `[hosts.<name>]`：`mode`（append / rewrite）、
   `brief` / `recall` 预算、`cli_timeout_ms`；改 `tools/codegen/generate.py` 的
   `TARGETS` 登记预算生成块，跑一次生成。
2. 实现 F1–F10 中适用于该宿主形态的项；**注入状态机不要自己写**——追加型调
   `agenote inject --host <name> --event session|prompt`，重写型调
   `agenote context` 并可在宿主侧加指纹缓存省 spawn。不适用项先写进本文件的
   豁免记录，再标 `N/A`。
3. 在 `packages/agenote/injectors/selftest.sh` 增加离线断言：正常注入、缓存重放、
   门槛/预算生效、CLI 缺失静默、语义开关零字节。
4. 更新本文件的对照矩阵与各插件 README / AGENTS.md 的功能表。
5. 跑校验：

```bash
python3 tools/codegen/generate.py --check && python3 tools/codegen/check.py
bash packages/agenote/injectors/selftest.sh
cd packages/agenote && .venv/bin/python -m pytest -q
for f in packages/dsh-agenote/*.js; do cp "$f" "/tmp/dsh-$(basename "${f%.js}").mjs"; node --check "/tmp/dsh-$(basename "${f%.js}").mjs"; done
python3 -m py_compile packages/agenote-hermes/__init__.py
```

## 相关内容

- 跨宿主的信号 / 预算 / 时序：`spec/injection.toml`
- 注入器架构与安装：`packages/agenote/injectors/README.md`
- 组件职责边界：`docs/adr/0005-monorepo-unify-seven-components.md`
- 编码与验证约定：`CODING_STANDARDS.md` §宿主适配、§验证
