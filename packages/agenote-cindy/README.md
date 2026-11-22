# agenote-cindy：Cindy 的 agenote 适配插件

把 agenote 跨 agent 知识库接进 Cindy：每轮把记忆简报与按需召回挂到你的消息上，
任务收尾时提醒 agent 做经验留痕；另有两个只读工具供 agent 主动查库。

## 为什么不是 Pi 扩展

Cindy 用 `--no-extensions` 启动内置 Pi（实参见进程列表），omp 那套
`agenote-pi/index.ts` 在这条路上不会被加载。所以这里是一个 ghost 插件
（`.cindy`），能力来自 Cindy 自己的插件协议，而不是 Pi 的扩展点。

## 它怎么工作

| 钩子 / 事件 | 行为 |
|---|---|
| `will-compose-context`（资料节点，2 秒裁决） | 把「会话简报 + 按轮召回」作为宿主的**资料**附到本轮消息上：只改发给模型的 wire 消息，不动显示与落库的原话 |
| `did-message-created` | 旁听用户消息，记下最后一条供完成信号判定（纯旁听，全局生效） |
| `did-turn-end` | 命中完成信号 → 投递 agenote-review 评估提示；长轮次结束后武装空闲兜底定时器 |
| `did-turn-start` | 取消空闲兜底定时器 |
| 工具 `agenote_search` / `agenote_health` | 只读查询，走随包 Node worker 调 CLI |

`main.js` 是浏览器沙箱里的电子脑：没有 Node、没有文件系统、没有网络直连。所有
CLI 调用都在 `node/worker.cjs` 里执行，每次调用都带 `AGENOTE_AGENT=cindy`，否则
卡片会记到 CLI 的默认 agent 名下。

状态机（指纹缓存、query 去重、单会话累计预算）只有 CLI 一份实现——插件调
`agenote inject`，自己只保留会话级簿记。这是 ADR 0005 的边界，不要在插件里补一份。

## 三个要知道的取舍

1. **资料通道要手动开，但保证不碰你的原话。** `will-compose-context` 是宿主里唯一
   既不显示、也不落库的注入面：它把内容附在 wire 消息上（`appendRuntimePluginContext`
   的注释写明「不影响落库/显示内容」）。代价是这个节点**只问选中了本插件的任务**，
   所以要在任务的「任务插件 → 资料提供者」里勾一次。
   为什么不用入口钩子 `will-user-message`：Cindy 的用户气泡是 `whitespace-pre-wrap`
   纯文本（`UserMessage.tsx`），不是 Markdown 渲染，任何改写都会原样出现在气泡里；
   塞进 HTML 注释也一样会被当纯文本显示出来。
   不勾选时插件仍然工作：完成信号与空闲兜底（`message` / `turn` 旁听）不依赖这个选择，
   只有简报与召回不注入。
2. **空闲兜底用轮次时长近似「有工作」。** Cindy 的订阅面没有工具调用事件，
   拿不到「本轮跑过工具」的事实，插件只在 `did-turn-end` 的 `durationMs` 超过
   45 秒时才武装定时器。短问答轮不会误报，但也可能漏掉真正的短任务。
3. **斜杠命令不随插件打包。** `/agenote-review`、`/agenote-curator` 等来自
   `agenote-skills`（装在 agent 技能目录里）。插件重复打包一份会让行为规范出现
   第二处真相源，所以这里是工具 + 注入，规范仍在 skill 里。

## 装

```bash
# 1. 打包（Cindy 宿主工具，需要在本机 Cindy 里跑）
#    ghost_forge_pack(dir: packages/agenote-cindy)
# 2. 安装
#    ghost_forge_install(dir: packages/agenote-cindy)
```

依赖：

- **agenote CLI** 在 `PATH` 里（或设 `AGENOTE_BIN` 指向它）。带 `inject` 子命令的
  版本（≥ 0.2.4）才有按轮召回与三件套；旧 CLI 自动降级为「只注会话简报」，
  工具仍可用。
- **Cindy ≥ 0.1.139**：`ghost.json` 的 `minCindyVersion` 就是按这个版本写的
  （`will-compose-context` + `did-message-created` / `did-turn-end` +
  `send_to_session` 都在）。

## 开关

| 想关什么 | 怎么做 |
|---|---|
| 全部注入 | `agenote config set injection.enabled false`（或 `AGENOTE_INJECTION_ENABLED=false`） |
| 只关 Cindy 这一路 | `agenote config set injection.hosts.cindy_enabled false`（或 `AGENOTE_INJECTION_HOSTS_CINDY_ENABLED=false`） |
| 关掉整个插件 | Cindy 插件页停用——钩子与工具都不再唤醒 |

语义开关由 CLI 裁决：关掉后插件拿到的是零字节，消息原样放行。

## 权限与风险

- `subscribe.hooks: will-compose-context`：本插件只往本轮附资料，不读取、不改写你的
  消息内容。装它不会替你批准任何事，只在任务里选中它时被询问。
- `subscribe.topics: message`：旁听已落库的消息正文（用来识别完成信号）。这是纯旁听，
  改不了流程，但确实能看到消息内容。
- `agent.hostTools: handoff`：只用于 `send_to_session` 投递收尾提示。注意它属
  **`handoff` 组**而不是 `control`——申报错组时宿主只回一个 `PERMISSION_DENIED`，
  没有别的提示（0.3.1 及以前就是这样哑掉的）。
- `node`：随包 Node 工作进程拥有当前系统账号的本机权限，用它跑 `agenote` CLI。

## 实测记录（2026-11，Cindy 0.1.139）

- `agenote context` 单次冷启动约 0.22 s；资料节点窗口 2 s，单次超时按 1.2 s 配置，
  一轮最多两次调用（简报 + 召回）。
- `--host cindy` 在 CLI ≥ 0.2.4 生效；旧 CLI（如 0.2.0.4）会以 `invalid choice`
  拒绝，worker 自动退回 `generic` 并记住，不报错。
- 宿主源码按 tag `v0.1.139`（commit `9ffef649`）核对过：`will-compose-context`、
  `did-message-created`、`did-turn-end`、`send_to_session`(control)、
  `launch: "resident"` 都在；`appendRuntimePluginContext` 的注释与实现都表明资料
  只改 wire 消息，不动落库与显示。

## 自检

```bash
node packages/agenote-cindy/selftest.mjs   # 桩替身跑注入/触发/工具分支
node --check packages/agenote-cindy/main.js
node --check packages/agenote-cindy/node/worker.cjs
```

`selftest.mjs` 不进宿主，用 `vm` 给 `cindy` 打桩，覆盖首条注入、无内容放行、
自注入与斜杠命令放行、完成信号与防抖、空闲兜底的武装与取消、工具参数与失败降级。

## 相关

- 功能基线与豁免：`docs/PLUGIN_CAPABILITIES.md`
- 跨宿主信号 / 预算 / 时序：`spec/injection.toml`
- 行为规范（写入流程、卡片格式、策展策略）：`packages/agenote-skills`
