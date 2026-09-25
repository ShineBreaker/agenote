# dsh-agenote

DeepSeek Harness（DSH）的 [agenote](https://github.com/ShineBreaker/agenote) 集成插件。

把 agenote 知识库接进 DSH 会话：**system prompt 注入健康度摘要**、**任务完成时提示 agent 做经验留痕**，外加三个斜杠命令快捷入口。

本插件是 `agenote-pi`（pi/omp 扩展）在 DSH 上的对等实现，二者共享同一套行为规范（`agenote-skills`）与同一个 `agenote` CLI，**不重复实现任何知识库逻辑**。

## 它做什么

| 触发点             | DSH 事件                | 行为                                                                                      |
| ------------------ | ----------------------- | ----------------------------------------------------------------------------------------- |
| system prompt 拼装 | `systemPrompt.section`  | 注入 `agenote health` 摘要（总数/孤立率/过时率/薄弱类别/记忆统计）——背景知识，不产生 turn |
| 用户消息进入       | `agent/inbox/inserted`  | 记住最近一条**真实用户**发言，用于信号判定                                                |
| 新一轮开始         | `agent/status`(running) | 作废待发的空闲计时器                                                                      |
| 工具执行完毕       | `tools/result`          | 标记本轮「真的干过活」（空闲兜底的门槛依据）                                              |
| turn 收尾          | `agent/turn-stopping`   | 检测完成信号 → 注入 review 提示；并武装空闲兜底                                           |
| 会话销毁           | `agent/disposed`        | 清计时器、释放会话状态                                                                    |

命令：

| 命令                 | 说明                                                            |
| -------------------- | --------------------------------------------------------------- |
| `/agenote-summarize` | 在当前会话触发经验总结 + 留痕（按 `agenote-review` skill 执行） |
| `/agenote-curate`    | 触发 KB 策展（按 `agenote-curator` skill 执行）                 |
| `/agenote-health`    | 直接回显 `agenote health` 报告（纯只读）                        |

前两个命令**不直接跑 CLI**——只把任务提示投进会话，流程编排与写盘决策由 agent 按对应 skill 主导。这保证了「行为规范」只有一份真相源（`agenote-skills`），不会在插件里长出第二份。

## 安装

```bash
# 在本仓库目录下，通过 DSH 的 plugin_manager 安装（推荐）
# 或在会话中让 agent 执行：plugin_manager install_bundle <本目录绝对路径>
```

安装会在当前 profile 的 `package.json` 里以 `link:` 方式登记依赖，并把 `dsh-agenote` 追加进 `dsh.profile.bundles`。

profile 为 `patchReload: live` 时（web profile 默认如此），新 bundle 会**热挂载到已存在的会话**，无需重启即可生效——实测：安装后同一会话的下一轮就出现了 `agent/created` 注入的健康度摘要。

两条注意事项：

- `install_bundle` / `set_bundle` 在**运行中**的进程上可能返回 `application: failed` + `agenote (dsh-agenote): failed to import`。这是该次调用自身的诊断，**不代表 bundle 未被采用**——请以「冷启动日志无失败」与「会话里是否真的看到注入」为准，不要据此改代码。
- **改了源码之后**需要重启：运行中的进程持有旧的模块图，重新加载要冷启动。

### 依赖

- `agenote` CLI 在 `PATH` 中（`which agenote`）。可用环境变量 `AGENOTE_BIN` 覆盖可执行名。
- 可选：`agenote-base` / `agenote-curator` / `agenote-review` skills 已安装（命令注入的提示词会引用它们）。

## 配置

配置从 cordis 行的 `config` 下发（见 `cordis.patch.yml` 注释）。全部字段可选：

```yaml
- id: agenote
  config:
    hooks:
      enabled: true # 总开关
      status: true # system prompt 注入健康度摘要
      completionSignals: true # turn 收尾检测完成信号
      idleFallback: true # 空闲兜底（无人值守场景）
      signals: ["搞定", "done."] # 覆盖内置信号清单
      debounceMs: 300000 # 触发冷却期（durable log 推导）
      idleMs: 300000 # 空闲兜底阈值
    commands:
      enabled: true
```

## 设计要点

几个刻意的取舍，改动前请先读：

- **健康度摘要走 system-prompt section，不走 `agent.followup()`**。`followup` 的语义是"排一个独立后续 turn 并**唤醒 driver**"——`agent/created` 时调用它，等于插件在用户开口前替用户"发言"，driver 被唤醒后模型把摘要当指令处理并回复（表现为"新会话一打开 agent 就自说自话"，已修）。背景知识类注入用 `ctx.systemPrompt.section()`，随每次请求进入 system prompt，不产生 turn。
- **`agent.followup()` 只用于任务提示**（review/curate）。这两个投递点都**没有待执行的 step**；`steer` 只作用于「最近的 step」，会把消息停在 inbox 里等下一次唤醒。`followup` 才等价于 pi 的 `sendUserMessage({deliverAs:'followUp'})`。
- **subagent 豁免**（`session.header.origin === 'subagent'`）。DSH 的 subagent 是同进程独立 agent，同一个插件实例会收到它们的事件：worker 的内部对话不该投 review 提示（污染 handoff），也不该各自武装 idle 计时器（大量委派时表现为 idle 兜底"反复触发"）。
- **防抖与「已触发」从 durable log 推导**，不存内存标志。内存标志在 `agent/created`（含 resume/clear/compact）时被重置——web 刷新即 resume，同一会话干完活又触发一次。改扫会话日志：`source.plugin === 'agenote-hooks'` 的 `user/message` 事件即本插件既往注入，resume/重启后答案不变。
- **每会话状态用 `Map<sessionId, state>`**，不用模块级标量。一个进程里可并存多个会话（web 多标签、subagent），模块级状态会串台——这正是 pi 版历史上修过的 bug。
- **自注入反馈环防护**。注入的提示词自身含「完成」等信号词；`agent/inbox/inserted` 只认 `source.kind === 'user'` 的消息（本插件注入的 source.kind 是 `'plugin'`），回路在来源层面断开。
- **不 import `@deepseek-ai/*`**。本包经 `link:` 部署时模块 realpath 落在源码目录，Node 的 `node_modules` 父级检索够不到 `$DSH_HOME/profiles/node_modules` 共享 fallback。需要上游纯函数（`createUserMessage`）时在 `lib.js` 里复刻并注明出处；上游改语义需手动同步。
- **空闲兜底以「跑过工具的轮次」为门槛**，不用裸 turn 数。turn 数无法区分「完成了一段工作」与「回答了一个问题」——纯对话轮同样推进 turn，会导致兜底在纯聊天上误报（实测发生过）。判据是 `workedThisTurn`（仅在该轮 `tools/result` 触发过时才成立）。
- **不重复实现行为规范**。信号清单、写入流程、卡片格式全部归 `agenote-skills`；插件只做「事件触发 + 命令快捷入口」。
- **归因**。所有 CLI 调用都带 `AGENOTE_AGENT=dsh`（`lib.js:runKb`）。该变量只给卡片打归因标签，不做写入隔离，但不带的话归因会错误落到默认 agent（`omp`）。

## 与 agenote-pi 的差异

|               | agenote-pi（pi/omp）                      | dsh-agenote                                                            |
| ------------- | ----------------------------------------- | ---------------------------------------------------------------------- |
| 形态          | 单文件 TS，运行时自动扫描                 | Cordis bundle（`package.json` + YAML patch），经 `plugin_manager` 安装 |
| 健康度摘要    | pi-ui 欢迎框显示（不注入对话）            | `systemPrompt.section`（进入 system prompt，不产生 turn）              |
| turn 收尾     | `agent_end`                               | `agent/turn-stopping`                                                  |
| 任务提示投递  | `sendUserMessage({deliverAs:'followUp'})` | `agent.followup(createUserMessage(...))`                               |
| subagent 守卫 | `isSubagentProcess()` 嗅探子进程 argv/env | `session.header.origin === 'subagent'`（持久化权威字段）               |
| 防抖依据      | 模块级内存变量                            | durable session log 推导（跨 resume/重启稳定）                         |
| 归因          | `AGENOTE_AGENT=pi`                        | `AGENOTE_AGENT=dsh`                                                    |

## 开发

无构建步骤，纯 ESM。改完源码后：

```bash
node --check index.js hooks.js commands.js lib.js   # 语法检查
dsh --profile <profile> --no-open --port 3099       # 新进程冷启动，确认无激活失败
```

`plugin_manager set_bundle` 在**运行中**的进程上可能报 `failed to import`——那是该次调用自身的诊断，旧模块图仍能服务已挂载的行。判断依据看「冷启动日志无失败」+「会话里是否真的看到注入」，不要据此改代码。

## 许可

MIT。见 `LICENSE`。
