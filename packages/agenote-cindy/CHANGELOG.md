# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.4] - 2026-11-25

收紧 Cindy 适配器的异步边界与宿主版本声明，并限制诊断与只读工具的失败半径。

---

### Fixed

- 旧轮次的异步投递回执不会封存新轮次的触发状态或清理新轮次定时器。
- `tool-execution` 只为已经观察到的活动会话计数，不为陌生会话创建簿记；CLI 的工具与 context 输出超过单行协议预算时都会做 UTF-8 安全截断。
- 旧 CLI 降级仅替换 `--host` 的值，并且只读工具保持 `search` / `health` 白名单。

### Changed

- `tool-execution` 依赖 Cindy `v0.1.143`（commit `55d3b6ac45ea68c489122ed891148699fb55e477`）；manifest 的最低版本同步上调。
- 诊断 `send_test` 改为必须显式提供 `target_session_id`，避免把测试消息误投到最近的其它会话；回执按结构化错误判断。

## [0.3.3] - 2026-11-22

收紧 Cindy 适配器的失败边界：注入去重与降级重新交给 CLI，投递失败会重试而不是永久吞掉后续兜底，并补齐诊断、文档和能力矩阵的真实状态。

---

### Fixed

- 移除 worker 自己维护的注入内容哈希缓存，避免与 `agenote inject` 的唯一状态机分叉。
- 旧 CLI 的 `--host cindy` 降级只替换 host 参数，不再改写恰好等于 `cindy` 的 prompt 或 session id。
- 收尾提示只有拿到成功回执才封存触发状态；失败会保留空闲兜底重试机会。
- 清理已结束轮次的用户消息快照，避免无新用户消息的后续轮次复用旧完成信号。
- 订阅 `tool-execution` 旁听主题，短工具轮不再依赖把工具消息误当普通消息；完成信号过滤否定句、条件句和疑问句，英文信号增加单词边界。
- 完成投递在异步回执期间遇到新轮次时不再重新武装旧定时器；事件丢失时清理不可恢复的本轮证据，避免错误触发。

### Changed

- 能观察到 `tool_use` / `tool_result` 消息时，短工具轮也可武装空闲兜底；README 与能力矩阵同步这一行为。
- Cindy 的 slash-command 三件套明确标为外部 skill 能力，不再伪装成 ghost manifest 已注册的功能。

## [0.3.2] - 2026-11-21

修掉「完成信号与空闲兜底从来没生效过」：`send_to_session` 属于宿主工具目录的 **`handoff`** 组，插件却申报了 `control`。宿主对未申报组的调用只回 `{"ok":false,"errorCode":"PERMISSION_DENIED"}`，本插件又把投递失败吞在 `catch` 里，于是「静默失败」贯穿了 0.1.0 到 0.3.1 —— 投递次数 0，卡片库里一条提示都没有。

---

### Fixed

- **`agent.hostTools` 改为 `["handoff"]`**：收尾提示终于能真正投递。
- **新增 `agenote_diag` 工具**：吐事件计数、最近一轮 `durationMs`/`endReason`、上一次投递的原始回执或错误、随包 CLI 是否通，并可 `send_test` 当场试投一条。排查「为什么没收到复盘提示」不必再靠猜。
- **投递失败不再无声**：错误码写进 `diag.lastNudge`，可用 `agenote_diag` 查。

## [0.3.0] - 2026-11-21

注入改走宿主的资料通道。0.2.0 把注入块包成 HTML 注释，前提是气泡按 Markdown 渲染（`skipHtml` 丢弃注释）——前提是错的：Cindy 的用户气泡是 `whitespace-pre-wrap` 纯文本（`UserMessage.tsx`），注释会原样显示。宿主里唯一既不显示、也不落库的注入面是 `will-compose-context`：它只往 wire 消息前置材料，`appendRuntimePluginContext` 的注释与实现都写明「不影响落库/显示内容」。

---

### Changed

- **注入通道换成 `will-compose-context`**：返回 `action: "context"` 与 `blocks: [{id: "agenote-brief", text}]`，不再改写用户消息。裁决窗口从 3 秒变成 2 秒，总预算压到 1.6 秒。
- **完成信号改由 `did-message-created` 旁听**：入口钩子不再声明，`message` topic 是纯旁听、不依赖任务选择，所以触发链路仍然全局生效；只有简报与召回需要在任务的「任务插件」里选中本插件。
- **权限面收窄**：不再申请「读取并改写你发出的消息」，改为「往本轮附资料」+ 旁听已落库消息正文。

## [0.2.0] - 2026-11-21

注入块不再出现在消息气泡里。宿主用 `skipHtml` 渲染（桌面端 `MarkdownRenderer` 显式传 `skipHtml`；手机端 `messageMarkdown` 对跨块 HTML 注释单独压制），所以把简报整块包成一段 HTML 注释，渲染时被丢弃，发给模型的消息正文里照常存在。

---

### Changed

- **注入块改为 HTML 注释形态**：`<!-- agenote-inject:start … agenote-inject:end -->` 一段到底。用户气泡里只剩自己写的那句话，模型侧内容不变。
- **正文 `-->` 消毒**：简报里出现 `-->` 会把注释提前闭合、后半段漏进气泡，写入前统一替换成 `-- >`。
- **完成信号判定用剥掉注入块后的原文**：用户把带块的消息编辑重发时，不会把上一次的简报当成「本轮用户说的话」。

## [0.1.0] - 2026-11-21

首个版本：把 agenote 接进 Cindy。宿主用 `--no-extensions` 启动内置 Pi，omp 扩展（`agenote-pi`）在这条路上不可用，故改用 ghost 插件。

---

### Added

- **`will-user-message` 注入**：会话简报 + 按轮召回封进 `<!-- agenote-inject:… -->`，以 `rewrite` 挂到本轮用户消息开头。注入状态机（指纹缓存、query 去重、单会话累计预算）全部由 `agenote inject` 承担，插件只搬运。
- **完成信号与空闲兜底**：`did-turn-end` 命中 `spec/injection.toml` 的显式完成信号后投递 agenote-review 提示；长轮次结束后武装空闲兜底，两者都带 `<agenote-hook>` 标记断开自触发反馈环。投递走宿主 `send_to_session`（`control` 组）。
- **只读工具**：`agenote_search`、`agenote_health`，经随包 Node worker 调 CLI，子命令白名单，不带 shell。
- **旧 CLI 降级**：`--host cindy` 被拒时自动退回 `generic`；没有 `inject` 子命令时只注会话简报，工具照常可用。

### Notes

- 追加型注入：入口钩子改不到历史，简报正文会出现在用户消息气泡里并随轮次累积，去重与累计预算由 CLI 兜底。改 `will-compose-context` 可避免污染气泡，但需要逐任务勾选插件。
