# Changelog

本项目的所有显著变更记录在此。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本管理遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.3.0] - 2026-11-21

注入改走宿主的资料通道。0.2.0 把注入块包成 HTML 注释，前提是气泡按 Markdown 渲染
（`skipHtml` 丢弃注释）——前提是错的：Cindy 的用户气泡是 `whitespace-pre-wrap`
纯文本（`UserMessage.tsx`），注释会原样显示。宿主里唯一既不显示、也不落库的注入面
是 `will-compose-context`：它只往 wire 消息前置材料，`appendRuntimePluginContext`
的注释与实现都写明「不影响落库/显示内容」。

---

### Changed

- **注入通道换成 `will-compose-context`**：返回 `action: "context"` 与
  `blocks: [{id: "agenote-brief", text}]`，不再改写用户消息。裁决窗口从 3 秒变成
  2 秒，总预算压到 1.6 秒。
- **完成信号改由 `did-message-created` 旁听**：入口钩子不再声明，`message` topic
  是纯旁听、不依赖任务选择，所以触发链路仍然全局生效；只有简报与召回需要在任务的
  「任务插件」里选中本插件。
- **权限面收窄**：不再申请「读取并改写你发出的消息」，改为「往本轮附资料」+ 旁听
  已落库消息正文。

## [0.2.0] - 2026-11-21

注入块不再出现在消息气泡里。宿主用 `skipHtml` 渲染（桌面端 `MarkdownRenderer`
显式传 `skipHtml`；手机端 `messageMarkdown` 对跨块 HTML 注释单独压制），所以把
简报整块包成一段 HTML 注释，渲染时被丢弃，发给模型的消息正文里照常存在。

---

### Changed

- **注入块改为 HTML 注释形态**：`<!-- agenote-inject:start … agenote-inject:end -->`
  一段到底。用户气泡里只剩自己写的那句话，模型侧内容不变。
- **正文 `-->` 消毒**：简报里出现 `-->` 会把注释提前闭合、后半段漏进气泡，写入前
  统一替换成 `-- >`。
- **完成信号判定用剥掉注入块后的原文**：用户把带块的消息编辑重发时，不会把上一次
  的简报当成「本轮用户说的话」。

## [0.1.0] - 2026-11-21

首个版本：把 agenote 接进 Cindy。宿主用 `--no-extensions` 启动内置 Pi，omp 扩展
（`agenote-pi`）在这条路上不可用，故改用 ghost 插件。

---

### Added

- **`will-user-message` 注入**：会话简报 + 按轮召回封进 `<!-- agenote-inject:… -->`，
  以 `rewrite` 挂到本轮用户消息开头。注入状态机（指纹缓存、query 去重、单会话累计
  预算）全部由 `agenote inject` 承担，插件只搬运。
- **完成信号与空闲兜底**：`did-turn-end` 命中 `spec/injection.toml` 的显式完成信号后
  投递 agenote-review 提示；长轮次结束后武装空闲兜底，两者都带 `<agenote-hook>`
  标记断开自触发反馈环。投递走宿主 `send_to_session`（`control` 组）。
- **只读工具**：`agenote_search`、`agenote_health`，经随包 Node worker 调 CLI，
  子命令白名单，不带 shell。
- **旧 CLI 降级**：`--host cindy` 被拒时自动退回 `generic`；没有 `inject` 子命令时
  只注会话简报，工具照常可用。

### Notes

- 追加型注入：入口钩子改不到历史，简报正文会出现在用户消息气泡里并随轮次累积，
  去重与累计预算由 CLI 兜底。改 `will-compose-context` 可避免污染气泡，但需要
  逐任务勾选插件。
