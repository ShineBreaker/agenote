# agenote → opencode 注入插件（recipe 级模板）

opencode 无自带记忆子系统，无需禁写前置。注入走 plugin 的
`experimental.chat.messages.transform`（**重写型**：每次 LLM 请求前改写全量
messages）——每请求幂等（先剥旧 agenote 块再注缓存块），compact 后自动
恢复，**不需要**追加型三件套（无累积风险），只需指纹缓存零 spawn 重放。

## 安装

```bash
cp injectors/opencode/agenote-context.ts ~/.config/opencode/plugin/
# 或项目级：cp 到 <project>/.opencode/plugin/（需项目信任）
```

免注册自动加载（plugin 目录任意 `*.ts`/`*.js`）。前置：agenote CLI 含
`context` 子命令。

## 预算与缓存

| 项 | 值 | 说明 |
|---|---|---|
| 单次预算 | 8000 字符（简报） | `AGENOTE_INJECTION_BRIEF_BUDGET` 覆盖 |
| 缓存 | MEMORY.org+memories/ 的 mtime+size 指纹 | 进程内缓存，指纹未变零 spawn |
| 幂等 | 剥旧块（正则到 `<!-- /agenote-context -->`）→ 注新块 | 注入点固定在末条 user 消息文本尾 |
| 三件套 | 不适用 | 重写型无累积；recall 需求可按同模式自行扩展 |

## ⚠️ experimental API 漂移风险（设计 R2）

模板对齐 `@opencode-ai/plugin` **1.18.18** 的
`experimental.chat.messages.transform` 签名（`({}, output: { messages: {
info: Message; parts: Part[] }[] }) => Promise<void>`）。experimental 前缀
API 无稳定性承诺，升级 opencode 后失效表现为「无注入」，不影响会话；
失效时对照新版 d.ts 迁移或等待升成品级。

## 验收

```bash
# 1. 类型/语法自检（可选，本仓库无 TS 构建链）：
#    对照 ~/.config/opencode/node_modules/@opencode-ai/plugin/dist/index.d.ts:259
#    的签名人工核对（selftest 做结构断言）。

# 2. 宿主内验收（调研已验证的观测面）：
tail -f ~/.local/share/opencode/log/opencode.log   # 插件加载与报错
# 会话内提问记忆相关问题，验证注入块在场；`/compact` 后继续提问验证自动恢复
```

## 待实装验证项

- experimental API 在你本版 opencode 的实际行为（模板基于 SDK 1.18.18）。
- user 消息 parts 的 `text` 字段形态（模板只注纯文本尾块，非纯文本消息跳过）。
