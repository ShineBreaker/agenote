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
| `will-user-message`（入口，3 秒裁决） | 把「会话简报 + 按轮召回」封进 `<!-- agenote-inject:… -->`，以 `rewrite` 挂到本轮用户消息开头 |
| `did-turn-end` | 命中完成信号 → 投递 agenote-review 评估提示；长轮次结束后武装空闲兜底定时器 |
| `did-turn-start` | 取消空闲兜底定时器 |
| 工具 `agenote_search` / `agenote_health` | 只读查询，走随包 Node worker 调 CLI |

`main.js` 是浏览器沙箱里的电子脑：没有 Node、没有文件系统、没有网络直连。所有
CLI 调用都在 `node/worker.cjs` 里执行，每次调用都带 `AGENOTE_AGENT=cindy`，否则
卡片会记到 CLI 的默认 agent 名下。

状态机（指纹缓存、query 去重、单会话累计预算）只有 CLI 一份实现——插件调
`agenote inject`，自己只保留会话级簿记。这是 ADR 0005 的边界，不要在插件里补一份。

## 三个要知道的取舍

1. **注入是追加型，会进你的消息气泡。** Cindy 的入口钩子只能改写当前这条消息，
   改不到历史，所以剥不掉旧块。这也是选它的代价：简报正文会出现在你发出去的那条
   消息里（同时落库），并且随会话轮次累积。去重与累计预算由 CLI 兜底：同 query 同
   指纹不重注，累计触顶后停召回。
   想换成不污染气泡的通道，就把清单里的 `will-user-message` 换成
   `will-compose-context`——代价是每个任务都要在「任务插件」里手动勾选本插件。
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
  （`will-user-message` + `did-turn-end` + `send_to_session` 都在）。

## 开关

| 想关什么 | 怎么做 |
|---|---|
| 全部注入 | `agenote config set injection.enabled false`（或 `AGENOTE_INJECTION_ENABLED=false`） |
| 只关 Cindy 这一路 | `agenote config set injection.hosts.cindy_enabled false`（或 `AGENOTE_INJECTION_HOSTS_CINDY_ENABLED=false`） |
| 关掉整个插件 | Cindy 插件页停用——钩子与工具都不再唤醒 |

语义开关由 CLI 裁决：关掉后插件拿到的是零字节，消息原样放行。

## 权限与风险

- `subscribe.hooks: will-user-message` 是**最重的一条**权限：插件能读取、拦截或
  改写你发出的每条消息。本插件只做「只为空/非空」的判定与拼接，不改写你的原话
  （注入块之后仍是原文）。
- `agent.hostTools: control`：只用于 `send_to_session` 投递收尾提示。
- `node`：随包 Node 工作进程拥有当前系统账号的本机权限，用它跑 `agenote` CLI。

## 实测记录（2026-11，Cindy 0.1.139）

- `agenote context` 单次冷启动约 0.22 s，钩子窗口 3 s，单次超时按 1.2 s 配置，
  一轮最多两次调用（简报 + 召回）。
- `--host cindy` 在 CLI ≥ 0.2.4 生效；旧 CLI（如 0.2.0.4）会以 `invalid choice`
  拒绝，worker 自动退回 `generic` 并记住，不报错。
- 宿主源码按 tag `v0.1.139`（commit `9ffef649`）核对过：`will-user-message`、
  `did-turn-end`、`send_to_session`(control)、`launch: "resident"` 都在。

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
