# AGENTS.md — dsh-agenote

DeepSeek Harness（DSH）的 agenote 集成插件：Cordis bundle，纯 ESM、无构建步骤。职责仅限事件触发与命令快捷入口；行为规范归 `agenote-skills`，知识库操作归 `agenote` CLI，二者不在本仓库重复实现。

动事件投递或信号检测逻辑前，先读 `README.md` 的「设计要点」——每条规则的完整取舍分析都在那里。

## 改码规则

- CLI 调用一律走 `lib.js` 的 `runKb`：数组传参（无 shell 注入面）、`AGENOTE_AGENT=dsh` 归因、统一超时与错误降级，全部收敛在它里面。
- 投递二分：背景知识（健康度摘要）走 `ctx.systemPrompt.section()`——随请求进 system prompt，不产生 turn；任务提示（review/curate）走 `agent.followup()`——排独立 turn 并唤醒 driver。
- subagent 一律豁免（判据 `session.header.origin === 'subagent'`），其事件不进信号检测、不武装 idle 计时器。
- 状态二分：瞬态（timer、最近用户文本）放 per-session `Map`；跨 resume/重启必须存活的（防抖、「已触发」）从 durable log 推导（`deliveredByUs()`）。模块级可变状态两头都不沾——多会话串台、resume 即重置——不许存在。
- 信号清单的单一真相源在 `agenote-skills` 的 `agenote-review/references/triggers.md`；改内置 `signals` 必须跨仓同步。
- 唯一硬禁令：不 import `@deepseek-ai/*`。`link:` 部署下模块 realpath 落在本仓库源码目录，Node 解析不到 `$DSH_HOME/profiles/node_modules` 共享 fallback；需要上游纯函数就在 `lib.js` 复刻并注明出处。
- 配置默认值与合法范围的单一真相源是 `Config` schema：`index.js` 组合、`hooks.js`/`commands.js` 各声明半边字段，`lib.js` 的 `schema.*` 是 Standard Schema v1 手搓子集（复刻 schemastery 语义，因 link: 部署不能 import）。`apply()` 收到的就是已校验、已填默认值的 config——禁止在 apply 里另写默认值或静默降级；新增可调项必须加 schema 字段。
- 源码风格：2 空格缩进、中文注释、内置模块带 `node:` 前缀。

## 验证

无自动化测试。改完源码按序执行，前一步通过才进下一步：

1. 语法：`node --check index.js hooks.js commands.js lib.js`——四个文件全部无输出退出。
2. 配置 schema：`node --input-type=module -e "import('./index.js').then(m=>{const v=m.Config['~standard'].validate;console.log(JSON.stringify(v(undefined)));console.log(JSON.stringify(v({hooks:{debounceMs:-1}}).issues??null))})"`——第一行是全默认值配置；第二行打出 issues 数组（path 指向 `hooks.debounceMs`），把 `-1` 换成合法值则第二行为 `null`。
3. 逻辑（需 `agenote` 在 PATH）：`node --input-type=module -e "import('./lib.js').then(m=>console.log(m.healthSummary(m.runKb)))"`——打印出健康度摘要而非异常。
4. 激活：`dsh --profile <profile> --no-open --port 3099` 冷启动——日志无 `failed to import`。

改了源码就必须冷启动，运行中进程持有旧模块图。运行中进程上的 `plugin_manager set_bundle` 报 `failed to import` 是该次调用自身的诊断，不代表 bundle 未被采用——能否采用只看第 4 步的冷启动日志与会话里是否真的出现注入，不要据此改代码。
